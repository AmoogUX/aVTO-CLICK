"""Контракт стриминга прогресса B2 (7.3): полный список источников в каждом кадре."""

from __future__ import annotations

import json

from avtoklik.collector.base import SourceQuery, SourceStatus
from avtoklik.collector.orchestrator import InMemoryCache, run_check
from avtoklik.collector.progress import (
    format_sse,
    heartbeat_comment,
    render_progress_event,
)
from tests.collector.fakes import FakeAdapter

QUERY = SourceQuery(key="а123ва777", plate="А123ВА777")


async def test_kadr_soderzhit_polnyy_spisok_istochnikov() -> None:
    """Дельты сэкономили бы байты и стоили бы склейки состояния при реконнекте."""
    adapters = [
        FakeAdapter("gibdd", title="ГИБДД · регистрация, ДТП", required=True),
        FakeAdapter("nomerogram", title="Номерограм · история фото"),
        FakeAdapter("drom", title="Дром · отзывы владельцев", error=RuntimeError("403")),
    ]
    frames: list[dict[str, object]] = []

    run = await run_check(
        adapters,
        QUERY,
        deadline_seconds=1.0,
        on_progress=lambda r: frames.append(render_progress_event(r)),
    )

    for frame in frames:
        sources = frame["sources"]
        assert isinstance(sources, list)
        assert [s["code"] for s in sources] == ["gibdd", "nomerogram", "drom"]
        assert [s["order"] for s in sources] == [1, 2, 3]
        # Подписи строк приходят с сервера: клиент их у себя не хранит.
        assert [s["title"] for s in sources] == [a.title for a in adapters]

    first, last = frames[0], frames[-1]
    assert first["progress"] == 0.0
    assert [s["state"] for s in first["sources"]] == ["queued", "queued", "queued"]
    assert last["progress"] == 1.0
    assert [s["state"] for s in last["sources"]] == ["ok", "ok", "error"]
    assert last["status"] == "partial"
    assert last["verdict_ready"] is True
    assert run.progress == 1.0


async def test_kadr_otrazhaet_kesh_i_taymaut() -> None:
    """`from_cache` несёт метку времени, таймаут отличается от прочих отказов."""
    clock = [1_758_000_000.0]
    cache = InMemoryCache(clock=lambda: clock[0])
    await cache.set("drom:а123ва777", {"listings": 2})
    clock[0] += 1800

    adapters = [
        FakeAdapter("drom", error=RuntimeError("503")),
        FakeAdapter("autoru", delay=1.0, timeout=0.02),
    ]
    run = await run_check(adapters, QUERY, deadline_seconds=1.0, cache=cache)
    frame = render_progress_event(run)
    by_code = {s["code"]: s for s in frame["sources"]}

    assert by_code["drom"]["state"] == "from_cache"
    assert by_code["drom"]["cache_age_s"] == 1800.0
    assert by_code["drom"]["cached_at"].startswith("2025-")
    assert by_code["autoru"]["state"] == "timeout"


async def test_opozdavshiy_pomechen_v_kadre() -> None:
    """Для клиента опоздавший всё ещё «ИЩЕМ…», но сервер знает, что он опоздал."""
    adapters = [FakeAdapter("gibdd", delay=0.01), FakeAdapter("drom", delay=0.3, timeout=5.0)]

    run = await run_check(adapters, QUERY, deadline_seconds=0.05)
    frame = render_progress_event(run)
    by_code = {s["code"]: s for s in frame["sources"]}

    assert by_code["drom"]["state"] == "running"
    assert by_code["drom"]["late"] is True
    assert frame["progress"] == 0.5
    assert frame["elapsed_ms"] >= 0

    assert run.late_completion is not None
    await run.late_completion


def test_format_sse_sobiraet_kadr() -> None:
    """`event:` / `id:` / `data:` и пустая строка-разделитель; `id` — для реконнекта."""
    raw = format_sse("progress", {"status": "running", "источник": "ГИБДД"}, event_id=4)

    assert raw.endswith("\n\n")
    lines = raw.rstrip("\n").split("\n")
    assert lines[0] == "event: progress"
    assert lines[1] == "id: 4"
    assert lines[2].startswith("data: ")
    assert "ГИБДД" in lines[2]  # кириллица не экранируется — поток читаем курлом
    assert json.loads(lines[2][len("data: ") :])["status"] == "running"


def test_format_sse_bez_id() -> None:
    """Без `id` кадр остаётся валидным — так шлются служебные события."""
    raw = format_sse("done", {"status": "partial"})

    assert "id:" not in raw
    assert raw.startswith("event: done\n")


def test_heartbeat_yavlyaetsya_kommentariem() -> None:
    """Пульс — комментарий SSE: клиент игнорирует, прокси не рвёт соединение."""
    assert heartbeat_comment() == ": keepalive\n\n"


async def test_status_i_progress_schitayutsya_serverom() -> None:
    """Клиент не считает ничего: в кадре готовые `progress` и `status`."""
    adapters = [
        FakeAdapter("gibdd", required=True, error=RuntimeError("недоступен")),
        FakeAdapter("drom"),
    ]

    run = await run_check(adapters, QUERY, deadline_seconds=1.0)
    frame = render_progress_event(run)

    assert frame["status"] == "missing_required"
    assert frame["verdict_ready"] is False
    assert frame["progress"] == 1.0


def test_status_sostoyaniya_otobrazhayutsya_odin_v_odin() -> None:
    """Каждому внутреннему статусу соответствует значение контракта."""
    from avtoklik.collector.progress import _WIRE_STATE

    assert set(_WIRE_STATE) == set(SourceStatus)
    assert _WIRE_STATE[SourceStatus.DONE] == "ok"
    assert _WIRE_STATE[SourceStatus.CACHED] == "from_cache"
