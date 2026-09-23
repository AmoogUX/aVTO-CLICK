"""Тесты сборки вердикта проверки — экран B3."""

from __future__ import annotations

from datetime import date

import pytest

from avtoklik.api.schemas import CheckBlockStatus, DegradedSource, PaywallInfo
from avtoklik.knowledge import DefectRisk, RepairForecast
from avtoklik.service.payloads import (
    AccidentRecord,
    ListingPayload,
    MileageRecord,
    RegistryPayload,
)
from avtoklik.service.verdict import (
    BASE_SCORE,
    HEADLINE_CAUTION,
    HEADLINE_GOOD,
    HEADLINE_STOP,
    MAX_SCORE,
    MIN_SCORE,
    PENALTY_MILEAGE_ROLLBACK,
    ROLLBACK_TOLERANCE_KM,
    build_repair_note,
    build_verdict,
    build_verdict_bundle,
    detect_mileage_rollback,
    score_vehicle,
)
from tests.knowledge.factories import CATALYST, STEERING_RACK, make_defect

TODAY = date(2026, 9, 23)


def _mileage(*points: tuple[date, int]) -> tuple[MileageRecord, ...]:
    return tuple(MileageRecord(recorded_on=day, mileage_km=km) for day, km in points)


HONEST_MILEAGE = _mileage(
    (date(2018, 6, 1), 21_000),
    (date(2020, 5, 1), 48_000),
    (date(2022, 6, 1), 71_000),
    (date(2024, 7, 1), 92_000),
)


