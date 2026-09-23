"""Контракт адаптера источника (ТЗ, раздел 5.1).

Здесь только каркас: конкретные адаптеры площадок (Авито, Авто.ру, Дром, форумы)
намеренно НЕ реализованы — правовой статус такого сбора в проекте не закрыт.
Когда доступ определят, адаптер пишется под этот контракт и ничего в оркестраторе
менять не потребуется.

Ключевой инвариант раздела 5.1: `SourceAdapter.fetch` никогда не выбрасывает
исключение. У оркестратора нет ветки «источник упал» — у него есть N результатов,
часть из которых деградированные. Деградация — это состояние, а не ошибка (CC2).
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "DEFAULT_SOURCE_TIMEOUT_SECONDS",
    "SourceAdapter",
    "SourceQuery",
    "SourceResult",
    "SourceStatus",
]

#: Бюджет источника по умолчанию (§8.2: классифайды 10 с, Номерограм 12 с, ГИБДД 20 с).
DEFAULT_SOURCE_TIMEOUT_SECONDS = 12.0


class SourceStatus(StrEnum):
    """Состояние источника внутри одной проверки.

    Отображается один-в-один на подписи экрана B2, поэтому клиенту не нужна логика:
    `QUEUED` → «В ОЧЕРЕДИ», `RUNNING` → «ИЩЕМ…», `DONE` → «ГОТОВО»,
    `CACHED` → «ГОТОВО · кэш 12:40», `FAILED` → «не ответил».

    `QUEUED` — это не «мы ещё не начали», а ожидание слота в rate-limiter'е площадки.
    """

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CACHED = "cached"


@dataclass(frozen=True, slots=True)
class SourceQuery:
    """Предмет проверки, одинаковый для всех источников.

    `key` — ключ кэширования (обычно нормализованный госномер или VIN):
    кэш общий на источник + предмет, поэтому ключ приходит снаружи, а не считается
    адаптером.
    """

    key: str
    plate: str | None = None
    vin: str | None = None
    model_hint: str | None = None

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("ключ запроса не может быть пустым")


@dataclass(frozen=True, slots=True)
class SourceResult:
    """Результат одного источника.

    Каждое значение носит `fetched_at` и `source_id` — требование раздела 5.6.5
    (свежесть и атрибуция): без них невозможно ни показать плашку «данные от 12:40»,
    ни сказать пользователю, откуда взялся факт.
    """

    source_id: str
    status: SourceStatus
    payload: Any = None
    fetched_at: float = field(default_factory=time.time)
    from_cache: bool = False
    cache_age_seconds: float | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status is SourceStatus.CACHED and not self.from_cache:
            raise ValueError("статус CACHED требует from_cache=True")
        if self.from_cache and self.cache_age_seconds is None:
            raise ValueError("ответ из кэша обязан нести возраст кэша")
        if self.cache_age_seconds is not None and self.cache_age_seconds < 0:
            raise ValueError("возраст кэша не может быть отрицательным")

    @property
    def is_answer(self) -> bool:
        """Источник дал данные, пригодные для вердикта (в том числе протухшие, CC2)."""
        return self.status in (SourceStatus.DONE, SourceStatus.CACHED)

    @classmethod
    def ok(cls, source_id: str, payload: Any) -> SourceResult:
        """Успешный свежий ответ источника."""
        return cls(source_id=source_id, status=SourceStatus.DONE, payload=payload)

    @classmethod
    def failed(cls, source_id: str, error: str) -> SourceResult:
        """Отказ источника. Именно сюда сходятся все исключения адаптера."""
        return cls(source_id=source_id, status=SourceStatus.FAILED, error=error)

    @classmethod
    def cached(
        cls,
        source_id: str,
        payload: Any,
        *,
        stored_at: float,
        age_seconds: float,
        error: str | None = None,
    ) -> SourceResult:
        """Деградация на кэш: «Дром не отвечает, показываем его данные от 12:40» (CC2).

        `fetched_at` — время, когда данные реально получили, а не время деградации:
        на экране показывается именно оно.
        """
        return cls(
            source_id=source_id,
            status=SourceStatus.CACHED,
            payload=payload,
            fetched_at=stored_at,
            from_cache=True,
            cache_age_seconds=age_seconds,
            error=error,
        )


class SourceAdapter(ABC):
    """Базовый класс источника: адаптер реализует только `fetch_payload`.

    Разделение `fetch` / `fetch_payload` сделано ради инварианта 5.1: конкретный
    адаптер может падать как угодно (сеть, капча, кривой HTML, свой таймаут),
    а наружу всё равно уходит `SourceResult`. Реализация, которая переопределит
    `fetch`, обязана сохранить этот инвариант.
    """

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Код источника: `gibdd`, `nomerogram`, `drom`, … (совпадает с `sources.code`)."""

    @property
    @abstractmethod
    def title(self) -> str:
        """Подпись строки на экране B2. Приходит с сервера (§7.3).

        Добавили седьмой источник — он появился на экране без релиза приложения,
        поэтому клиент никогда не хранит эти строки у себя.
        """

    @property
    def timeout_seconds(self) -> float:
        """Личный бюджет источника (`sources.budget_ms`); у медленных (ГИБДД) — больше."""
        return DEFAULT_SOURCE_TIMEOUT_SECONDS

    @property
    def is_required(self) -> bool:
        """Обязателен ли источник для вердикта (`verdict_requirements`).

        ГИБДД обязателен — без него вердикт не выдаётся вообще. Остальные нет:
        без Дрома блок площадок просто показывается неполным.
        """
        return False

    @property
    def cache_ttl_seconds(self) -> float | None:
        """Порог «свежо» из §8.3. `None` — короткого замыкания на кэш нет.

        Если кэш свежее порога, источник не опрашивается вовсе: повторная проверка
        того же номера обязана отдаваться за < 1 с, а не за 40 (§8.2).
        """
        return None

    @abstractmethod
    async def fetch_payload(self, query: SourceQuery) -> Any:
        """Собственно поход в источник. Может бросать что угодно."""

    async def fetch(self, query: SourceQuery) -> SourceResult:
        """Инвариант 5.1: НИКОГДА не выбрасывает исключение.

        Таймаут навешивается здесь же, а не только в оркестраторе: бюджет —
        свойство источника, и адаптер не должен иметь возможности его игнорировать.
        """
        try:
            payload = await asyncio.wait_for(self.fetch_payload(query), self.timeout_seconds)
        except TimeoutError:
            # Отдельный маркер: на экране это «не ответил», а в метриках — не ошибка парсера.
            return SourceResult.failed(self.source_id, "timeout")
        except asyncio.CancelledError:
            # Отмена всей проверки (жёсткий дедлайн, уход пользователя) — не отказ
            # источника, и «проглотить» её нельзя: иначе задача не остановится.
            raise
        except Exception as exc:  # noqa: BLE001 — это и есть точка сбора всех отказов
            return SourceResult.failed(self.source_id, f"{type(exc).__name__}: {exc}")
        return SourceResult.ok(self.source_id, payload)
