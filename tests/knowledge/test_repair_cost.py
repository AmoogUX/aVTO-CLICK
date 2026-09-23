"""Тесты расчёта ожидаемых затрат на ремонт под пробег (ТЗ, 5.6.4)."""

from __future__ import annotations

import pytest

from avtoklik.knowledge import (
    RemedyOption,
    expected_remedy_cost,
    expected_repair_cost,
)
from tests.knowledge.factories import (
    CATALYST,
    STEERING_RACK,
    THERMOSTAT,
    make_defect,
    make_remedy,
)


def test_expected_remedy_cost_single_remedy() -> None:
    remedy = make_remedy(parts_min=10_000, parts_max=20_000, labor_hours=2.0, labor_rate=1_500)
    expected, low, high = expected_remedy_cost([remedy])
    assert low == pytest.approx(13_000)
    assert high == pytest.approx(23_000)
    assert expected == pytest.approx(18_000)


def test_expected_remedy_cost_mixes_shares() -> None:
    remedies = [
        make_remedy(remedy="подтяжка", share=0.5, parts_min=0, parts_max=0, labor_hours=1.0),
        make_remedy(remedy="замена", share=0.5, parts_min=10_000, parts_max=10_000),
    ]
    expected, low, high = expected_remedy_cost(remedies)
    # 0.5 × 1 500 + 0.5 × (10 000 + 3 000)
    assert expected == pytest.approx(7_250)
    assert low == pytest.approx(expected)
    assert high == pytest.approx(expected)


def test_expected_remedy_cost_region_rate_overrides_price_list() -> None:
    remedy = make_remedy(parts_min=1_000, parts_max=1_000, labor_hours=2.0, labor_rate=1_500)
    expected, _, _ = expected_remedy_cost([remedy], region_labor_rate=3_000)
    assert expected == pytest.approx(7_000)


def test_expected_remedy_cost_without_remedies_is_zero() -> None:
    assert expected_remedy_cost([]) == (0.0, 0.0, 0.0)


def test_contributions_sum_to_expected() -> None:
    defects = [
        make_defect(defect_id=1, node=STEERING_RACK, shape=2.1, scale=118_000, prevalence=0.4),
        make_defect(defect_id=2, node=CATALYST, shape=3.0, scale=160_000, prevalence=0.2),
        make_defect(defect_id=3, node=THERMOSTAT, shape=1.0, scale=220_000, prevalence=0.3),
    ]
    remedies = {d.id: [make_remedy()] for d in defects}
    forecast = expected_repair_cost(92_000, defects, remedies)

    assert sum(risk.contribution for risk in forecast.top_contributors) == pytest.approx(
        forecast.expected
    )
    assert forecast.low < forecast.expected < forecast.high


def test_zero_prevalence_contributes_nothing() -> None:
    defect = make_defect(defect_id=7, prevalence=0.0)
    forecast = expected_repair_cost(92_000, [defect], {7: [make_remedy()]})
    assert forecast.expected == 0.0
    assert forecast.top_contributors == ()
    assert forecast.prevalence_calibrated is True


def test_low_confidence_defect_is_excluded() -> None:
    weak = make_defect(defect_id=1, confidence=1, prevalence=1.0)
    strong = make_defect(defect_id=2, confidence=3, prevalence=1.0)
    remedies: dict[int, list[RemedyOption]] = {1: [make_remedy()], 2: [make_remedy()]}

    only_weak = expected_repair_cost(92_000, [weak], remedies)
    assert only_weak.expected == 0.0
    assert only_weak.confidence == 0

    both = expected_repair_cost(92_000, [weak, strong], remedies)
    only_strong = expected_repair_cost(92_000, [strong], remedies)
    assert both.expected == pytest.approx(only_strong.expected)
    assert [r.defect.id for r in both.top_contributors] == [2]
    assert both.confidence == 3


def test_uncalibrated_prevalence_is_flagged() -> None:
    # Без калибровки проценты встречаемости показывать нельзя (5.6.4),
    # но болячка всё равно учитывается — как верхняя оценка риска.
    uncalibrated = make_defect(defect_id=1, prevalence=None)
    calibrated = make_defect(defect_id=1, prevalence=1.0)
    remedies = {1: [make_remedy()]}

    forecast = expected_repair_cost(92_000, [uncalibrated], remedies)
    assert forecast.prevalence_calibrated is False
    assert forecast.expected == pytest.approx(
        expected_repair_cost(92_000, [calibrated], remedies).expected
    )


def test_defect_without_remedies_costs_nothing() -> None:
    # Прайса на болячку ещё нет — стоимость нулевая, падать расчёт не должен.
    forecast = expected_repair_cost(92_000, [make_defect(defect_id=5)], {})
    assert forecast.expected == 0.0


