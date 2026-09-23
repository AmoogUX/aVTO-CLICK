"""Фабрики тестовых объектов каталога болячек."""

from __future__ import annotations

from avtoklik.knowledge import (
    Defect,
    DefectNode,
    EmpiricalParams,
    HazardModel,
    HazardParams,
    Observation,
    RemedyOption,
    WeibullParams,
)

STEERING_RACK = DefectNode(code="steering_rack", title_ru="Рулевая рейка", system="рулевое")
CATALYST = DefectNode(code="catalytic_converter", title_ru="Катализатор", system="выпуск")
THERMOSTAT = DefectNode(code="thermostat", title_ru="Термостат", system="двигатель")


def make_defect(
    *,
    defect_id: int = 1,
    node: DefectNode = STEERING_RACK,
    shape: float = 2.1,
    scale: float = 118_000.0,
    params: HazardParams | None = None,
    prevalence: float | None = 0.5,
    confidence: int = 3,
    severity: int = 2,
    observations_count: int = 120,
) -> Defect:
    """Болячка с параметрами по умолчанию; `params` перекрывает модель Вейбулла."""
    hazard_params = params if params is not None else WeibullParams(shape=shape, scale=scale)
    model = (
        HazardModel.WEIBULL if isinstance(hazard_params, WeibullParams) else HazardModel.EMPIRICAL
    )
    return Defect(
        id=defect_id,
        generation_id=100,
        node=node,
        title=f"Болячка узла {node.title_ru}",
        description="тестовая болячка",
        severity=severity,
        hazard_model=model,
        hazard_params=hazard_params,
        prevalence=prevalence,
        observations_count=observations_count,
        confidence=confidence,
    )


def make_empirical_defect(
    observations: list[tuple[float, bool]],
    *,
    defect_id: int = 1,
    prevalence: float | None = 1.0,
) -> Defect:
    """Болячка с эмпирической моделью по списку (пробег, был_ли_отказ)."""
    return make_defect(
        defect_id=defect_id,
        params=EmpiricalParams(
            observations=tuple(Observation(mileage_km=m, failed=f) for m, f in observations)
        ),
        prevalence=prevalence,
    )


def make_remedy(
    *,
    remedy: str = "замена в сборе",
    share: float = 1.0,
    parts_min: int = 10_000,
    parts_max: int = 20_000,
    labor_hours: float = 2.0,
    labor_rate: float = 1_500.0,
) -> RemedyOption:
    """Способ устранения с предсказуемыми числами."""
    return RemedyOption(
        remedy=remedy,
        share=share,
        parts_min=parts_min,
        parts_max=parts_max,
        labor_hours=labor_hours,
        labor_rate=labor_rate,
    )
