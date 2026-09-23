"""Роутер флоу B «проверка автомобиля» (ТЗ, 7.1 и 7.3).

Здесь три транспорта поверх одного и того же состояния: запуск, поллинг и SSE.
Роутер не считает бизнес-логику — он приводит состояние оркестратора к контракту
на проводе и отвечает за две вещи, которые нельзя делегировать оркестратору:
гарантию «в каждом кадре полный список источников» и идемпотентность запуска.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.responses import StreamingResponse

from avtoklik.api.deps import (
    CheckOrchestrator,
    IdempotencyStore,
    SseEvent,
    VehicleRepository,
    as_payload,
    get_idempotency_store,
    get_orchestrator,
    get_vehicle_repository,
)
from avtoklik.api.schemas import (
    CheckCreatedResponse,
    CheckCreateRequest,
    CheckStatusResponse,
    CheckVerdict,
    ErrorFrame,
    ErrorResponse,
    ProgressFrame,
    SourceStatus,
    Subject,
    progress_from_sources,
)

__all__ = ["HEARTBEAT_INTERVAL_SEC", "router"]

router = APIRouter(prefix="/checks", tags=["checks"])

# ТЗ, 7.3: heartbeat-комментарий раз в 15 секунд. Без него прокси и CDN рвут
# «молчащее» соединение — а проверка молчит законно: ГИБДД может думать 20 секунд,
# и всё это время новых событий нет. Комментарий SSE (строка с ':') клиентом
# игнорируется, но для прокси это трафик, и соединение живёт.
HEARTBEAT_INTERVAL_SEC = 15.0
_HEARTBEAT_FRAME = ": keepalive\n\n"

OrchestratorDep = Annotated[CheckOrchestrator, Depends(get_orchestrator)]
IdempotencyDep = Annotated[IdempotencyStore, Depends(get_idempotency_store)]
VehiclesDep = Annotated[VehicleRepository | None, Depends(get_vehicle_repository)]


def _fingerprint(subject: Subject) -> str:
    """Отпечаток тела запроса — по нему отличаем повтор от конфликта ключа."""
    return hashlib.sha256(subject.key.encode("utf-8")).hexdigest()


def _stream_url(check_id: str) -> str:
    return f"/api/v1/checks/{check_id}/stream"


def _status_url(check_id: str) -> str:
    return f"/api/v1/checks/{check_id}"


def _verdict_url(check_id: str) -> str:
    return f"/api/v1/checks/{check_id}/verdict"


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CheckCreatedResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    },
    summary="Запустить проверку по госномеру или VIN",
)
async def create_check(
    payload: CheckCreateRequest,
    response: Response,
    orchestrator: OrchestratorDep,
    idempotency: IdempotencyDep,
    vehicles: VehiclesDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CheckCreatedResponse:
    """Поставить проверку в очередь.

    Отвечаем 202, а не 201: проверка — длинная операция, к моменту ответа
    ресурса «результат» ещё не существует, есть только обещание и `stream_url`.

    Идемпотентность (ТЗ, 7: все изменяющие запросы принимают `Idempotency-Key`).
    Сценарий не гипотетический: пользователь в метро жмёт «Проверить», ответ
    не доехал, он жмёт ещё раз. Без ключа он получил бы две проверки, два
    похода к шести источникам с их лимитами и, если дело дойдёт до покупки,
    два списания. Поэтому: тот же ключ и то же тело — та же проверка; тот же
    ключ и другое тело — 409, потому что это ошибка клиента, а не повтор.
    """
    subject = payload.subject
    fingerprint = _fingerprint(subject)

    if idempotency_key:
        record = await idempotency.lookup(idempotency_key)
        if record is not None:
            if record.fingerprint != fingerprint:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "idempotency_key_reused",
                        "detail": "Этот Idempotency-Key уже использован с другим запросом",
                    },
                )
            existing = await orchestrator.get_check(record.check_id)
            response.headers["Idempotency-Replayed"] = "true"
            return _created_response(
                record.check_id,
                eta_sec=0 if existing is not None else 40,
                check_status=existing.status if existing is not None else None,
            )

    if vehicles is not None:
        vehicle = await vehicles.find_vehicle(subject)
        if vehicle is None:
            # CC5: не нашли по номеру — предлагаем другие способы, а не «ошибка».
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "vehicle_not_found",
                    "detail": "Не нашли автомобиль по этим данным",
                    "alternatives": ["vin", "photo"],
                },
            )

    started = await orchestrator.start_check(subject)

    if idempotency_key:
        # remember атомарен: если параллельный запрос успел первым, получим его
        # запись — и отдадим клиенту ту проверку, а не только что созданную.
        record = await idempotency.remember(idempotency_key, fingerprint, started.check_id)
        if record.check_id != started.check_id:
            response.headers["Idempotency-Replayed"] = "true"
            return _created_response(record.check_id, eta_sec=started.eta_sec)

    return _created_response(started.check_id, eta_sec=started.eta_sec, check_status=started.status)


def _created_response(
    check_id: str, *, eta_sec: int, check_status: Any = None
) -> CheckCreatedResponse:
    """Собрать ответ на запуск: клиент получает готовые ссылки, а не шаблон пути."""
    kwargs: dict[str, Any] = {
        "check_id": check_id,
        "stream_url": _stream_url(check_id),
        "status_url": _status_url(check_id),
        "eta_sec": eta_sec,
    }
    if check_status is not None:
        kwargs["status"] = check_status
    return CheckCreatedResponse(**kwargs)


@router.get(
    "/{check_id}",
    response_model=CheckStatusResponse,
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}},
    summary="Состояние или результат проверки",
)
async def get_check(check_id: str, orchestrator: OrchestratorDep) -> CheckStatusResponse:
    """Резервный поллинг вместо SSE (ТЗ, 5.1).

    Ответ по форме совпадает с кадром `progress`: это то же состояние, просто
    доставленное иначе. Клиент, у которого прокси не пропустил `text/event-stream`,
    не должен переписывать разбор ответа.
    """
    state = await orchestrator.get_check(check_id)
    if state is None:
        raise _not_found(check_id)
    # Прогресс считает сервер: пересчитываем по полному списку источников,
    # чтобы поллинг и SSE никогда не показали пользователю разные числа.
    if state.progress is None:
        state.progress = progress_from_sources(state.sources)
    return state


@router.get(
    "/{check_id}/verdict",
    response_model=CheckVerdict,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    },
    summary="Вердикт проверки (экран B3)",
)
async def get_verdict(check_id: str, orchestrator: OrchestratorDep) -> CheckVerdict:
    """Вердикт со скорингом, блоками проверок и признаком неполноты источников."""
    state = await orchestrator.get_check(check_id)
    if state is None:
        raise _not_found(check_id)
    verdict = await orchestrator.get_verdict(check_id)
    if verdict is None:
        # Проверка есть, вердикта ещё нет — это не 404: ресурс существует,
        # просто состояние не то. Клиенту надо дождаться события done.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "verdict_not_ready",
                "detail": "Проверка ещё идёт",
                "hint": _stream_url(check_id),
            },
        )
    return verdict


@router.delete(
    "/{check_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}},
    summary="Отменить проверку",
)
async def cancel_check(check_id: str, orchestrator: OrchestratorDep) -> Response:
    """«Отменить» на экране B2 — обычный DELETE, поток для этого не нужен."""
    if not await orchestrator.cancel_check(check_id):
        raise _not_found(check_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{check_id}/stream",
    responses={
        status.HTTP_200_OK: {"content": {"text/event-stream": {}}},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    },
    summary="SSE-поток прогресса проверки",
)
async def stream_check(
    check_id: str,
    orchestrator: OrchestratorDep,
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Поток событий проверки (ТЗ, 7.3).

    Существование проверки проверяем до начала стрима: отдав 200 и уже потом
    обнаружив, что id неизвестен, мы не сможем поменять код ответа — заголовки
    уже ушли. 404 должен прилететь до первого байта тела.
    """
    if await orchestrator.get_check(check_id) is None:
        raise _not_found(check_id)

    body = _sse_body(orchestrator.stream(check_id, last_event_id), check_id)
    return StreamingResponse(
        body,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx по умолчанию буферизует ответ и копит кадры — для потока
            # прогресса это означает «экран B2 замер, потом всё разом».
            "X-Accel-Buffering": "no",
        },
    )