def test_top_contributors_are_capped_and_sorted() -> None:
    defects = [
        make_defect(defect_id=i, prevalence=0.1 * i, scale=100_000 + 1_000 * i) for i in range(1, 9)
    ]
    remedies = {d.id: [make_remedy()] for d in defects}
    forecast = expected_repair_cost(92_000, defects, remedies)

    contributions = [risk.contribution for risk in forecast.top_contributors]
    assert len(contributions) == 5
    assert contributions == sorted(contributions, reverse=True)
    # В разбор попадает только верхушка, а `expected` считается по всем болячкам.
    assert forecast.expected > sum(contributions)


def test_empty_defect_list() -> None:
    forecast = expected_repair_cost(92_000, [], {})
    assert forecast.expected == 0.0
    assert forecast.low == 0.0
    assert forecast.high == 0.0
    assert forecast.top_contributors == ()
    assert forecast.confidence == 0
    assert forecast.prevalence_calibrated is True


def test_zero_horizon_gives_zero_forecast() -> None:
    defects = [make_defect(defect_id=1, prevalence=1.0)]
    forecast = expected_repair_cost(92_000, defects, {1: [make_remedy()]}, horizon_km=0)
    assert forecast.expected == 0.0
    assert forecast.top_contributors == ()


def test_zero_mileage_is_the_lowest_risk_for_wearout() -> None:
    defects = [make_defect(defect_id=1, shape=2.5, scale=140_000, prevalence=1.0)]
    remedies = {1: [make_remedy()]}
    new_car = expected_repair_cost(0, defects, remedies)
    used_car = expected_repair_cost(92_000, defects, remedies)
    assert 0.0 < new_car.expected < used_car.expected


def test_negative_arguments_rejected() -> None:
    with pytest.raises(ValueError):
        expected_repair_cost(-1, [], {})
    with pytest.raises(ValueError):
        expected_repair_cost(92_000, [], {}, horizon_km=-1)


def test_kia_rio_scenario() -> None:
    """Сценарий из дизайна C2: Kia Rio, 92 000 км, горизонт 20 000 км."""
    rack = make_defect(
        defect_id=1,
        node=STEERING_RACK,
        shape=2.1,
        scale=118_000,
        prevalence=0.22,
        confidence=3,
    )
    catalyst = make_defect(
        defect_id=2,
        node=CATALYST,
        shape=3.0,
        scale=160_000,
        prevalence=0.15,
        confidence=3,
    )
    thermostat = make_defect(
        defect_id=3,
        node=THERMOSTAT,
        shape=1.0,
        scale=220_000,
        prevalence=0.30,
        confidence=2,
    )
    remedies: dict[int, list[RemedyOption]] = {
        1: [
            make_remedy(
                remedy="подтяжка",
                share=0.5,
                parts_min=500,
                parts_max=1_500,
                labor_hours=1.0,
                labor_rate=1_200,
            ),
            make_remedy(
                remedy="ремкомплект",
                share=0.3,
                parts_min=3_000,
                parts_max=6_000,
                labor_hours=2.5,
                labor_rate=1_200,
            ),
            make_remedy(
                remedy="замена в сборе",
                share=0.2,
                parts_min=12_000,
                parts_max=28_000,
                labor_hours=3.0,
                labor_rate=1_200,
            ),
        ],
        2: [
            make_remedy(
                remedy="замена катализатора",
                share=1.0,
                parts_min=18_000,
                parts_max=45_000,
                labor_hours=2.0,
                labor_rate=1_200,
            )
        ],
        3: [
            make_remedy(
                remedy="замена термостата",
                share=1.0,
                parts_min=1_500,
                parts_max=3_500,
                labor_hours=1.5,
                labor_rate=1_200,
            )
        ],
    }

    forecast = expected_repair_cost(
        vehicle_mileage_km=92_000,
        defects=[rack, catalyst, thermostat],
        remedies_by_defect=remedies,
        horizon_km=20_000,
    )

    # Сумма правдоподобна: не ноль и не «весь ремонт машины сразу».
    assert 500 < forecast.expected < 5_000
    assert forecast.low < forecast.expected < forecast.high
    # Основной риск — катализатор: реже рейки, но дороже её в разы.
    assert [risk.defect.node.code for risk in forecast.top_contributors] == [
        "catalytic_converter",
        "steering_rack",
        "thermostat",
    ]
    assert forecast.prevalence_calibrated is True
    # confidence прогноза — минимум по показываемым болячкам (термостат, 2).
    assert forecast.confidence == 2
    assert all(0.0 < risk.failure_probability < 1.0 for risk in forecast.top_contributors)
