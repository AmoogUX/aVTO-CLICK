"""Тесты флоу B «проверка автомобиля» (ТЗ, разделы 7.1 и 7.3).

Всё идёт через ASGI-транспорт httpx: приложение поднимается в процессе теста,
сокеты, БД и площадки не участвуют.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from avtoklik.api import create_app, routes_check
from avtoklik.api.schemas import Subject, SubjectType
from tests.api.conftest import READY_CHECK_ID, SOURCE_TITLES, StubOrchestrator

CHECKS_URL = "/api/v1/checks"
VALID_PLATE = "К999КК799"


async def test_health_ok(client: AsyncClient) -> None:
    """Лайвнес-проба отвечает 200 и не зависит от состояния источников."""
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_create_check_by_plate(client: AsyncClient, orchestrator: StubOrchestrator) -> None:
    """Запуск по валидному госномеру: 202 и идентификатор проверки."""
    response = await client.post(CHECKS_URL, json={"plate": VALID_PLATE})

    assert response.status_code == 202
    body = response.json()
    assert body["check_id"]
    assert body["stream_url"] == f"{CHECKS_URL}/{body['check_id']}/stream"
    assert body["eta_sec"] == 40
    assert orchestrator.start_calls == [Subject(type=SubjectType.PLATE, value=VALID_PLATE)]


async def test_create_check_normalizes_latin_plate(
    client: AsyncClient, orchestrator: StubOrchestrator
) -> None:
    """CC5: номер в латинской раскладке и с пробелами приводится, а не отвергается."""
    response = await client.post(CHECKS_URL, json={"plate": "k999kk 799"})

    assert response.status_code == 202
    assert orchestrator.start_calls[0].value == VALID_PLATE


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        pytest.param({}, "validation_failed", id="ни госномера, ни VIN"),
        pytest.param(
            {"plate": VALID_PLATE, "vin": "XW8ZZZ61ZJG511111"},
            "validation_failed",
            id="госномер и VIN одновременно",
        ),
        pytest.param({"plate": "Z999ZZ799"}, "invalid_plate", id="недопустимые буквы (CC5)"),
        pytest.param({"vin": "XW8ZZZ61ZJG5111"}, "invalid_vin", id="короткий VIN"),
    ],
)
async def test_create_check_rejects_invalid_input(
    client: AsyncClient,
    orchestrator: StubOrchestrator,
    payload: dict[str, str],
    expected_code: str,
) -> None:
    """Невалидный ввод — 422 с машинным кодом и человеческой подсказкой."""
    response = await client.post(CHECKS_URL, json=payload)

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == expected_code
    assert body["detail"]
    assert orchestrator.start_calls == []


async def test_invalid_plate_carries_allowed_letters_hint(client: AsyncClient) -> None:
    """CC5 требует не «validation error», а перечисления допустимых букв."""
    response = await client.post(CHECKS_URL, json={"plate": "Z999ZZ799"})

    assert response.status_code == 422
    assert "А В Е К М Н О Р С Т У Х" in response.json()["detail"]


async def test_idempotency_key_replays_same_check(
    client: AsyncClient, orchestrator: StubOrchestrator
) -> None:
    """Повтор с тем же ключом возвращает ту же проверку, а не создаёт новую."""
    headers = {"Idempotency-Key": "key-1"}

    first = await client.post(CHECKS_URL, json={"plate": VALID_PLATE}, headers=headers)
    second = await client.post(CHECKS_URL, json={"plate": VALID_PLATE}, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["check_id"] == second.json()["check_id"]
    assert second.headers["Idempotency-Replayed"] == "true"
    # Главное: шесть источников не опрашивались второй раз.
    assert len(orchestrator.start_calls) == 1


async def test_idempotency_key_reuse_with_other_body_conflicts(
    client: AsyncClient, orchestrator: StubOrchestrator
) -> None:
    """Тот же ключ с другим телом — ошибка клиента, 409, а не чужая проверка."""
    headers = {"Idempotency-Key": "key-2"}
    await client.post(CHECKS_URL, json={"plate": VALID_PLATE}, headers=headers)

    conflict = await client.post(CHECKS_URL, json={"plate": "А341ВР116"}, headers=headers)

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_key_reused"
    assert len(orchestrator.start_calls) == 1


async def test_status_of_unknown_check_is_404(client: AsyncClient) -> None:
    """Статус несуществующей проверки — 404 с машинным кодом."""
    response = await client.get(f"{CHECKS_URL}/chk-does-not-exist")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "check_not_found"


async def test_stream_of_unknown_check_is_404(client: AsyncClient) -> None:
    """404 должен прилететь до первого байта тела, а не внутри потока."""
    response = await client.get(f"{CHECKS_URL}/chk-does-not-exist/stream")

    assert response.status_code == 404


async def test_status_progress_is_computed_by_server(client: AsyncClient) -> None:
    """Прогресс считает сервер: клиент получает готовое число."""
    response = await client.get(f"{CHECKS_URL}/{READY_CHECK_ID}")

    assert response.status_code == 200
    body = response.json()
    assert body["progress"] == 1.0
    assert len(body["sources"]) == len(SOURCE_TITLES)
    assert body["sources"][4]["cache_age_s"] == 3600


async def test_ready_result_has_score_blocks_and_incompleteness(client: AsyncClient) -> None:
    """Готовый результат: скоринг, блоки проверок и честные «5 из 6» (CC2)."""
    response = await client.get(f"{CHECKS_URL}/{READY_CHECK_ID}")

    assert response.status_code == 200
    verdict = response.json()["verdict"]

    assert verdict["score"] == 82
    assert verdict["headline"] == "Можно смотреть"
    assert {block["code"] for block in verdict["checks"]} == {
        "mileage",
        "owners",
        "accidents",
        "legal",
    }
    coverage = verdict["coverage"]
    assert (coverage["sources_ok"], coverage["sources_total"]) == (5, 6)
    assert coverage["complete"] is False
    assert coverage["summary"] == "5 из 6"
    assert verdict["degraded"][0]["source"] == "drom"


async def test_verdict_endpoint_marks_deduplicated_cluster(client: AsyncClient) -> None:
    """Цены на площадках приходят с пометкой дедупликации и её объяснением."""
    response = await client.get(f"{CHECKS_URL}/{READY_CHECK_ID}/verdict")

    assert response.status_code == 200
    cluster = response.json()["cluster"]
    assert cluster["deduplicated"] is True
    assert cluster["dedup_note"] == "совпали VIN, фото и телефон"
    assert [listing["source"] for listing in cluster["listings"]] == ["avito", "autoru", "drom"]
    assert cluster["listings"][2]["stale"] is True


async def test_verdict_of_running_check_is_409(
    client: AsyncClient, orchestrator: StubOrchestrator
) -> None:
    """Проверка есть, вердикта ещё нет — это 409, ресурс существует."""
    created = await client.post(CHECKS_URL, json={"plate": VALID_PLATE})

    response = await client.get(f"{CHECKS_URL}/{created.json()['check_id']}/verdict")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "verdict_not_ready"
    assert orchestrator.start_calls


async def test_cancel_check(client: AsyncClient, orchestrator: StubOrchestrator) -> None:
    """«Отменить» на экране B2 — обычный DELETE, 204."""
    created = await client.post(CHECKS_URL, json={"plate": VALID_PLATE})
    check_id = created.json()["check_id"]

    response = await client.delete(f"{CHECKS_URL}/{check_id}")

    assert response.status_code == 204
    assert orchestrator.canceled == [check_id]


def _parse_sse(body: str) -> list[dict[str, object]]:
    """Разобрать тело `text/event-stream` в список кадров."""
    frames: list[dict[str, object]] = []
    for block in body.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        frame: dict[str, object] = {}
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event: "):
                frame["event"] = line.removeprefix("event: ")
            elif line.startswith("id: "):
                frame["id"] = int(line.removeprefix("id: "))
            elif line.startswith("data: "):
                data_lines.append(line.removeprefix("data: "))
        frame["data"] = json.loads("\n".join(data_lines))
        frames.append(frame)
    return frames


async def test_stream_first_frame_has_full_source_list(client: AsyncClient) -> None:
    """Первый кадр несёт полный список источников, формат строк — `data: ` + JSON."""
    response = await client.get(f"{CHECKS_URL}/{READY_CHECK_ID}/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    body = response.text
    assert body.startswith("event: progress\nid: 1\ndata: {")

    frames = _parse_sse(body)
    first = frames[0]
    assert first["event"] == "progress"
    data = first["data"]
    assert isinstance(data, dict)
    sources = data["sources"]
    assert isinstance(sources, list)
    assert [source["code"] for source in sources] == [code for code, _ in SOURCE_TITLES]
    # title приходит с сервера: седьмой источник должен появляться без релиза клиента.
    assert sources[0]["title"] == "ГИБДД · регистрация, ДТП"
    assert data["vehicle"]["title"] == "KIA RIO III"


async def test_stream_delta_frame_is_expanded_to_full_list(client: AsyncClient) -> None:
    """Даже если оркестратор прислал дельту, наружу уходит полный список.

    Это гарантия API, а не оркестратора: клиент никогда не склеивает состояние.
    """
    response = await client.get(f"{CHECKS_URL}/{READY_CHECK_ID}/stream")
    frames = _parse_sse(response.text)

    second = frames[1]["data"]
    assert isinstance(second, dict)
    sources = second["sources"]
    assert isinstance(sources, list)
    assert len(sources) == len(SOURCE_TITLES)
    by_code = {source["code"]: source for source in sources}
    assert by_code["gibdd"]["state"] == "ok"
    assert by_code["autoru"]["state"] == "running"
    # Источник из первого кадра не потерял title при слиянии дельты.
    assert by_code["drom"]["title"] == "Дром · отзывы владельцев"
    assert by_code["drom"]["state"] == "queued"
    # Прогресс пересчитан по полному списку: 3 готовых + половина за running.
    assert second["progress"] == pytest.approx(0.58, abs=0.01)


async def test_stream_ends_with_done_and_coverage(client: AsyncClient) -> None:
    """Последний кадр — `done` с тем же признаком неполноты источников."""
    response = await client.get(f"{CHECKS_URL}/{READY_CHECK_ID}/stream")
    frames = _parse_sse(response.text)

    assert [frame["event"] for frame in frames] == [
        "progress",
        "progress",
        "source_degraded",
        "done",
    ]
    done = frames[-1]["data"]
    assert isinstance(done, dict)
    assert (done["sources_ok"], done["sources_total"]) == (5, 6)
    assert done["verdict_url"] == f"{CHECKS_URL}/{READY_CHECK_ID}/verdict"


async def test_stream_sends_heartbeat_while_sources_are_silent(
    app: FastAPI, orchestrator: StubOrchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Молчащий поток шлёт `: keepalive`, иначе прокси рвут соединение (ТЗ, 7.3)."""
    monkeypatch.setattr(routes_check, "HEARTBEAT_INTERVAL_SEC", 0.01)
    orchestrator.stream_delay_s = 0.08

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get(f"{CHECKS_URL}/{READY_CHECK_ID}/stream")

    assert response.text.startswith(": keepalive\n\n")
    assert _parse_sse(response.text)[0]["event"] == "progress"


class _EmptyVehicleRepository:
    """Справочник, в котором нет ничего: сценарий CC5 «не нашли по номеру»."""

    async def find_vehicle(self, subject: Subject) -> None:
        return None


async def test_unknown_vehicle_is_404_with_alternatives(
    orchestrator: StubOrchestrator,
) -> None:
    """CC5: не нашли авто — предлагаем VIN и фото, а не «ошибка»."""
    app = create_app(orchestrator=orchestrator, vehicle_repository=_EmptyVehicleRepository())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post(CHECKS_URL, json={"plate": VALID_PLATE})

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "vehicle_not_found"
    assert detail["alternatives"] == ["vin", "photo"]
    assert orchestrator.start_calls == []
