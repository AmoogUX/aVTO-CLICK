"""Функции дожития S(m) и условная вероятность отказа на интервале (ТЗ, 5.6.4).

S(m) — доля автомобилей, у которых болячка ещё не проявилась к пробегу m.
Только стандартная библиотека: numpy/scipy в зависимости проекта не входят.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from avtoklik.knowledge.models import (
    Defect,
    EmpiricalParams,
    Observation,
    WeibullParams,
)

__all__ = [
    "SURVIVAL_EPS",
    "conditional_failure_probability",
    "defect_survival",
    "empirical_survival",
    "weibull_survival",
]

# Ниже этого порога считаем, что болячка по модели уже почти наверняка наступила:
# делить на такое S(m) бессмысленно, а сама болячка из суммы исключается (5.6.4).
SURVIVAL_EPS = 1e-6


def weibull_survival(mileage_km: float, shape: float, scale: float) -> float:
    """S(m) = exp(-(m/scale)^shape) — дожитие по Вейбуллу."""
    if shape <= 0 or scale <= 0:
        raise ValueError("shape и scale должны быть положительными")
    if mileage_km < 0:
        raise ValueError("пробег не может быть отрицательным")
    if mileage_km == 0:
        # Отдельной веткой, потому что 0**shape при shape<1 даёт 0, а при
        # отрицательной степени — ошибку; S(0) по определению равно 1.
        return 1.0
    return math.exp(-((mileage_km / scale) ** shape))


def empirical_survival(observations: Sequence[Observation], mileage_km: float) -> float:
    """S(m) по Каплану–Мейеру: ступенчатая оценка без предположения о форме.

    Цензурированные наблюдения («проехал 150 т. км, не ломалось») входят в число
    находящихся под риском и потому снижают оценку риска. Учитывать только отказы
    нельзя — модель решит, что ломается всё и у всех (5.6.4).
    """
    if mileage_km < 0:
        raise ValueError("пробег не может быть отрицательным")
    if not observations:
        # Наблюдений нет — утверждать нечего, риск нулевой.
        return 1.0

    failure_times = sorted({o.mileage_km for o in observations if o.failed})
    survival = 1.0
    for time in failure_times:
        if time > mileage_km:
            break
        # Под риском — все, кто доехал до этого пробега без события:
        # и будущие отказы, и цензурированные с большим пробегом.
        at_risk = sum(1 for o in observations if o.mileage_km >= time)
        failures = sum(1 for o in observations if o.failed and o.mileage_km == time)
        if at_risk == 0:
            break
        survival *= 1.0 - failures / at_risk
    return survival


def defect_survival(defect: Defect, mileage_km: float) -> float:
    """S(m) для болячки по объявленной в каталоге модели отказа."""
    params = defect.hazard_params
    if isinstance(params, WeibullParams):
        return weibull_survival(mileage_km, params.shape, params.scale)
    if isinstance(params, EmpiricalParams):
        return empirical_survival(params.observations, mileage_km)
    raise TypeError(f"неизвестный тип параметров отказа: {type(params)!r}")


def conditional_failure_probability(
    defect: Defect,
    mileage_km: float,
    horizon_km: float,
) -> float:
    """P(m, Δ) = (S(m) − S(m+Δ)) / S(m) — отказ на [m, m+Δ] при условии дожития до m.

    Возвращает 0.0, если S(m) ниже `SURVIVAL_EPS`: такую болячку либо уже устранил
    прошлый владелец, либо она заложена в цену автомобиля, и в сумму она не идёт.
    """
    if horizon_km < 0:
        raise ValueError("горизонт не может быть отрицательным")

    survival_now = defect_survival(defect, mileage_km)
    if survival_now <= SURVIVAL_EPS:
        return 0.0
    survival_then = defect_survival(defect, mileage_km + horizon_km)
    probability = (survival_now - survival_then) / survival_now
    # Зажимаем в 0..1: ступенчатая эмпирическая оценка на равных пробегах
    # может дать микроскопический отрицательный ноль из-за плавающей точки.
    return min(1.0, max(0.0, probability))
