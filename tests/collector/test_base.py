"""Контракт адаптера источника: `fetch` никогда не выбрасывает исключение (5.1)."""

from __future__ import annotations

import asyncio

import pytest

from avtoklik.collector.base import SourceQuery, SourceResult, SourceStatus
from tests.collector.fakes import FakeAdapter

QUERY = SourceQuery(key="а123ва777", plate="А123ВА777")


async def test_uspeshnyy_otvet_neset_istochnik_i_metku_vremeni() -> None:
    """Каждое значение носит `source_id` и `fetched_at` — требование 5.6.5."""
    adapter = FakeAdapter("gibdd", payload={"dtp": 0})

    result = await adapter.fetch(QUERY)

    assert result.status is SourceStatus.DONE
    assert result.source_id == "gibdd"
    assert result.payload == {"dtp": 0}
    assert result.fetched_at > 0
    assert result.from_cache is False
    assert result.is_answer is True


async def test_isklyuchenie_adaptera_stanovitsya_failed() -> None:
    """Адаптер может падать как угодно — наружу уходит результат, а не исключение."""
    adapter = FakeAdapter("drom", error=RuntimeError("капча"))

    result = await adapter.fetch(QUERY)

    assert result.status is SourceStatus.FAILED
    assert result.error is not None
    assert "капча" in result.error
    assert result.is_answer is False


async def test_prevyshenie_byudzheta_stanovitsya_timeout() -> None:
    """Бюджет — свойство источника, адаптер не может его игнорировать."""
    adapter = FakeAdapter("autoru", delay=1.0, timeout=0.02)

    result = await adapter.fetch(QUERY)

    assert result.status is SourceStatus.FAILED
    assert result.error == "timeout"


async def test_otmena_ne_gasitsya() -> None:
    """Отмена всей проверки — не отказ источника: её нельзя «проглотить»."""
    adapter = FakeAdapter("social", delay=5.0, timeout=10.0)
    task = asyncio.create_task(adapter.fetch(QUERY))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


def test_kesh_bez_vozrasta_zapreshchen() -> None:
    """Ответ из кэша без возраста нечем подписать на экране («данные от 12:40»)."""
    with pytest.raises(ValueError):
        SourceResult(source_id="drom", status=SourceStatus.CACHED, from_cache=True)
    with pytest.raises(ValueError):
        SourceResult(source_id="drom", status=SourceStatus.CACHED, from_cache=False)
