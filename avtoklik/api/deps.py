"""Внешние зависимости API: протоколы и провайдеры FastAPI `Depends`.

Почему протоколы, а не прямые импорты `avtoklik.collector`, `avtoklik.matching`
и `avtoklik.knowledge`.

API — верхний слой: он зависит от того, что умеют нижние, но не должен зависеть
от того, *как* они это делают. Здесь описано только то, что слою API реально
нужно от оркестратора и репозитория: запустить проверку, спросить состояние,
подписаться на поток событий. Всё остальное — пул соединений, адаптеры площадок,
Weibull-модель износа — за этой границей и API не касается.

Практических следствий три:

1. тесты API идут на заглушках, без сети, БД и брокера, и остаются быстрыми;
2. модули собираются параллельно: контракт зафиксирован, реализация — отдельно;
3. интеграция сводится к передаче реальной реализации в :func:`create_app`;
   ни один файл этого пакета при этом не меняется.

Это архитектурная граница, а не временная заглушка: она нужна и после того,
как все модули будут готовы.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, cast, runtime_checkable

from fastapi import Request

from avtoklik.api.schemas import (
    CheckStatusResponse,
    CheckVerdict,
    StartedCheck,
    Subject,
    VehicleBrief,
)

__all__ = [
    "CheckOrchestrator",
    "IdempotencyRecord",
    "IdempotencyStore",
    "InMemoryIdempotencyStore",
    "SseEvent",
    "VehicleRepository",
    "as_payload",
    "get_idempotency_store",
    "get_orchestrator",
    "get_vehicle_repository",
]


@dataclass(frozen=True, slots=True)
class SseEvent:
    """Одно событие потока прогресса (ТЗ, 7.3).

    `data` — либо pydantic-модель кадра, либо уже готовый словарь: оркестратору
    удобнее отдавать модель, а тестам и адаптерам — словарь. Сериализацию в
    формат SSE делает роутер, а не оркестратор: это деталь транспорта.
    """

    event: str
    data: Any
    id: int | None = None


@runtime_checkable
class CheckOrchestrator(Protocol):
    """Оркестратор проверки (ТЗ, 5.1) — то, что API от него требует.

    Инвариант, унаследованный из 5.1: деградация источника — это состояние,
    а не исключение. Методы не кидают ошибок «источник упал»; они возвращают
    состояние, в котором часть источников деградирована.
    """

    async def start_check(self, subject: Subject) -> StartedCheck:
        """Поставить проверку в работу и сразу вернуть её идентификатор."""
        ...

    async def get_check(self, check_id: str) -> CheckStatusResponse | None:
        """Текущее состояние проверки; `None` — проверки не существует."""
        ...

    async def get_verdict(self, check_id: str) -> CheckVerdict | None:
        """Готовый вердикт; `None` — проверки нет или вердикт ещё не собран."""
        ...

    def stream(self, check_id: str, last_event_id: int | None = None) -> AsyncIterator[SseEvent]:
        """События проверки.

        `last_event_id` приходит из заголовка `Last-Event-ID`: клиент нырнул в
        лифт на пять секунд, переподключился — и должен добрать пропущенное.
        Поток обязан завершаться после `done`/`error`, иначе соединение
        останется висеть до таймаута прокси.
        """
        ...

    async def cancel_check(self, check_id: str) -> bool:
        """Отменить проверку («Отменить» на экране B2). `False` — такой проверки нет."""
        ...


@runtime_checkable
class VehicleRepository(Protocol):
    """Справочник автомобилей: нужен только чтобы честно ответить 404 (CC5)."""

    async def find_vehicle(self, subject: Subject) -> VehicleBrief | None:
        """Карточка автомобиля по госномеру/VIN; `None` — не нашли."""
        ...


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """Запомненный результат изменяющего запроса.

    `fingerprint` — отпечаток тела запроса. Один и тот же ключ с другим телом —
    это ошибка клиента, а не повтор, и отвечать на неё надо 409, а не молча
    отдавать чужую проверку.
    """

    key: str
    fingerprint: str
    check_id: str


class IdempotencyStore(Protocol):
    """Хранилище ключей идемпотентности (ТЗ, 7: все изменяющие запросы их принимают)."""

    async def lookup(self, key: str) -> IdempotencyRecord | None:
        """Найти запись по ключу."""
        ...

    async def remember(self, key: str, fingerprint: str, check_id: str) -> IdempotencyRecord:
        """Запомнить результат и вернуть актуальную запись.

        Операция обязана быть атомарной «вставить-или-прочитать»: два
        одновременных запроса с одним ключом — норма (пользователь нажал
        «Проверить» дважды), и второй должен получить запись первого, а не
        затереть её. Реализация на Postgres — `INSERT ... ON CONFLICT DO NOTHING`
        с последующим `SELECT`.
        """
        ...


@dataclass(slots=True)
class InMemoryIdempotencyStore:
    """Хранилище в памяти процесса — значение по умолчанию и эталон для тестов.

    Для продакшена не годится: переживает только один процесс и не имеет TTL.
    Настоящая реализация подключается через :func:`create_app`.
    """

    _records: dict[str, IdempotencyRecord] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def lookup(self, key: str) -> IdempotencyRecord | None:
        async with self._lock:
            return self._records.get(key)

    async def remember(self, key: str, fingerprint: str, check_id: str) -> IdempotencyRecord:
        async with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                return existing
            record = IdempotencyRecord(key=key, fingerprint=fingerprint, check_id=check_id)
            self._records[key] = record
            return record


def _from_state(request: Request, attribute: str, human_name: str) -> Any:
    """Достать зависимость из `app.state` или честно упасть с понятным текстом."""
    value = getattr(request.app.state, attribute, None)
    if value is None:
        raise RuntimeError(
            f"{human_name} не сконфигурирован: передайте его в create_app() "
            f"или подмените зависимость через app.dependency_overrides"
        )
    return value


def get_orchestrator(request: Request) -> CheckOrchestrator:
    """Провайдер оркестратора проверки."""
    return cast("CheckOrchestrator", _from_state(request, "orchestrator", "Оркестратор проверки"))


def get_idempotency_store(request: Request) -> IdempotencyStore:
    """Провайдер хранилища ключей идемпотентности."""
    return cast(
        "IdempotencyStore",
        _from_state(request, "idempotency_store", "Хранилище идемпотентности"),
    )


def get_vehicle_repository(request: Request) -> VehicleRepository | None:
    """Провайдер справочника автомобилей.

    Необязателен: без него API просто не сможет ответить `vehicle_not_found`
    до запуска проверки и отдаст это решение оркестратору.
    """
    repository = getattr(request.app.state, "vehicle_repository", None)
    if repository is None:
        return None
    return cast("VehicleRepository", repository)


def as_payload(data: Any) -> Mapping[str, Any]:
    """Привести данные события к словарю для сериализации в SSE."""
    dump = getattr(data, "model_dump", None)
    if callable(dump):
        return cast("Mapping[str, Any]", dump(mode="json"))
    if isinstance(data, Mapping):
        return cast("Mapping[str, Any]", data)
    raise TypeError(f"кадр SSE должен быть моделью или словарём, получено {type(data)!r}")