class _SourceLedger:
    """Накопитель полного списка источников для кадров прогресса.

    ТЗ, 7.3: **в каждом кадре — полный список источников, а не дельта.** Дельты
    сэкономили бы байты и стоили бы склейки состояния на клиенте: после
    реконнекта клиенту пришлось бы знать, какие кадры он пропустил, и мержить
    их в правильном порядке — на трёх платформах, тремя разными способами.
    Поэтому полноту кадра гарантирует сервер, на границе API: даже если
    оркестратор пришлёт дельту (а в псевдокоде 5.1 снапшот строится заново
    на каждое событие, но реализация может измениться), наружу уйдёт весь набор.
    """

    def __init__(self) -> None:
        self._sources: dict[str, SourceStatus] = {}

    def merge(self, incoming: list[SourceStatus]) -> list[SourceStatus]:
        """Влить кадр в накопленное состояние и вернуть полный список."""
        for source in incoming:
            known = self._sources.get(source.code)
            if known is None:
                self._sources[source.code] = source
                continue
            # exclude_unset: в дельте приходят только изменившиеся поля, а
            # title и order пришли в первом кадре и меняться не должны.
            update = source.model_dump(exclude_unset=True)
            update.pop("code", None)
            self._sources[source.code] = known.model_copy(update=update)
        return sorted(
            self._sources.values(),
            key=lambda item: (item.order if item.order is not None else 10**6, item.code),
        )


