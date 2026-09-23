"""Заглушки зависимостей API и общие фикстуры.

Тесты слоя API не должны знать про сеть, Postgres и площадки: контракт с
нижними модулями описан протоколами (:mod:`avtoklik.api.deps`), а здесь лежит
их минимальная реализация в памяти. Если тест API падает, виноват API.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from avtoklik.api import create_app
from avtoklik.api.deps import SseEvent
from avtoklik.api.schemas import (
    CheckBlock,
    CheckBlockStatus,
    CheckStatus,
    CheckStatusResponse,
    CheckVerdict,
    DegradedSource,
    DoneFrame,
    ListingCluster,
    ListingPrice,
    PaywallInfo,
    ProgressFrame,
    SourceDegradedFrame,
    SourcesCoverage,
    SourceState,
    SourceStatus,
    StartedCheck,
    Subject,
    VehicleBrief,
)

READY_CHECK_ID = "chk-ready"

# Шесть источников экрана B2 — ровно те, что в контракте 7.3.
SOURCE_TITLES: list[tuple[str, str]] = [
    ("gibdd", "ГИБДД · регистрация, ДТП"),
    ("nomerogram", "Номерограм · история фото"),
    ("avito", "Авито · объявления"),
    ("autoru", "Авто.ру · история цены"),
    ("drom", "Дром · отзывы владельцев"),
    ("social", "Соцсети · борды VK/TG"),
]


def _all_sources(state: SourceState = SourceState.QUEUED) -> list[SourceStatus]:
    return [
        SourceStatus(code=code, title=title, state=state, order=index)
        for index, (code, title) in enumerate(SOURCE_TITLES, start=1)
    ]


def ready_verdict() -> CheckVerdict:
    """Вердикт сценария CC2: 5 источников из 6, Дром отдал кэш."""
    return CheckVerdict(
        score=82,
        headline="Можно смотреть",
        subtext="Серьёзных стоп-факторов не нашли. Один нюанс — лёгкое ДТП в 2022.",
        checks=[
            CheckBlock(
                code="mileage",
                status=CheckBlockStatus.OK,
                title="Пробег",
                value="92 т. км",
                note="растёт равномерно",
            ),
            CheckBlock(
                code="owners",
                status=CheckBlockStatus.NEUTRAL,
                title="2 владельца",
                note="ПТС оригинал · 3,5 + 4 года",
            ),
            CheckBlock(
                code="accidents",
                status=CheckBlockStatus.WARN,
                title="1 ДТП",
                note="Лёгкое, зад 2022 · окрас крышки",
            ),
            CheckBlock(
                code="legal",
                status=CheckBlockStatus.OK,
                title="Залог · такси",
                note="не числится",
            ),
        ],
        cluster=ListingCluster(
            id="cluster-1",
            spread_rub=25000,
            match_reasons=["vin", "photo_phash", "phone"],
            listings=[
                ListingPrice(source="avito", price_rub=785000, seen="today"),
                ListingPrice(source="autoru", price_rub=799000, seen="3d"),
                ListingPrice(
                    source="drom",
                    price_rub=810000,
                    seen="8d",
                    stale=True,
                    cached_at="2026-09-21T12:40:00Z",
                ),
            ],
        ),
        degraded=[
            DegradedSource(
                source="drom",
                reason="timeout",
                cached_at="2026-09-21T12:40:00Z",
                message="Дром не отвечает. Показываем его данные от 12:40.",
                retry_eta_min=(5, 15),
            )
        ],
        coverage=SourcesCoverage(sources_ok=5, sources_total=6),
        paywall=PaywallInfo(report_id="rep-1", price_rub=199, preview_blocks=["mileage"]),
    )


class StubOrchestrator:
    """Оркестратор в памяти: считает вызовы и отдаёт заранее собранные состояния."""

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.start_calls: list[Subject] = []
        self.canceled: list[str] = []
        self.stream_delay_s: float = 0.0
        self.checks: dict[str, CheckStatusResponse] = {
            READY_CHECK_ID: CheckStatusResponse(
                check_id=READY_CHECK_ID,
                status=CheckStatus.PARTIAL,
                elapsed_ms=38_400,
                vehicle=VehicleBrief(title="KIA RIO III", year=2017),
                sources=[
                    *_all_sources(SourceState.OK)[:4],
                    SourceStatus(
                        code="drom",
                        title="Дром · отзывы владельцев",
                        state=SourceState.FROM_CACHE,
                        order=5,
                        cache_age_s=3600,
                    ),
                    SourceStatus(
                        code="social",
                        title="Соцсети · борды VK/TG",
                        state=SourceState.TIMEOUT,
                        order=6,
                    ),
                ],
                verdict=ready_verdict(),
            )
        }
        self.verdicts: dict[str, CheckVerdict] = {READY_CHECK_ID: ready_verdict()}

    async def start_check(self, subject: Subject) -> StartedCheck:
        self.start_calls.append(subject)
        check_id = f"chk-{next(self._ids)}"
        self.checks[check_id] = CheckStatusResponse(
            check_id=check_id,
            status=CheckStatus.QUEUED,
            sources=_all_sources(),
        )
        return StartedCheck(check_id=check_id, eta_sec=40, status=CheckStatus.QUEUED)

    async def get_check(self, check_id: str) -> CheckStatusResponse | None:
        return self.checks.get(check_id)

    async def get_verdict(self, check_id: str) -> CheckVerdict | None:
        return self.verdicts.get(check_id)

    async def cancel_check(self, check_id: str) -> bool:
        if check_id not in self.checks:
            return False
        self.canceled.append(check_id)
        return True

    async def stream(
        self, check_id: str, last_event_id: int | None = None
    ) -> AsyncIterator[SseEvent]:
        """Поток из спеки 7.3: полный кадр, затем дельта, деградация и done.

        Второй кадр намеренно дельта — так проверяется, что полный список
        источников гарантирует именно API, а не добросовестность оркестратора.
        """
        if self.stream_delay_s:
            await asyncio.sleep(self.stream_delay_s)
        yield SseEvent(
            event="progress",
            id=1,
            data=ProgressFrame(
                status=CheckStatus.RUNNING,
                elapsed_ms=900,
                vehicle=VehicleBrief(title="KIA RIO III", year=2017),
                sources=[
                    SourceStatus(
                        code="gibdd",
                        title="ГИБДД · регистрация, ДТП",
                        state=SourceState.RUNNING,
                        order=1,
                    ),
                    *_all_sources()[1:],
                ],
            ),
        )
        yield SseEvent(
            event="progress",
            id=4,
            data=ProgressFrame(
                status=CheckStatus.RUNNING,
                elapsed_ms=18_400,
                sources=[
                    SourceStatus(code="gibdd", state=SourceState.OK),
                    SourceStatus(code="nomerogram", state=SourceState.OK),
                    SourceStatus(code="avito", state=SourceState.OK),
                    SourceStatus(code="autoru", state=SourceState.RUNNING),
                ],
            ),
        )
        yield SseEvent(
            event="source_degraded",
            id=6,
            data=SourceDegradedFrame(
                code="drom",
                reason="timeout",
                cached_at="2026-09-21T12:40:00Z",
                message="Дром не отвечает. Показываем его данные от 12:40.",
                retry_eta_min=(5, 15),
            ),
        )
        yield SseEvent(
            event="done",
            id=7,
            data=DoneFrame(
                status=CheckStatus.PARTIAL,
                verdict_url=f"/api/v1/checks/{check_id}/verdict",
                sources_ok=5,
                sources_total=6,
            ),
        )


@pytest.fixture
def orchestrator() -> StubOrchestrator:
    """Заглушка оркестратора, доступная тесту для проверки числа вызовов."""
    return StubOrchestrator()


@pytest.fixture
def app(orchestrator: StubOrchestrator) -> FastAPI:
    """Приложение с внедрённой заглушкой оркестратора."""
    return create_app(orchestrator=orchestrator)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """HTTP-клиент поверх ASGI-приложения: без сокетов и без реального сервера."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
