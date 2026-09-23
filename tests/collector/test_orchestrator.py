"""Оркестрация проверки B2: дедлайны, деградация на кэш, вердикт (5.1)."""

from __future__ import annotations

import time

from avtoklik.collector.base import SourceQuery, SourceStatus
from avtoklik.collector.orchestrator import CheckStatus, InMemoryCache, run_check
from tests.collector.fakes import FakeAdapter

QUERY = SourceQuery(key="а123ва777", plate="А123ВА777")


async def test_upavshiy_istochnik_ne_ronyaet_orkestrator() -> None:
    """У оркестратора нет ветки «источник упал» — есть N результатов, часть плохих."""
    adapters = [
        FakeAdapter("gibdd", payload={"dtp": 1}),
        FakeAdapter("drom", error=RuntimeError("403 от площадки")),
    ]

    run = await run_check(adapters, QUERY, deadline_seconds=1.0)

    assert run.source("gibdd").status is SourceStatus.DONE
    assert run.source("drom").status is SourceStatus.FAILED
    assert run.source("drom").error is not None
    assert run.progress == 1.0


async def test_medlennyy_istochnik_ne_meshaet_ostalnym() -> None:
    """Личный бюджет источника: медленный отваливается, остальные отвечают нормально."""
    slow = FakeAdapter("autoru", delay=1.0, timeout=0.02)
    adapters = [FakeAdapter("gibdd"), slow, FakeAdapter("nomerogram")]

    run = await run_check(adapters, QUERY, deadline_seconds=2.0)

    assert run.source("autoru").status is SourceStatus.FAILED
    assert run.source("autoru").error == "timeout"
    assert run.source("gibdd").status is SourceStatus.DONE
    assert run.source("nomerogram").status is SourceStatus.DONE


async def test_myagkiy_dedlayn_otdayot_chastichnyy_rezultat() -> None:
    """Опоздавшие не отменяются: частичный результат сейчас, доработка — потом."""
    slow = FakeAdapter("drom", delay=0.35, timeout=5.0)
    adapters = [FakeAdapter("gibdd", delay=0.01), FakeAdapter("avito", delay=0.01), slow]

    started = time.monotonic()
    run = await run_check(adapters, QUERY, deadline_seconds=0.1)
    elapsed = time.monotonic() - started

    # Частичный результат отдан вовремя — ждать опоздавшего пользователь не должен.
    assert elapsed < 0.3
    assert run.progress == 2 / 3
    assert run.late_source_ids == ("drom",)
    assert run.source("drom").status is SourceStatus.RUNNING
    assert run.late_completion is not None
    assert not run.late_completion.done()

    # Опоздавший не отменён: он доработал и дописал результат.
    finished = await run.late_completion
    assert finished is run
    assert run.source("drom").status is SourceStatus.DONE
    assert run.source("drom").is_late is True  # флаг остаётся — по нему шлётся пуш CC2
    assert run.progress == 1.0
    assert slow.finished is True


async def test_otkaz_istochnika_degradiruet_na_kesh() -> None:
    """CC2: «Дром не отвечает. Показываем его данные от 12:40»."""
    # Управляемые часы: данные легли в кэш, прошёл час, источник отвалился.
    clock = [1_758_000_000.0]
    cache = InMemoryCache(clock=lambda: clock[0])
    await cache.set("drom:а123ва777", {"listings": 3})
    stored_at = clock[0]
    clock[0] += 3600

    adapters = [FakeAdapter("drom", error=TimeoutError("нет ответа"))]
    run = await run_check(adapters, QUERY, deadline_seconds=1.0, cache=cache)

    state = run.source("drom")
    assert state.status is SourceStatus.CACHED
    assert state.payload == {"listings": 3}
    assert state.cache_age_seconds is not None
    assert state.cache_age_seconds == 3600
    assert state.fetched_at == stored_at  # метка времени — когда данные получены, а не когда упали


