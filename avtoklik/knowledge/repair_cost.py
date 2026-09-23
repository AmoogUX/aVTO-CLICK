"""Расчёт ожидаемых затрат на ремонт под пробег (ТЗ, 5.6.4).

E[затраты] = Σ_d P_d(m, Δ) × prevalence_d × C_d,
C_d = Σ_remedy share × (parts + labor_hours × labor_rate).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from avtoklik.knowledge.models import (
    Defect,
    DefectRisk,
    RemedyOption,
    RepairForecast,
)
from avtoklik.knowledge.survival import conditional_failure_probability

__all__ = [
    "DEFAULT_HORIZON_KM",
    "MIN_DISPLAY_CONFIDENCE",
    "TOP_CONTRIBUTORS",
    "expected_remedy_cost",
    "expected_repair_cost",
]

# Горизонт из дизайна экрана C2: «риск ремонтов на ближайшие 20 т. км».
DEFAULT_HORIZON_KM = 20_000.0
# confidence == 1 не показываем вовсе (5.6.3): за болячкой стоит слишком мало наблюдений.
MIN_DISPLAY_CONFIDENCE = 2
# Пользователю нужно знать, из чего сложилась сумма, а не только её саму.
TOP_CONTRIBUTORS = 5


def expected_remedy_cost(
    remedies: Sequence[RemedyOption],
    region_labor_rate: float | None = None,
) -> tuple[float, float, float]:
    """C_d — ожидаемая стоимость устранения и её границы: (ожидание, минимум, максимум).

    Границы берутся из вилки «аналог — оригинал»: `parts_min` даёт нижнюю оценку,
    `parts_max` — верхнюю, ожидание — середина вилки. `region_labor_rate`
    перекрывает ставку нормо-часа из прайса, когда известен регион пользователя.
    """
    if not remedies:
        return 0.0, 0.0, 0.0

    total_share = sum(r.share for r in remedies)
    if total_share <= 0:
        return 0.0, 0.0, 0.0

    expected = low = high = 0.0
    for remedy in remedies:
        # Нормируем доли: набор способов устранения в прайсе может быть неполным,
        # и без нормировки неучтённый способ молча обнулял бы часть стоимости.
        weight = remedy.share / total_share
        rate = region_labor_rate if region_labor_rate is not None else remedy.labor_rate
        labor = remedy.labor_hours * rate
        low += weight * (remedy.parts_min + labor)
        high += weight * (remedy.parts_max + labor)
        expected += weight * ((remedy.parts_min + remedy.parts_max) / 2.0 + labor)
    return expected, low, high


def expected_repair_cost(
    vehicle_mileage_km: float,
    defects: Sequence[Defect],
    remedies_by_defect: Mapping[int, Sequence[RemedyOption]],
    horizon_km: float = DEFAULT_HORIZON_KM,
    region_labor_rate: float | None = None,
) -> RepairForecast:
    """Ожидаемые затраты на ремонт на ближайшие `horizon_km`. Экран C2.

    Складываются только болячки, которые к текущему пробегу ещё могли не наступить.
    Болячки с `confidence == 1` отбрасываются: показывать их нельзя, значит и в
    сумму они не идут. Если хотя бы у одной болячки `prevalence is None`, результат
    помечается `prevalence_calibrated=False` — проценты встречаемости в UI запрещены.
    """
    if vehicle_mileage_km < 0:
        raise ValueError("пробег автомобиля не может быть отрицательным")
    if horizon_km < 0:
        raise ValueError("горизонт не может быть отрицательным")

    shown = [d for d in defects if d.confidence >= MIN_DISPLAY_CONFIDENCE]

    expected_total = low_total = high_total = 0.0
    prevalence_calibrated = True
    breakdown: list[DefectRisk] = []

    for defect in shown:
        if defect.prevalence is None:
            # Некалиброванную болячку считаем «встречается у всех»: это верхняя
            # оценка риска, а флаг ниже запрещает показывать её как процент.
            prevalence_calibrated = False
            prevalence = 1.0
        else:
            prevalence = defect.prevalence

        probability = conditional_failure_probability(defect, vehicle_mileage_km, horizon_km)
        weight = probability * prevalence
        if weight <= 0.0:
            # Нулевой горизонт, S(m) ≈ 0 (болячка уже наступила, 5.6.4) либо
            # prevalence = 0. Вклад нулевой, и в разбор такая строка не нужна.
            continue

        cost, cost_low, cost_high = expected_remedy_cost(
            remedies_by_defect.get(defect.id, ()),
            region_labor_rate,
        )
        contribution = weight * cost
        expected_total += contribution
        low_total += weight * cost_low
        high_total += weight * cost_high
        breakdown.append(
            DefectRisk(
                defect=defect,
                failure_probability=weight,
                expected_cost=cost,
                contribution=contribution,
            )
        )

    breakdown.sort(key=lambda risk: -risk.contribution)

    return RepairForecast(
        expected=expected_total,
        low=low_total,
        high=high_total,
        top_contributors=tuple(breakdown[:TOP_CONTRIBUTORS]),
        confidence=min((d.confidence for d in shown), default=0),
        prevalence_calibrated=prevalence_calibrated,
    )