def make_registry(**overrides: object) -> RegistryPayload:
    """Эталонная Kia Rio с экрана B3; перекрываем только то, что проверяем."""
    base: dict[str, object] = {
        "vin": "Z94CB41AAER000001",
        "brand": "Kia",
        "model": "Rio",
        "year": 2016,
        "owners_count": 2,
        "mileage_history": HONEST_MILEAGE,
        "accidents": (
            AccidentRecord(
                occurred_on=date(2022, 8, 14),
                severity="лёгкое",
                damage_zone="задний бампер",
            ),
        ),
        "is_pledged": False,
        "is_taxi": False,
        "has_restrictions": False,
    }
    base.update(overrides)
    return RegistryPayload(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 1. Эталонный сценарий из дизайна                                            #
# --------------------------------------------------------------------------- #


def test_reference_kia_rio_matches_screen_b3() -> None:
    """Чистая история, 2 владельца, одно лёгкое ДТП → «можно смотреть»."""
    verdict = build_verdict(make_registry(), sources_ok=6, sources_total=6, today=TODAY)

    # Точное число не подгоняем под макетные 82: важен диапазон и вывод словами.
    assert 75 <= verdict.score <= 90
    assert verdict.headline == HEADLINE_GOOD
    assert verdict.subtext is not None
    assert "лёгкое ДТП в 2022" in verdict.subtext
    assert "стоп-фактор" in verdict.subtext


def test_reference_check_blocks_follow_the_screen() -> None:
    """Четыре блока в порядке экрана, с теми же статусами, что в макете."""
    verdict = build_verdict(make_registry(), sources_ok=6, sources_total=6, today=TODAY)
    blocks = {block.code: block for block in verdict.checks}

    assert [block.code for block in verdict.checks] == ["mileage", "owners", "accidents", "legal"]
    assert blocks["mileage"].status is CheckBlockStatus.OK
    assert blocks["mileage"].value == "92 т. км"
    assert blocks["owners"].title == "2 владельца"
    # Лёгкое ДТП — янтарь, а не красный: дизайн-система бережёт красный для «бежать».
    assert blocks["accidents"].status is CheckBlockStatus.WARN
    assert blocks["accidents"].title == "1 ДТП"
    assert blocks["legal"].status is CheckBlockStatus.OK
    assert blocks["legal"].note == "не числится"


# --------------------------------------------------------------------------- #
# 2. Идеальный автомобиль                                                     #
# --------------------------------------------------------------------------- #


def test_spotless_vehicle_scores_at_the_top() -> None:
    """Один владелец, честный пробег, ни ДТП, ни обременений → 100."""
    registry = make_registry(owners_count=1, accidents=())
    result = score_vehicle(registry, today=TODAY)

    assert result.score == BASE_SCORE
    assert result.factors == ()

    verdict = build_verdict(registry, sources_ok=6, sources_total=6, today=TODAY)
    assert verdict.headline == HEADLINE_GOOD
    assert verdict.subtext is not None
    assert "всё ровно" in verdict.subtext


# --------------------------------------------------------------------------- #
# 3. Стоп-факторы                                                             #
# --------------------------------------------------------------------------- #


def test_pledge_collapses_the_score_regardless_of_the_rest() -> None:
    """Залог блокирует сделку: идеальная во всём остальном машина всё равно «бежать»."""
    registry = make_registry(owners_count=1, accidents=(), is_pledged=True)
    bundle = build_verdict_bundle(registry, sources_ok=6, sources_total=6, today=TODAY)

    assert bundle.verdict.score < 40
    assert bundle.verdict.headline == HEADLINE_STOP
    assert bundle.verdict.subtext is not None
    assert "залог" in bundle.verdict.subtext
    assert [factor.code for factor in bundle.score.blocking_factors] == ["pledge"]

    blocks = {block.code: block for block in bundle.verdict.checks}
    assert blocks["legal"].status is CheckBlockStatus.DANGER


def test_restrictions_are_a_stop_factor_too() -> None:
    """Ограничения ГИБДД: машину не поставить на учёт — тоже стоп."""
    registry = make_registry(owners_count=1, accidents=(), has_restrictions=True)
    bundle = build_verdict_bundle(registry, sources_ok=6, sources_total=6, today=TODAY)

    assert bundle.verdict.score < 40
    assert bundle.verdict.headline == HEADLINE_STOP
    assert [factor.code for factor in bundle.score.blocking_factors] == ["restrictions"]


def test_taxi_is_a_deduction_but_not_a_stop_factor() -> None:
    """Такси — крупная уценка, но сделке не мешает: заголовок не «бежать»."""
    registry = make_registry(owners_count=1, accidents=(), is_taxi=True)
    bundle = build_verdict_bundle(registry, sources_ok=6, sources_total=6, today=TODAY)

    assert bundle.score.blocking_factors == ()
    assert bundle.verdict.score < BASE_SCORE
    assert bundle.verdict.headline in {HEADLINE_GOOD, HEADLINE_CAUTION}
    blocks = {block.code: block for block in bundle.verdict.checks}
    assert blocks["legal"].status is CheckBlockStatus.WARN


# --------------------------------------------------------------------------- #
# 4. Скрутка пробега                                                          #
# --------------------------------------------------------------------------- #


def test_monotonic_history_is_not_a_rollback() -> None:
    assert detect_mileage_rollback(HONEST_MILEAGE) is False


def test_decreasing_history_is_a_rollback() -> None:
    history = _mileage(
        (date(2020, 5, 1), 148_000),
        (date(2022, 6, 1), 171_000),
        (date(2024, 7, 1), 92_000),
    )
    assert detect_mileage_rollback(history) is True


def test_empty_and_single_point_history_are_not_a_rollback() -> None:
    """Нет данных — это не «всё чисто» и не «скрутка»: признаков мы не нашли."""
    assert detect_mileage_rollback(()) is False
    assert detect_mileage_rollback(_mileage((date(2024, 7, 1), 92_000))) is False


def test_history_is_sorted_before_comparison() -> None:
    """Точки приходят из разных источников, порядок во входе не гарантирован."""
    shuffled = _mileage(
        (date(2024, 7, 1), 92_000),
        (date(2018, 6, 1), 21_000),
        (date(2020, 5, 1), 48_000),
    )
    assert detect_mileage_rollback(shuffled) is False


def test_rounding_noise_is_not_a_rollback() -> None:
    """Сервис округлил одометр — это шум записи, а не подкрутка."""
    history = _mileage(
        (date(2024, 1, 1), 92_380),
        (date(2024, 7, 1), 92_380 - ROLLBACK_TOLERANCE_KM + 10),
    )
    assert detect_mileage_rollback(history) is False


def test_return_to_previous_level_is_caught() -> None:
    """После скрутки показания снова растут: сравнение соседей это проглядит."""
    history = _mileage(
        (date(2020, 1, 1), 150_000),
        (date(2022, 1, 1), 60_000),
        (date(2024, 1, 1), 95_000),
    )
    assert detect_mileage_rollback(history) is True


def test_rollback_is_penalised_and_shows_in_the_mileage_block() -> None:
    """Скрутка обесценивает всё, что считается от пробега, — штраф серьёзный."""
    honest = make_registry()
    twisted = make_registry(
        mileage_history=_mileage(
            (date(2020, 5, 1), 148_000),
            (date(2024, 7, 1), 92_000),
        )
    )
    honest_score = score_vehicle(honest, today=TODAY)
    twisted_bundle = build_verdict_bundle(twisted, sources_ok=6, sources_total=6, today=TODAY)

    assert honest_score.score - twisted_bundle.verdict.score == PENALTY_MILEAGE_ROLLBACK
    assert PENALTY_MILEAGE_ROLLBACK >= 30, "скрутка обязана быть серьёзным штрафом"
    assert "mileage_rollback" in {factor.code for factor in twisted_bundle.score.factors}

    blocks = {block.code: block for block in twisted_bundle.verdict.checks}
    assert blocks["mileage"].status is CheckBlockStatus.DANGER
    assert blocks["mileage"].note is not None
    assert "скрут" in blocks["mileage"].note


def test_missing_mileage_history_is_neutral_not_ok() -> None:
    """Пустая история — серый блок «сверьте одометр», а не зелёная галочка."""
    verdict = build_verdict(
        make_registry(mileage_history=()), sources_ok=6, sources_total=6, today=TODAY
    )
    blocks = {block.code: block for block in verdict.checks}
    assert blocks["mileage"].status is CheckBlockStatus.NEUTRAL
    assert blocks["mileage"].value is None


# --------------------------------------------------------------------------- #
# 5. Владельцы                                                                #
# --------------------------------------------------------------------------- #


def test_more_owners_cost_more() -> None:
    scores = [
        score_vehicle(make_registry(owners_count=count, accidents=()), today=TODAY).score
        for count in (1, 2, 3)
    ]
    assert scores[0] > scores[1] > scores[2]


def test_owner_churn_is_penalised_separately() -> None:
    """Четыре владельца за три года — машину перепродают, а не ездят на ней."""
    calm = make_registry(year=2006, owners_count=4, accidents=())
    churned = make_registry(year=2023, owners_count=4, accidents=())

    calm_result = score_vehicle(calm, today=TODAY)
    churned_result = score_vehicle(churned, today=TODAY)

    assert "owner_churn" not in {factor.code for factor in calm_result.factors}
    assert "owner_churn" in {factor.code for factor in churned_result.factors}
    assert churned_result.score < calm_result.score

    blocks = {
        block.code: block
        for block in build_verdict_bundle(
            churned, sources_ok=6, sources_total=6, today=TODAY
        ).verdict.checks
    }
    assert blocks["owners"].status is CheckBlockStatus.WARN


def test_unknown_owners_are_not_penalised() -> None:
    """Неизвестное — не находка: источник не ответил, а не машина плохая."""
    registry = make_registry(owners_count=None, accidents=())
    result = score_vehicle(registry, today=TODAY)
    assert result.score == BASE_SCORE

    blocks = {
        block.code: block
        for block in build_verdict(registry, sources_ok=5, sources_total=6, today=TODAY).checks
    }
    assert blocks["owners"].status is CheckBlockStatus.NEUTRAL


# --------------------------------------------------------------------------- #
# 6. Тяжесть ДТП                                                              #
# --------------------------------------------------------------------------- #


def _with_accident(severity: str) -> RegistryPayload:
    return make_registry(
        accidents=(
            AccidentRecord(occurred_on=date(2022, 8, 14), severity=severity, damage_zone="перед"),
        )
    )


def test_heavier_accident_costs_more_than_a_light_one() -> None:
    light = score_vehicle(_with_accident("лёгкое"), today=TODAY).score
    medium = score_vehicle(_with_accident("среднее"), today=TODAY).score
    heavy = score_vehicle(_with_accident("тяжёлое"), today=TODAY).score

    assert light > medium > heavy


def test_heavy_accident_turns_the_block_red() -> None:
    verdict = build_verdict(_with_accident("тяжёлое"), sources_ok=6, sources_total=6, today=TODAY)
    blocks = {block.code: block for block in verdict.checks}
    assert blocks["accidents"].status is CheckBlockStatus.DANGER
    assert verdict.headline in {HEADLINE_CAUTION, HEADLINE_GOOD}


def test_unknown_severity_is_scored_as_medium() -> None:
    """Тяжесть не указана — считаем по середине: ни занижать, ни пугать зря."""
    unknown = score_vehicle(_with_accident("не указана"), today=TODAY).score
    medium = score_vehicle(_with_accident("среднее"), today=TODAY).score
    assert unknown == medium


def test_no_accidents_block_is_green() -> None:
    verdict = build_verdict(make_registry(accidents=()), sources_ok=6, sources_total=6, today=TODAY)
    blocks = {block.code: block for block in verdict.checks}
    assert blocks["accidents"].status is CheckBlockStatus.OK
    assert blocks["accidents"].title == "ДТП не найдены"


# --------------------------------------------------------------------------- #
# 7. Покрытие источников (CC2)                                                #
# --------------------------------------------------------------------------- #


def test_partial_coverage_still_produces_a_verdict() -> None:
    """Вердикт по 5 из 6 — штатный ответ, но неполноту видно (CC2)."""
    degraded = [
        DegradedSource(
            source="Дром",
            reason="timeout",
            cached_at="2026-09-23T12:40:00Z",
            retry_eta_min=(5, 15),
        )
    ]
    verdict = build_verdict(
        make_registry(),
        sources_ok=5,
        sources_total=6,
        degraded=degraded,
        today=TODAY,
    )

    assert verdict.coverage.sources_ok == 5
    assert verdict.coverage.sources_total == 6
    assert verdict.coverage.complete is False
    assert verdict.coverage.summary == "5 из 6"
    assert verdict.subtext is not None
    # Пейволл не имеет права продавать данные, которых нет.
    assert "5 из 6" in verdict.subtext
    assert [entry.source for entry in verdict.degraded] == ["Дром"]
    assert verdict.headline == HEADLINE_GOOD


def test_full_coverage_does_not_clutter_the_subtext() -> None:
    verdict = build_verdict(make_registry(), sources_ok=6, sources_total=6, today=TODAY)
    assert verdict.coverage.complete is True
    assert verdict.subtext is not None
    assert "из 6" not in verdict.subtext


# --------------------------------------------------------------------------- #
# 8. Объяснимость                                                             #
# --------------------------------------------------------------------------- #


def test_score_is_explained_by_its_factors() -> None:
    """За числом стоит перечень причин, и сумма штрафов сходится с баллом."""
    registry = make_registry(owners_count=3, is_taxi=True)
    result = score_vehicle(registry, today=TODAY)

    assert result.factors, "скоринг без причин — приговор, а не данные"
    assert all(factor.penalty > 0 for factor in result.factors)
    assert all(factor.summary for factor in result.factors)
    assert result.score == BASE_SCORE - result.total_penalty

    codes = [factor.code for factor in result.factors]
    assert len(codes) == len(set(codes)), "каждый фактор упоминается один раз"


def test_factor_codes_cover_every_finding() -> None:
    registry = make_registry(
        year=2021,
        owners_count=4,
        is_taxi=True,
        is_pledged=True,
        has_restrictions=True,
        mileage_history=_mileage((date(2020, 1, 1), 150_000), (date(2024, 1, 1), 90_000)),
    )
    codes = {factor.code for factor in score_vehicle(registry, today=TODAY).factors}
    assert codes == {
        "mileage_rollback",
        "owners",
        "owner_churn",
        "accidents",
        "taxi",
        "pledge",
        "restrictions",
    }


# --------------------------------------------------------------------------- #
# 9. Границы шкалы                                                            #
# --------------------------------------------------------------------------- #


def test_score_never_leaves_the_scale() -> None:
    """Даже нагромождение всех проблем сразу не уводит балл ниже нуля."""
    disaster = make_registry(
        owners_count=12,
        is_taxi=True,
        is_pledged=True,
        has_restrictions=True,
        mileage_history=_mileage((date(2019, 1, 1), 400_000), (date(2024, 1, 1), 90_000)),
        accidents=tuple(
            AccidentRecord(
                occurred_on=date(2019 + i, 3, 1), severity="тяжёлое", damage_zone="перед"
            )
            for i in range(5)
        ),
    )
    result = score_vehicle(disaster, today=TODAY)

    assert result.total_penalty > BASE_SCORE
    assert result.score == MIN_SCORE
    verdict = build_verdict(disaster, sources_ok=1, sources_total=6, today=TODAY)
    assert MIN_SCORE <= verdict.score <= MAX_SCORE
    assert verdict.headline == HEADLINE_STOP


def test_score_never_exceeds_one_hundred() -> None:
    empty = RegistryPayload()
    assert score_vehicle(empty, today=TODAY).score == MAX_SCORE
    verdict = build_verdict(empty, sources_ok=1, sources_total=6, today=TODAY)
    assert verdict.score <= MAX_SCORE
    blocks = {block.code: block for block in verdict.checks}
    # Ничего не проверили — значит серый, а не зелёный: «чисто» мы не утверждали.
    assert blocks["legal"].status is CheckBlockStatus.NEUTRAL


# --------------------------------------------------------------------------- #
# Кластер площадок                                                            #
# --------------------------------------------------------------------------- #


def _listings() -> list[ListingPayload]:
    return [
        ListingPayload(
            listing_id="a",
            platform="Авито",
            price=785_000,
            published_on=date(2026, 9, 23),
        ),
        ListingPayload(
            listing_id="b",
            platform="Авто.ру",
            price=799_000,
            published_on=date(2026, 9, 20),
        ),
        ListingPayload(
            listing_id="c",
            platform="Дром",
            price=810_000,
            published_on=date(2026, 9, 15),
        ),
    ]


def test_cluster_shows_prices_spread_and_reasons() -> None:
    """«Это авто на площадках · разница 25 000 ₽» с объяснимой склейкой."""
    verdict = build_verdict(
        make_registry(),
        sources_ok=6,
        sources_total=6,
        listings=_listings(),
        match_reasons=["vin", "photo_phash", "phone"],
        cluster_id="cl-1",
        today=TODAY,
    )

    cluster = verdict.cluster
    assert cluster is not None
    assert cluster.id == "cl-1"
    assert cluster.spread_rub == 25_000
    assert cluster.deduplicated is True
    assert cluster.dedup_note == "совпали VIN, фото и телефон"
    assert [price.price_rub for price in cluster.listings] == [785_000, 799_000, 810_000]
    assert [price.seen for price in cluster.listings] == ["сегодня", "3 дня", "8 дней"]


def test_cluster_marks_degraded_platform_price_as_stale() -> None:
    """Площадка отдала кэш — цена помечена, чтобы на экране была плашка «12:40»."""
    verdict = build_verdict(
        make_registry(),
        sources_ok=5,
        sources_total=6,
        listings=_listings(),
        match_reasons=["vin"],
        degraded=[
            DegradedSource(source="дром", reason="timeout", cached_at="2026-09-23T12:40:00Z")
        ],
        today=TODAY,
    )
    cluster = verdict.cluster
    assert cluster is not None
    stale = {price.source: price for price in cluster.listings}
    assert stale["Дром"].stale is True
    assert stale["Дром"].cached_at == "2026-09-23T12:40:00Z"
    assert stale["Авито"].stale is False


def test_without_listings_there_is_no_cluster() -> None:
    verdict = build_verdict(make_registry(), sources_ok=5, sources_total=6, today=TODAY)
    assert verdict.cluster is None


# --------------------------------------------------------------------------- #
# Прогноз ремонта                                                             #
# --------------------------------------------------------------------------- #


def _forecast(*, calibrated: bool) -> RepairForecast:
    defects = (
        make_defect(defect_id=1, node=STEERING_RACK, prevalence=0.4 if calibrated else None),
        make_defect(defect_id=2, node=CATALYST, prevalence=0.2 if calibrated else None),
    )
    return RepairForecast(
        expected=34_000.0,
        low=21_000.0,
        high=52_000.0,
        top_contributors=(
            DefectRisk(
                defect=defects[0],
                failure_probability=0.34,
                expected_cost=60_000.0,
                contribution=1.0,
            ),
            DefectRisk(
                defect=defects[1],
                failure_probability=0.05,
                expected_cost=40_000.0,
                contribution=1.0,
            ),
        ),
        confidence=3,
        prevalence_calibrated=calibrated,
    )


def test_calibrated_forecast_may_show_percentages() -> None:
    note = build_repair_note(_forecast(calibrated=True))
    assert note.calibrated is True
    assert note.amount_rub == 34_000
    assert "34 000 ₽" in note.headline
    assert "20 т. км" in note.headline
    assert any("34 %" in line for line in note.lines)


def test_uncalibrated_forecast_hides_percentages_and_calls_it_an_upper_bound() -> None:
    """Некалиброванная встречаемость завышает риск (5.6.1) — проценты запрещены."""
    note = build_repair_note(_forecast(calibrated=False))
    assert note.calibrated is False
    assert "%" not in note.headline
    assert all("%" not in line for line in note.lines)
    assert "не больше" in note.headline
    assert {"часто", "редко"} <= {line.split(" — ")[1].split(",")[0] for line in note.lines}


def test_forecast_travels_in_the_bundle() -> None:
    """Прогноз отдаётся рядом с вердиктом, а не внутри него.

    В схеме `CheckVerdict` поле `repair` теперь есть, но заполняет его
    оркестратор: сборка вердикта не знает ни каталога болячек, ни пробега
    из объявлений, поэтому считать прогноз здесь было бы не на чем.
    Разделение сохраняется сознательно — это граница ответственности,
    а не пережиток отсутствующего поля.
    """
    bundle = build_verdict_bundle(
        make_registry(),
        sources_ok=6,
        sources_total=6,
        repair_forecast=_forecast(calibrated=True),
        today=TODAY,
    )
    assert bundle.repair is not None
    assert bundle.repair.amount_rub == 34_000
    # Поле в схеме существует, но эта функция его не заполняет.
    assert bundle.verdict.repair is None


def test_bundle_without_forecast_has_none() -> None:
    bundle = build_verdict_bundle(make_registry(), sources_ok=6, sources_total=6, today=TODAY)
    assert bundle.repair is None


# --------------------------------------------------------------------------- #
# Пейволл и прочее                                                            #
# --------------------------------------------------------------------------- #


def test_paywall_is_passed_through() -> None:
    paywall = PaywallInfo(report_id="r-1", price_rub=199, preview_blocks=["История пробега"])
    verdict = build_verdict(
        make_registry(), sources_ok=6, sources_total=6, paywall=paywall, today=TODAY
    )
    assert verdict.paywall is not None
    assert verdict.paywall.price_rub == 199


def test_coverage_rejects_impossible_numbers() -> None:
    with pytest.raises(ValueError):
        build_verdict(make_registry(), sources_ok=7, sources_total=6, today=TODAY)