async def test_otkaz_bez_kesha_ostayotsya_otkazom() -> None:
    """Пустого кэша достаточно, чтобы деградация не состоялась."""
    adapters = [FakeAdapter("drom", error=RuntimeError("бан IP"))]

    run = await run_check(adapters, QUERY, deadline_seconds=1.0, cache=InMemoryCache())

    assert run.source("drom").status is SourceStatus.FAILED


async def test_svezhiy_kesh_zamykaet_zapros() -> None:
    """Повторная проверка того же номера обязана отдаваться из кэша (§8.2, §8.3)."""
    cache = InMemoryCache()
    await cache.set("avito:а123ва777", {"listings": 1})
    adapter = FakeAdapter("avito", cache_ttl=900.0)

    run = await run_check([adapter], QUERY, deadline_seconds=1.0, cache=cache)

    assert run.source("avito").status is SourceStatus.CACHED
    assert adapter.calls == 0  # источник не трогали вовсе


async def test_obyazatelnyy_istochnik_upal_verdikt_ne_vydayotsya() -> None:
    """Не готов ГИБДД — вердикта нет вообще, экран «не хватает главного»."""
    adapters = [
        FakeAdapter("gibdd", required=True, error=RuntimeError("недоступен")),
        FakeAdapter("drom"),
    ]

    run = await run_check(adapters, QUERY, deadline_seconds=1.0)

    assert run.verdict_ready is False
    assert run.status is CheckStatus.MISSING_REQUIRED


async def test_neobyazatelnyy_istochnik_upal_verdikt_vydayotsya() -> None:
    """Не готов Дром — вердикт выдаём, блок площадок показываем неполным."""
    adapters = [
        FakeAdapter("gibdd", required=True),
        FakeAdapter("nomerogram", required=True),
        FakeAdapter("drom", error=RuntimeError("таймаут площадки")),
    ]

    run = await run_check(adapters, QUERY, deadline_seconds=1.0)

    assert run.verdict_ready is True
    assert run.status is CheckStatus.PARTIAL


async def test_obyazatelnyy_istochnik_iz_kesha_daet_verdikt() -> None:
    """Протухший кэш обязательного источника всё равно закрывает вердикт (CC2)."""
    cache = InMemoryCache()
    await cache.set("gibdd:а123ва777", {"dtp": 0})
    adapters = [FakeAdapter("gibdd", required=True, error=RuntimeError("503"))]

    run = await run_check(adapters, QUERY, deadline_seconds=1.0, cache=cache)

    assert run.source("gibdd").status is SourceStatus.CACHED
    assert run.verdict_ready is True
    assert run.status is CheckStatus.PARTIAL


async def test_vse_otvetili_status_done() -> None:
    """Все источники свежие — проверка завершена полностью."""
    adapters = [FakeAdapter("gibdd", required=True), FakeAdapter("drom")]

    run = await run_check(adapters, QUERY, deadline_seconds=1.0)

    assert run.status is CheckStatus.DONE
    assert run.progress == 1.0


async def test_progress_schitaet_server() -> None:
    """Доля ответивших считается на сервере и растёт по мере ответов."""
    snapshots: list[float] = []
    adapters = [
        FakeAdapter("gibdd", delay=0.01),
        FakeAdapter("drom", delay=0.05),
        FakeAdapter("avito", delay=0.09),
        FakeAdapter("social", error=RuntimeError("пусто")),
    ]

    run = await run_check(
        adapters, QUERY, deadline_seconds=2.0, on_progress=lambda r: snapshots.append(r.progress)
    )

    assert snapshots[0] == 0.0  # первый кадр — «все в очереди»
    assert snapshots == sorted(snapshots)  # прогресс не убывает
    assert run.progress == 1.0  # отказ тоже «ответил»: строка перестала крутиться
    assert run.source("social").status is SourceStatus.FAILED
