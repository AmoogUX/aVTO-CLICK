"""Сквозные тесты проверки: госномер → источники → вердикт (B1 → B2 → B3).

Эти тесты отличаются от модульных тем, что гоняют настоящую связку четырёх
модулей, а не заглушки: сбор, дедупликацию, расчёт ремонта и сборку вердикта.
Именно здесь ловятся расхождения контрактов, которые модульные тесты каждого
модуля по отдельности пропускают.
"""

from __future__ import annotations

import asyncio

import pytest

from avtoklik.api.schemas import CheckVerdict, Subject
from avtoklik.collector.base import SourceStatus
from avtoklik.service.orchestrator import CheckService
from avtoklik.service.sources import default_sources

RIO = "К999КК799"
SOLARIS = "У777МН178"


async def run_to_end(service: CheckService, plate: str) -> tuple[object, CheckVerdict | None]:
    """Прогнать проверку до конца и вернуть состояние и вердикт."""
    started = await service.start_check(Subject(type="plate", value=plate))
    status = await service.get_check(started.check_id)
    assert status is not None
    for _ in range(500):
        if status.status not in ("queued", "running"):
            break
        await asyncio.sleep(0.01)
        status = await service.get_check(started.check_id)
        assert status is not None
    return status, await service.get_verdict(started.check_id)


class TestReferenceScenario:
    """Эталонный автомобиль дизайна: Kia Rio III, экран B3."""

    async def test_verdict_is_positive(self) -> None:
        _, verdict = await run_to_end(CheckService(), RIO)
        assert verdict is not None
        # Дизайн показывает 82; точное число зависит от весов скоринга, важна зона.
        assert 75 <= verdict.score <= 90
        assert verdict.headline == "Можно смотреть"

    async def test_all_six_sources_answer(self) -> None:
        status, _ = await run_to_end(CheckService(), RIO)
        assert status.progress == pytest.approx(1.0)
        assert len(status.sources) == 6

    async def test_vin_comes_from_nomerogram(self) -> None:
        """Регистрационный источник VIN не отдаёт — цепочка госномер → VIN держится
        на Номерограме, и её обрыв ломает весь флоу."""
        status, _ = await run_to_end(CheckService(), RIO)
        assert status.vehicle is not None
        assert status.vehicle.vin == "Z94C241BBHR123456"

    async def test_three_platforms_glued_with_design_spread(self) -> None:
        """Три объявления склеиваются в одно авто с разбросом 25 000 ₽ — число
        из дизайна, оно же главный видимый дифференциатор продукта."""
        _, verdict = await run_to_end(CheckService(), RIO)
        assert verdict is not None
        assert verdict.cluster is not None
        assert len(verdict.cluster.listings) == 3
        assert verdict.cluster.spread_rub == 25_000
        assert verdict.cluster.deduplicated is True

    async def test_dedup_note_has_no_raw_codes(self) -> None:
        """Подпись склейки — текст для пользователя: коды признаков в неё
        просачиваться не должны."""
        _, verdict = await run_to_end(CheckService(), RIO)
        assert verdict is not None
        assert verdict.cluster is not None
        note = verdict.cluster.dedup_note
        assert note is not None
        for code in ("vin", "attrs", "photo_phash", "phone", "plate"):
            assert code not in note

    async def test_coverage_is_complete(self) -> None:
        _, verdict = await run_to_end(CheckService(), RIO)
        assert verdict is not None
        assert verdict.coverage.complete is True
        assert verdict.coverage.summary == "6 из 6"


