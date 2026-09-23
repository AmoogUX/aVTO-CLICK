"""Оркестрация проверки: параллельный опрос источников (ТЗ, раздел 5.1).

Три вещи, которые здесь принципиальны и легко потерять при реализации:

1. `_fetch_one` не выбрасывает исключений — у оркестратора нет ветки «источник упал».
2. «Опоздавшие» не отменяются: задача, не уложившаяся в мягкий дедлайн, продолжает
   работать в фоне и дописывает результат. Отменить её — значит выбросить уже
   потраченный запрос к лимитированному источнику.
3. Прогресс и вердикт считает сервер. Клиент только отрисовывает.

Состояние здесь живёт в памяти процесса; по спеке боевой оркестратор хранит его
в Postgres (`check_runs` + `check_run_sources`), чтобы проверка переживала рестарт.
Этот каркас сознательно не тянет БД: слой хранения подключается через тот же
`CheckRun`, не меняя алгоритма.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from avtoklik.collector.base import SourceAdapter, SourceQuery, SourceResult, SourceStatus

__all__ = [
    "HARD_DEADLINE_SECONDS",
    "SOFT_DEADLINE_SECONDS",
    "CacheEntry",
    "CacheProtocol",
    "CheckRun",
    "CheckStatus",
    "InMemoryCache",
    "SourceState",
    "run_check",
]

#: После мягкого дедлайна отдаём частичный вердикт (§8.2: худший случай для
#: пользователя — ровно 40 секунд, а не 40 + сколько-то).
SOFT_DEADLINE_SECONDS = 40.0
#: Жёсткий дедлайн — на нём фоновая доработка обрывается.
HARD_DEADLINE_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """Запись кэша с возрастом. Возраст считает кэш, а не вызывающий код."""

    payload: Any
    stored_at: float
    age_seconds: float


class CacheProtocol(Protocol):
    """Минимальный интерфейс кэша, нужный оркестратору.

    Намеренно узкий: боевой Redis-кэш реализует его же, и подмена в тестах
    не требует ни мока сети, ни живого Redis.
    """

    async def get(self, key: str) -> CacheEntry | None:
        """Вернуть запись с её возрастом или `None`, если ключа нет."""
        ...

    async def set(self, key: str, payload: Any) -> None:
        """Положить значение, зафиксировав момент получения."""
        ...


class InMemoryCache:
    """Кэш в памяти для тестов и локального запуска.

    `clock` инжектируется, чтобы тест мог предъявить «данные от 12:40», не ожидая
    реального времени.
    """

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock: Callable[[], float] = clock or time.time
        self._items: dict[str, tuple[Any, float]] = {}

    async def get(self, key: str) -> CacheEntry | None:
        """Вернуть запись с возрастом на момент чтения."""
        item = self._items.get(key)
        if item is None:
            return None
        payload, stored_at = item
        return CacheEntry(
            payload=payload,
            stored_at=stored_at,
            age_seconds=max(0.0, self._clock() - stored_at),
        )

    async def set(self, key: str, payload: Any) -> None:
        """Записать значение с текущей меткой времени."""
        self._items[key] = (payload, self._clock())


class CheckStatus(StrEnum):
    """Итоговое состояние проверки.

    `MISSING_REQUIRED` — тот самый «специальный статус»: обязательный источник не
    ответил, вердикт не выдаётся вообще, экран показывает «не хватает главного»
    с ретраем (в SSE это `error` с кодом `critical_source_unavailable`).
    """

    RUNNING = "running"
    DONE = "done"
    PARTIAL = "partial"
    MISSING_REQUIRED = "missing_required"


@dataclass(slots=True)
class SourceState:
    """Строка экрана B2: один источник внутри одной проверки."""

    source_id: str
    title: str
    order: int
    is_required: bool
    status: SourceStatus = SourceStatus.QUEUED
    payload: Any = None
    fetched_at: float | None = None
    cache_age_seconds: float | None = None
    error: str | None = None
    #: Источник не уложился в мягкий дедлайн и дописывается в фоне.
    #: Флаг не снимается после прихода данных: по нему шлётся пуш «дособрали» (CC2).
    is_late: bool = False

    @property
    def is_settled(self) -> bool:
        """Источник отработал: ждать от него больше нечего (в том числе отказ)."""
        return self.status in (SourceStatus.DONE, SourceStatus.CACHED, SourceStatus.FAILED)

    @property
    def is_usable(self) -> bool:
        """Данные есть и годятся для вердикта — свежие или из кэша (CC2)."""
        return self.status in (SourceStatus.DONE, SourceStatus.CACHED)

    def apply(self, result: SourceResult) -> None:
        """Перенести результат источника в состояние строки."""
        self.status = result.status
        self.payload = result.payload
        self.fetched_at = result.fetched_at
        self.cache_age_seconds = result.cache_age_seconds
        self.error = result.error


@dataclass(slots=True)
class CheckRun:
    """Состояние одной проверки: то, из чего рисуется экран B2 и считается вердикт."""

    run_id: str
    query: SourceQuery
    sources: list[SourceState]
    started_at: float
    started_monotonic: float
    finished_at: float | None = None
    #: Awaitable на доработку «опоздавших»; `None`, если все успели к дедлайну.
    #: Вызывающий код получает и частичный результат (сам `CheckRun`), и это —
    #: дождаться его можно, а можно отдать пользователю частичный вердикт и уйти.
    late_completion: asyncio.Task[CheckRun] | None = field(default=None, compare=False)

    @property
    def progress(self) -> float:
        """Доля отработавших источников. Считает сервер, не клиент (§7.3).

        Отказ тоже считается отработавшим: строка на экране перестала крутиться,
        и прогресс, застывший на 83 % из-за лежащего Дрома, врал бы пользователю.
        """
        if not self.sources:
            return 1.0
        return sum(1 for s in self.sources if s.is_settled) / len(self.sources)

    @property
    def verdict_ready(self) -> bool:
        """Вердикт выдаётся, только если ответили ВСЕ обязательные источники.

        Правило зашито в `is_required` источника, а не в код оркестратора:
        добавить седьмой обязательный источник — это строка в таблице `sources`.
        """
        return all(s.is_usable for s in self.sources if s.is_required)

    @property
    def status(self) -> CheckStatus:
        """Статус проверки для кадра SSE и для записи в `check_runs`."""
        if self.finished_at is None:
            return CheckStatus.RUNNING
        if not self.verdict_ready:
            return CheckStatus.MISSING_REQUIRED
        if all(s.status is SourceStatus.DONE for s in self.sources):
            return CheckStatus.DONE
        return CheckStatus.PARTIAL

    @property
    def late_source_ids(self) -> tuple[str, ...]:
        """Источники, не уложившиеся в мягкий дедлайн."""
        return tuple(s.source_id for s in self.sources if s.is_late)

    def source(self, source_id: str) -> SourceState:
        """Найти строку источника по коду."""
        for state in self.sources:
            if state.source_id == source_id:
                return state
        raise KeyError(source_id)

    def elapsed_ms(self) -> int:
        """Сколько идёт проверка — для кадра прогресса."""
        return int((time.monotonic() - self.started_monotonic) * 1000)


ProgressHook = Callable[[CheckRun], None]


def _cache_key(source_id: str, query: SourceQuery) -> str:
    """Кэш общий на источник + предмет проверки."""
    return f"{source_id}:{query.key}"


async def _fetch_one(
    adapter: SourceAdapter,
    query: SourceQuery,
    cache: CacheProtocol | None,
    state: SourceState,
) -> SourceResult:
    """Опрос одного источника. Инвариант: возвращает результат ВСЕГДА.

    Порядок шагов взят из псевдокода 5.1: свежий кэш → источник → деградация
    на протухший кэш. Предохранитель (circuit breaker) и rate-limiter площадки
    сюда добавятся, когда появятся боевые адаптеры: на контракт они не влияют.
    """
    key = _cache_key(adapter.source_id, query)
    try:
        # 1. Свежий кэш — мгновенный ответ, источник не трогаем (§8.3, §8.2).
        ttl = adapter.cache_ttl_seconds
        if cache is not None and ttl is not None:
            fresh = await cache.get(key)
            if fresh is not None and fresh.age_seconds <= ttl:
                return SourceResult.cached(
                    adapter.source_id,
                    fresh.payload,
                    stored_at=fresh.stored_at,
                    age_seconds=fresh.age_seconds,
                )

        state.status = SourceStatus.RUNNING  # → «ИЩЕМ…» на экране B2
        result = await adapter.fetch(query)

        if result.status is SourceStatus.DONE:
            if cache is not None:
                await cache.set(key, result.payload)
            return result

        # 2. Деградация: протухший кэш лучше пустоты (CC2).
        if cache is not None:
            stale = await cache.get(key)
            if stale is not None:
                return SourceResult.cached(
                    adapter.source_id,
                    stale.payload,
                    stored_at=stale.stored_at,
                    age_seconds=stale.age_seconds,
                    error=result.error,
                )
        return result
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — упасть может и кэш; наружу всё равно результат
        return SourceResult.failed(adapter.source_id, f"{type(exc).__name__}: {exc}")


def _emit(run: CheckRun, hook: ProgressHook | None) -> None:
    """Опубликовать кадр прогресса (в бою — в шину SSE)."""
    if hook is not None:
        hook(run)


async def _finish_late(
    run: CheckRun,
    pending: set[asyncio.Task[SourceResult]],
    owners: dict[asyncio.Task[SourceResult], SourceState],
    hook: ProgressHook | None,
) -> CheckRun:
    """Дописать «опоздавших» после мягкого дедлайна.

    Задачи здесь НЕ отменяются (требование 5.1): запрос к лимитированному источнику
    уже потрачен, и выбрасывать его результат — чистый убыток. В бою отсюда же
    уходит пуш «обновили сами» (CC2, 5–15 минут).
    """

    async def _one(task: asyncio.Task[SourceResult]) -> None:
        result = await task
        owners[task].apply(result)
        _emit(run, hook)

    await asyncio.gather(*(_one(task) for task in pending))
    return run


async def run_check(
    adapters: Sequence[SourceAdapter],
    query: SourceQuery,
    deadline_seconds: float = SOFT_DEADLINE_SECONDS,
    cache: CacheProtocol | None = None,
    run_id: str | None = None,
    on_progress: ProgressHook | None = None,
) -> CheckRun:
    """Опросить источники параллельно и вернуть состояние проверки.

    Общее время = max по источникам, а не сумма. По истечении мягкого дедлайна
    возвращается частичный результат; «опоздавшие» остаются в `late_completion` —
    awaitable, который дописывает их в фоне и возвращает тот же `CheckRun`.

    Вызывающий код волен либо отдать частичный вердикт пользователю и не ждать,
    либо дождаться `late_completion` (например, в тестах или в фоновом воркере).
    """
    started_at = time.time()
    started_monotonic = time.monotonic()
    run = CheckRun(
        run_id=run_id or str(uuid.uuid4()),
        query=query,
        sources=[
            SourceState(
                source_id=adapter.source_id,
                title=adapter.title,
                order=index + 1,
                is_required=adapter.is_required,
            )
            for index, adapter in enumerate(adapters)
        ],
        started_at=started_at,
        started_monotonic=started_monotonic,
    )

    owners: dict[asyncio.Task[SourceResult], SourceState] = {}
    for adapter, state in zip(adapters, run.sources, strict=True):
        task = asyncio.create_task(
            _fetch_one(adapter, query, cache, state),
            name=f"fetch:{adapter.source_id}",
        )
        owners[task] = state

    _emit(run, on_progress)  # первый кадр «все в очереди» — до 400 мс (§8.2)

    pending: set[asyncio.Task[SourceResult]] = set(owners)
    while pending:
        remaining = deadline_seconds - (time.monotonic() - started_monotonic)
        if remaining <= 0:
            break
        done, pending = await asyncio.wait(
            pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            # `_fetch_one` НИКОГДА не кидает, поэтому `result()` безопасен.
            owners[task].apply(task.result())
            _emit(run, on_progress)  # частичные результаты — UI обновляется сразу

    if pending:
        for task in pending:
            owners[task].is_late = True
        run.late_completion = asyncio.ensure_future(_finish_late(run, pending, owners, on_progress))

    run.finished_at = time.time()
    _emit(run, on_progress)
    return run
