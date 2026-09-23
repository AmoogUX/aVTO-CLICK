"""Доменные типы модуля знаний о моделях (ТЗ, раздел 5.6.3).

Типы неизменяемые (`frozen=True`): прогноз затрат считается на снимке каталога,
и случайная мутация болячки посреди расчёта дала бы несогласованный результат.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "Defect",
    "DefectNode",
    "DefectRisk",
    "EmpiricalParams",
    "HazardModel",
    "HazardParams",
    "Observation",
    "RemedyOption",
    "RepairForecast",
    "WeibullParams",
]


@dataclass(frozen=True, slots=True)
class DefectNode:
    """Узел из управляемого справочника: `steering_rack`, `catalytic_converter`, ..."""

    code: str
    title_ru: str
    system: str

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("код узла не может быть пустым")


class HazardModel(StrEnum):
    """Способ описания отказов по пробегу."""

    WEIBULL = "weibull"
    EMPIRICAL = "empirical"


@dataclass(frozen=True, slots=True)
class Observation:
    """Одно наблюдение с известным пробегом.

    `failed=False` — правоцензурированное наблюдение («150 т. км, рейка не стучала»).
    Без таких наблюдений модель считает, что ломается всё и у всех (5.6.4).
    """

    mileage_km: float
    failed: bool

    def __post_init__(self) -> None:
        if self.mileage_km < 0:
            raise ValueError("пробег наблюдения не может быть отрицательным")


@dataclass(frozen=True, slots=True)
class WeibullParams:
    """Параметры Вейбулла: shape > 1 — износ, shape ≈ 1 — случайные отказы."""

    shape: float
    scale: float

    def __post_init__(self) -> None:
        if self.shape <= 0:
            raise ValueError("shape должен быть положительным")
        if self.scale <= 0:
            raise ValueError("scale должен быть положительным")


@dataclass(frozen=True, slots=True)
class EmpiricalParams:
    """Наблюдения для оценки Каплана–Мейера, когда выборка достаточна для отказа от формы."""

    observations: tuple[Observation, ...] = ()


HazardParams = WeibullParams | EmpiricalParams


@dataclass(frozen=True, slots=True)
class Defect:
    """Каталожная болячка — то, что пользователь видит на экране C2."""

    id: int
    generation_id: int
    node: DefectNode
    title: str
    description: str
    severity: int
    hazard_model: HazardModel
    hazard_params: HazardParams
    # None = калибровка встречаемости ещё не сделана. Доля упоминаний в отзывах
    # сюда попасть не может: она смещена к жалобам и завышает риск (5.6.1).
    prevalence: float | None = None
    observations_count: int = 0
    confidence: int = 1

    def __post_init__(self) -> None:
        if self.severity not in (1, 2, 3):
            raise ValueError("severity должен быть 1..3")
        if self.confidence not in (1, 2, 3):
            raise ValueError("confidence должен быть 1..3")
        if self.prevalence is not None and not 0.0 <= self.prevalence <= 1.0:
            raise ValueError("prevalence должен быть в диапазоне 0..1")
        if self.observations_count < 0:
            raise ValueError("observations_count не может быть отрицательным")
        # Тип параметров и объявленная модель обязаны совпадать: расхождение здесь
        # означает, что болячка посчиталась бы не той формулой и молча.
        expected = WeibullParams if self.hazard_model is HazardModel.WEIBULL else EmpiricalParams
        if not isinstance(self.hazard_params, expected):
            raise ValueError(
                f"hazard_params не соответствует модели {self.hazard_model.value}",
            )


@dataclass(frozen=True, slots=True)
class RemedyOption:
    """Способ устранения болячки: подтяжка / ремкомплект / замена в сборе.

    Стоимость не хранится числом, а считается из деталей и нормо-часов (5.6.3),
    поэтому при подорожании запчастей экран обновляется сам.
    """

    remedy: str
    share: float
    parts_min: int
    parts_max: int
    labor_hours: float
    labor_rate: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.share <= 1.0:
            raise ValueError("share должен быть в диапазоне 0..1")
        if self.parts_min < 0 or self.parts_max < self.parts_min:
            raise ValueError("должно выполняться 0 <= parts_min <= parts_max")
        if self.labor_hours < 0 or self.labor_rate < 0:
            raise ValueError("нормо-часы и ставка не могут быть отрицательными")


@dataclass(frozen=True, slots=True)
class DefectRisk:
    """Вклад одной болячки в прогноз затрат."""

    defect: Defect
    failure_probability: float
    expected_cost: float
    contribution: float


@dataclass(frozen=True, slots=True)
class RepairForecast:
    """Прогноз затрат на ремонт на горизонте пробега.

    Интервал важнее точки: это ожидание суммы величин с большой дисперсией,
    и одна цифра обещала бы точность, которой нет (5.6.4).
    """

    expected: float
    low: float
    high: float
    top_contributors: tuple[DefectRisk, ...] = ()
    confidence: int = 0
    # False — у части болячек нет калиброванной prevalence; UI обязан показать
    # порядковую шкалу («часто / иногда / редко») вместо процентов.
    prevalence_calibrated: bool = True
