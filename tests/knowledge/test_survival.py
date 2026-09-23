"""Тесты функций дожития и условной вероятности отказа (ТЗ, 5.6.4)."""

from __future__ import annotations

import math
from itertools import pairwise

import pytest

from avtoklik.knowledge import (
    Observation,
    conditional_failure_probability,
    empirical_survival,
    weibull_survival,
)
from tests.knowledge.factories import make_defect, make_empirical_defect


def test_weibull_survival_at_zero_is_one() -> None:
    assert weibull_survival(0, shape=2.1, scale=118_000) == 1.0
    assert weibull_survival(0, shape=0.5, scale=50_000) == 1.0


def test_weibull_survival_is_decreasing() -> None:
    values = [weibull_survival(m, shape=2.1, scale=118_000) for m in range(0, 300_001, 25_000)]
    assert all(later < earlier for earlier, later in pairwise(values))
    assert values[-1] > 0.0


def test_weibull_survival_at_scale_is_exp_minus_one() -> None:
    # При shape=1 масштаб — это среднее время до отказа, S(scale) = e^-1.
    assert weibull_survival(100_000, shape=1.0, scale=100_000) == pytest.approx(math.exp(-1))
    # Свойство держится при любом shape: (scale/scale)^k = 1.
    assert weibull_survival(100_000, shape=3.0, scale=100_000) == pytest.approx(math.exp(-1))


def test_weibull_rejects_invalid_arguments() -> None:
    with pytest.raises(ValueError):
        weibull_survival(10_000, shape=0.0, scale=100_000)
    with pytest.raises(ValueError):
        weibull_survival(10_000, shape=1.0, scale=0.0)
    with pytest.raises(ValueError):
        weibull_survival(-1, shape=1.0, scale=100_000)


def test_conditional_probability_grows_with_mileage_for_wearout() -> None:
    # shape > 1 — износ: чем больше пробег, тем выше риск на том же горизонте.
    defect = make_defect(shape=2.5, scale=140_000)
    probabilities = [
        conditional_failure_probability(defect, m, 20_000) for m in range(0, 200_001, 20_000)
    ]
    assert all(later > earlier for earlier, later in pairwise(probabilities))


def test_conditional_probability_is_memoryless_for_shape_one() -> None:
    # shape = 1 — экспоненциальное распределение: условная вероятность на
    # фиксированном горизонте не зависит от текущего пробега. Проверка формулы.
    defect = make_defect(shape=1.0, scale=200_000)
    baseline = conditional_failure_probability(defect, 0, 20_000)
    for mileage in (30_000, 92_000, 250_000):
        assert conditional_failure_probability(defect, mileage, 20_000) == pytest.approx(baseline)
    assert baseline == pytest.approx(1 - math.exp(-20_000 / 200_000))


def test_zero_horizon_gives_zero_probability() -> None:
    defect = make_defect(shape=2.1, scale=118_000)
    assert conditional_failure_probability(defect, 92_000, 0) == 0.0


def test_negative_horizon_rejected() -> None:
    with pytest.raises(ValueError):
        conditional_failure_probability(make_defect(), 92_000, -1)


def test_exhausted_defect_is_excluded() -> None:
    # S(m) ≈ 0: по модели болячка почти наверняка уже наступила — вклада нет.
    defect = make_defect(shape=4.0, scale=50_000)
    assert conditional_failure_probability(defect, 400_000, 20_000) == 0.0


def test_empirical_survival_without_observations_is_one() -> None:
    assert empirical_survival([], 100_000) == 1.0


def test_empirical_survival_is_a_step_function() -> None:
    observations = [
        Observation(mileage_km=60_000, failed=True),
        Observation(mileage_km=80_000, failed=True),
        Observation(mileage_km=100_000, failed=True),
        Observation(mileage_km=200_000, failed=False),
    ]
    assert empirical_survival(observations, 50_000) == 1.0
    # До следующего отказа значение не меняется — ступенька.
    assert empirical_survival(observations, 60_000) == pytest.approx(0.75)
    assert empirical_survival(observations, 79_999) == pytest.approx(0.75)
    assert empirical_survival(observations, 80_000) == pytest.approx(0.5)


def test_censored_observations_lower_the_risk() -> None:
    """Ключевое требование 5.6.4: «проехал 150 т, не ломалось» снижает оценку риска."""
    failures: list[tuple[float, bool]] = [
        (60_000, True),
        (80_000, True),
        (100_000, True),
        (120_000, True),
        (200_000, False),
    ]
    censored = [(150_000.0, False)] * 10

    only_failures = make_empirical_defect(failures)
    with_censored = make_empirical_defect(failures + censored)

    risk_without = conditional_failure_probability(only_failures, 90_000, 20_000)
    risk_with = conditional_failure_probability(with_censored, 90_000, 20_000)

    assert risk_without == pytest.approx(1 / 3)
    assert risk_with < risk_without
    # Без учёта цензурирования модель переоценивает риск в разы.
    assert risk_with == pytest.approx((13 / 15 - 12 / 15) / (13 / 15))


def test_empirical_survival_rejects_negative_mileage() -> None:
    with pytest.raises(ValueError):
        empirical_survival([Observation(mileage_km=10_000, failed=True)], -5)