class TestRepairForecast:
    """Расчёт затрат на ремонт под пробег — требование заказчика (5.6.4)."""

    async def test_forecast_is_present_and_bounded(self) -> None:
        _, verdict = await run_to_end(CheckService(), RIO)
        assert verdict is not None
        assert verdict.repair is not None
        repair = verdict.repair
        assert repair.amount_rub > 0
        assert repair.low_rub <= repair.amount_rub <= repair.high_rub
        assert repair.horizon_km == 20_000

    async def test_uncalibrated_forecast_shows_no_percentages(self) -> None:
        """Пока встречаемость не откалибрована по данным СТО, проценты
        показывать нельзя — это прямое требование 5.6.4."""
        _, verdict = await run_to_end(CheckService(), RIO)
        assert verdict is not None
        assert verdict.repair is not None
        assert verdict.repair.calibrated is False
        assert "%" not in " ".join(verdict.repair.lines)
        assert "%" not in verdict.repair.headline

    async def test_forecast_explains_its_composition(self) -> None:
        """Сумма без разбора бесполезна: пользователю нужно знать, из чего она."""
        _, verdict = await run_to_end(CheckService(), RIO)
        assert verdict is not None
        assert verdict.repair is not None
        assert verdict.repair.lines


class TestProblemVehicle:
    """Автомобиль с проблемами: скрутка, тяжёлое ДТП, залог."""

    async def test_blocking_factors_sink_the_verdict(self) -> None:
        _, verdict = await run_to_end(CheckService(), SOLARIS)
        assert verdict is not None
        assert verdict.score < 40
        assert verdict.headline == "Лучше не связываться"

    async def test_rollback_is_reported_in_mileage_block(self) -> None:
        _, verdict = await run_to_end(CheckService(), SOLARIS)
        assert verdict is not None
        mileage = next(b for b in verdict.checks if b.code == "mileage")
        assert mileage.status == "danger"

    async def test_no_repair_forecast_for_unknown_model(self) -> None:
        """По модели без каталога болячек блок не рисуется — это честнее нулей."""
        _, verdict = await run_to_end(CheckService(), SOLARIS)
        assert verdict is not None
        assert verdict.repair is None


class TestDegradation:
    """Деградация источников — состояние, а не исключение (5.1, CC2)."""

    async def test_optional_source_failure_still_gives_verdict(self) -> None:
        adapters = default_sources()
        for adapter in adapters:
            if adapter.source_id == "drom":
                adapter.fail = True  # type: ignore[attr-defined]
        _, verdict = await run_to_end(CheckService(adapters), RIO)
        assert verdict is not None
        assert verdict.coverage.complete is False
        assert verdict.coverage.summary == "5 из 6"
        assert verdict.degraded

    async def test_required_source_failure_withholds_verdict(self) -> None:
        """Без обязательного источника вердикта не бывает — правило продукта."""
        adapters = default_sources()
        for adapter in adapters:
            if adapter.source_id == "gibdd":
                adapter.fail = True  # type: ignore[attr-defined]
        status, verdict = await run_to_end(CheckService(adapters), RIO)
        assert verdict is None
        assert status.status == "failed"

    async def test_failed_source_counts_as_settled(self) -> None:
        """Полоса прогресса не должна застревать из-за лежащего источника."""
        adapters = default_sources()
        for adapter in adapters:
            if adapter.source_id == "social":
                adapter.fail = True  # type: ignore[attr-defined]
        status, _ = await run_to_end(CheckService(adapters), RIO)
        assert status.progress == pytest.approx(1.0)
        states = {s.code: s.state for s in status.sources}
        assert states["social"] == SourceStatus.FAILED.value or states["social"] == "error"


class TestLifecycle:
    """Жизненный цикл проверки."""

    async def test_unknown_check_is_none(self) -> None:
        service = CheckService()
        assert await service.get_check("нет-такой") is None
        assert await service.get_verdict("нет-такой") is None

    async def test_cancel_unknown_returns_false(self) -> None:
        assert await CheckService().cancel_check("нет-такой") is False

    async def test_stream_terminates(self) -> None:
        """Поток обязан завершаться, иначе соединение висит до таймаута прокси."""
        service = CheckService()
        started = await service.start_check(Subject(type="plate", value=RIO))
        events = [event async for event in service.stream(started.check_id)]
        assert events
        assert events[-1].event in ("done", "error")
