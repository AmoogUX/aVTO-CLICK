"""Каталог болячек для расчёта ремонта под пробег.

Временный источник знаний, пока не заработал конвейер сбора из раздела 5.6:
болячки описаны в коде, с теми же параметрами, что лягут в `defect_catalog`.
Это не заглушка «лишь бы считалось» — структура совпадает с целевой, и когда
конвейер начнёт наполнять базу, поменяется источник данных, а не расчёт.

Распространённость намеренно оставлена неоткалиброванной (`prevalence=None`)
у всех болячек: калибровать её нечем, пока нет статистики обращений СТО
(5.6.4). Расчёт в этом случае даёт оценку сверху и помечает результат флагом,
по которому интерфейс показывает порядковую шкалу вместо процентов.
"""

from __future__ import annotations

from avtoklik.knowledge import Defect, DefectNode, HazardModel, RemedyOption, WeibullParams

__all__ = ["RIO_III_GENERATION_ID", "defects_for_model", "remedies_for_defect"]

#: Идентификатор поколения эталонной модели дизайна (Kia Rio III).
_RIO_III_GEN = 1
RIO_III_GENERATION_ID = _RIO_III_GEN

# Узлы: подмножество справочника `defect_nodes`.
_RACK = DefectNode(code="steering_rack", title_ru="Рулевая рейка", system="рулевое управление")
_CATALYST = DefectNode(code="catalytic_converter", title_ru="Катализатор", system="выпуск")
_THERMOSTAT = DefectNode(code="thermostat", title_ru="Термостат", system="охлаждение")
_SUSPENSION = DefectNode(code="front_struts", title_ru="Передние стойки", system="подвеска")

# Болячки эталонной модели из дизайна (экран C2 нарисован на Kia Rio III).
# shape > 1 везде, где отказ вызван износом: вероятность растёт с пробегом.
_RIO_III: tuple[Defect, ...] = (
    Defect(
        id=1,
        generation_id=_RIO_III_GEN,
        node=_RACK,
        title="Стук рулевой рейки",
        description="лечится подтяжкой или ремкомплектом",
        severity=2,
        hazard_model=HazardModel.WEIBULL,
        hazard_params=WeibullParams(shape=2.1, scale=118_000.0),
        prevalence=None,
        observations_count=214,
        confidence=3,
    ),
    Defect(
        id=2,
        generation_id=_RIO_III_GEN,
        node=_CATALYST,
        title="Оплавление катализатора",
        description="проверить эндоскопом при осмотре",
        severity=3,
        hazard_model=HazardModel.WEIBULL,
        hazard_params=WeibullParams(shape=3.0, scale=155_000.0),
        prevalence=None,
        observations_count=96,
        confidence=3,
    ),
    Defect(
        id=3,
        generation_id=_RIO_III_GEN,
        node=_THERMOSTAT,
        title="Термостат: долгий прогрев зимой",
        description="меняется в сборе",
        severity=1,
        hazard_model=HazardModel.WEIBULL,
        hazard_params=WeibullParams(shape=1.8, scale=105_000.0),
        prevalence=None,
        observations_count=58,
        confidence=2,
    ),
    Defect(
        id=4,
        generation_id=_RIO_III_GEN,
        node=_SUSPENSION,
        title="Стойки передней подвески",
        description="расходник, но заметный по деньгам",
        severity=1,
        hazard_model=HazardModel.WEIBULL,
        hazard_params=WeibullParams(shape=2.4, scale=125_000.0),
        prevalence=None,
        observations_count=131,
        confidence=3,
    ),
)

# Стоимость: детали (аналог → оригинал) и нормо-часы. Ставка нормо-часа —
# средняя по стране; региональная подставляется расчётом (5.6.4).
_REMEDIES: dict[int, tuple[RemedyOption, ...]] = {
    1: (
        RemedyOption(
            remedy="подтяжка",
            share=0.55,
            parts_min=0,
            parts_max=0,
            labor_hours=1.0,
            labor_rate=1800,
        ),
        RemedyOption(
            remedy="ремкомплект",
            share=0.35,
            parts_min=4_500,
            parts_max=9_000,
            labor_hours=3.5,
            labor_rate=1800,
        ),
        RemedyOption(
            remedy="замена в сборе",
            share=0.10,
            parts_min=18_000,
            parts_max=34_000,
            labor_hours=4.0,
            labor_rate=1800,
        ),
    ),
    2: (
        RemedyOption(
            remedy="замена катализатора",
            share=0.45,
            parts_min=28_000,
            parts_max=72_000,
            labor_hours=2.5,
            labor_rate=1800,
        ),
        RemedyOption(
            remedy="пламегаситель",
            share=0.55,
            parts_min=6_000,
            parts_max=14_000,
            labor_hours=2.0,
            labor_rate=1800,
        ),
    ),
    3: (
        RemedyOption(
            remedy="замена термостата",
            share=1.0,
            parts_min=1_900,
            parts_max=4_600,
            labor_hours=1.5,
            labor_rate=1800,
        ),
    ),
    4: (
        RemedyOption(
            remedy="замена пары стоек",
            share=1.0,
            parts_min=7_600,
            parts_max=16_800,
            labor_hours=2.5,
            labor_rate=1800,
        ),
    ),
}

_BY_MODEL: dict[int, tuple[Defect, ...]] = {_RIO_III_GEN: _RIO_III}


def defects_for_model(generation_id: int) -> tuple[Defect, ...]:
    """Болячки поколения. Пустой кортеж — данных по модели пока нет.

    Пустой ответ штатен и обязан обрабатываться выше: на редкой модели экран
    показывает «по этой модели данных пока мало», а не выдуманные болячки.
    """
    return _BY_MODEL.get(generation_id, ())


def remedies_for_defect(defect_id: int) -> tuple[RemedyOption, ...]:
    """Способы устранения болячки с долями и стоимостью."""
    return _REMEDIES.get(defect_id, ())
