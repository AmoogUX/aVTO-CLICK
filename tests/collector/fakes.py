"""Фейковые адаптеры для тестов оркестрации.

Настоящих адаптеров площадок в проекте пока нет и в тестах быть не должно:
ни одного сетевого запроса. Всё поведение источника — задержка, исключение,
полезная нагрузка — задаётся здесь явно.
"""

from __future__ import annotations

import asyncio
from typing import Any

from avtoklik.collector.base import DEFAULT_SOURCE_TIMEOUT_SECONDS, SourceAdapter, SourceQuery


class FakeAdapter(SourceAdapter):
    """Источник с управляемым поведением: задержка, ответ или исключение."""

    def __init__(
        self,
        source_id: str,
        *,
        title: str | None = None,
        delay: float = 0.0,
        payload: Any = None,
        error: Exception | None = None,
        timeout: float = DEFAULT_SOURCE_TIMEOUT_SECONDS,
        required: bool = False,
        cache_ttl: float | None = None,
    ) -> None:
        self._source_id = source_id
        self._title = title or f"{source_id} · тестовый источник"
        self._delay = delay
        self._payload = payload if payload is not None else {"source": source_id}
        self._error = error
        self._timeout = timeout
        self._required = required
        self._cache_ttl = cache_ttl
        #: Сколько раз источник реально опрашивался — так проверяется,
        #: что свежий кэш замыкает запрос, а «опоздавшего» никто не отменил.
        self.calls = 0
        self.finished = False

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def title(self) -> str:
        return self._title

    @property
    def timeout_seconds(self) -> float:
        return self._timeout

    @property
    def is_required(self) -> bool:
        return self._required

    @property
    def cache_ttl_seconds(self) -> float | None:
        return self._cache_ttl

    async def fetch_payload(self, query: SourceQuery) -> Any:
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        self.finished = True
        return self._payload