def _render(event: SseEvent) -> str:
    """Сериализовать событие в формат SSE.

    JSON пишем в одну строку: многострочный `data` в SSE пришлось бы разбивать
    на несколько `data:` — формат это позволяет, но лишний повод для ошибки
    разбора на клиенте. `ensure_ascii=False` — чтобы «ГИБДД · регистрация, ДТП»
    читалось в логах и в `curl` как текст, а не как \\u0413.
    """
    payload = json.dumps(as_payload(event.data), ensure_ascii=False, separators=(",", ":"))
    lines = [f"event: {event.event}"]
    if event.id is not None:
        lines.append(f"id: {event.id}")
    for chunk in payload.split("\n"):
        lines.append(f"data: {chunk}")
    return "\n".join(lines) + "\n\n"


async def _sse_body(events: AsyncIterator[SseEvent], check_id: str) -> AsyncIterator[str]:
    """Превратить события оркестратора в тело `text/event-stream`.

    Источник событий читается отдельной задачей в очередь, а не напрямую с
    `asyncio.wait_for`: таймаут на `__anext__()` асинхронного генератора бросил
    бы в него CancelledError и закрыл поток — heartbeat убивал бы соединение,
    которое должен был спасти.
    """
    queue: asyncio.Queue[SseEvent | None] = asyncio.Queue()

    async def pump() -> None:
        try:
            async for event in events:
                await queue.put(event)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - наружу поток отдаёт событие, а не 500
            await queue.put(
                SseEvent(
                    event="error",
                    data=ErrorFrame(code="stream_failed", retry_after_sec=5),
                )
            )
        finally:
            await queue.put(None)

    pump_task = asyncio.create_task(pump(), name=f"sse-pump:{check_id}")
    ledger = _SourceLedger()
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL_SEC)
            except TimeoutError:
                yield _HEARTBEAT_FRAME
                continue
            if event is None:
                break
            yield _render(_with_full_sources(event, ledger))
    finally:
        pump_task.cancel()
        with suppress(asyncio.CancelledError):
            await pump_task


def _with_full_sources(event: SseEvent, ledger: _SourceLedger) -> SseEvent:
    """Дополнить кадр `progress` полным списком источников и серверным прогрессом."""
    if event.event != "progress":
        return event
    frame = (
        event.data
        if isinstance(event.data, ProgressFrame)
        else ProgressFrame.model_validate(as_payload(event.data))
    )
    sources = ledger.merge(frame.sources)
    # Прогресс пересчитываем по полному списку: посчитанный по дельте, он
    # прыгал бы вниз на каждом кадре с одним источником.
    full = frame.model_copy(update={"sources": sources})
    if "progress" not in frame.model_fields_set:
        full.progress = progress_from_sources(sources)
    return SseEvent(event=event.event, data=full, id=event.id)


def _not_found(check_id: str) -> HTTPException:
    """404 на неизвестный id проверки."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "check_not_found", "detail": f"Проверка {check_id} не найдена"},
    )
