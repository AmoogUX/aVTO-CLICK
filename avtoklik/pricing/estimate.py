"""Оценка цены и прогноз срока продажи (5.3, экран E3).

Две величины и одна связь между ними:

* **справедливая цена** — медиана сегмента с поправкой на пробег (5.3.4, шаг 1);
* **срок продажи** — экспозиция сегмента, умноженная на надбавку за отклонение
  цены от медианы (5.3.4, шаг 3: «до появления сделок считаем по статистике
  сегмента, а не моделью»);
* **шкала «быстрее ↔ дороже»** — та же зависимость, прочитанная в обратную
  сторону: какую цену поставить, чтобы уложиться в шесть дней.

Чего здесь сознательно нет
--------------------------
Формулы `utility(p) = p − daily_holding_cost × E[days | p]` из 5.3.3. Она
требует стоимости дня ожидания и, если подставить реалистичную (амортизация
плюс страховка и стоянка — порядка 600–700 ₽ в день для машины за 1,3 млн),
советует держать цену заметно выше медианы и ждать втрое дольше, чем
предлагает макет. Формула не ошибочна — она просто описывает терпеливого
продавца, а три опорные точки экрана E3 описывают нетерпеливого. Пока нет
данных, из которых видно, какой продавец у нас на самом деле, рекомендацией
служит справедливая цена, и это заодно делает продукт согласованным: продавцу
называется ровно то число, которое покупатель видит как «оценка АвтоКлик».
Пересмотреть — на шаге 3 (5.3.4), когда накопятся факты продаж.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from avtoklik.pricing.segments import SegmentStats, segment_for

__all__ = [
    "FAST_TARGET_DAYS",
    "PRICE_STEP_RUB",
    "SLOW_TARGET_DAYS",
    "PriceAnchor",
    "PriceAnchors",
    "PriceEstimate",
    "build_anchors",
    "days_on_market",
    "estimate_price",
    "price_for_days",
]

#: Шаг шкалы. Требование 5.3.3: слайдер ходит тысячами рублей, а не рублями.
PRICE_STEP_RUB = 1_000

#: Края шкалы E3 заданы сроком, а не ценой: «продать за неделю» — это то,
#: что продавец действительно хочет сказать, в отличие от «минус 3,3 %».
FAST_TARGET_DAYS = 6.0
SLOW_TARGET_DAYS = 25.0


@dataclass(frozen=True, slots=True)
class PriceEstimate:
    """Справедливая цена и объяснение, откуда она взялась.

    `reasons` — это строки экрана E3 («214 похожих в продаже · медиана
    1 305 т», «минус: пробег выше среднего»). Объяснимость здесь не украшение:
    число без объяснения продавец просто не примет.
    """

    fair_price_rub: int
    segment: SegmentStats
    mileage_delta_km: int
    reasons: tuple[str, ...]


def _round_to_step(value: float) -> int:
    return int(round(value / PRICE_STEP_RUB) * PRICE_STEP_RUB)


def estimate_price(model_id: str | None, mileage_km: int | None) -> PriceEstimate | None:
    """Справедливая цена автомобиля. ``None`` — данных для оценки не хватает.

    Возврат ``None`` штатен и обязан обрабатываться выше: по правилу 5.3.4
    интерфейс в этом случае предлагает продавцу назначить цену самому, а не
    показывает выдуманное число.
    """
    segment = segment_for(model_id)
    if segment is None or not segment.is_thick_enough:
        return None
    if mileage_km is None or mileage_km < 0:
        return None

    mileage_delta = mileage_km - segment.median_mileage_km
    correction = -(mileage_delta / 1000) * segment.price_per_1000km
    fair = _round_to_step(segment.median_price_rub + correction)

    median = f"{segment.median_price_rub // 1000} т"
    reasons = [f"{segment.listings_count} похожих в продаже · медиана {median}"]
    if mileage_delta > 0:
        reasons.append(f"минус: пробег выше среднего по сегменту на {mileage_delta // 1000} т. км")
    elif mileage_delta < 0:
        reasons.append(f"плюс: пробег ниже среднего по сегменту на {-mileage_delta // 1000} т. км")
    else:
        reasons.append("пробег ровно средний по сегменту")
    return PriceEstimate(
        fair_price_rub=fair,
        segment=segment,
        mileage_delta_km=mileage_delta,
        reasons=tuple(reasons),
    )


def days_on_market(price_rub: int, segment: SegmentStats) -> float:
    """Сколько дней объявление провисит при такой цене.

    Отклонение считается от медианы сегмента, а не от справедливой цены:
    покупатель сравнивает объявление с тем, что видит рядом, а не с нашей
    оценкой.
    """
    delta_pct = (price_rub / segment.median_price_rub - 1) * 100
    return segment.median_days_on_market * math.exp(segment.days_elasticity * delta_pct)


def price_for_days(target_days: float, segment: SegmentStats) -> int:
    """Обратная задача: какую цену поставить, чтобы уложиться в срок.

    Именно так заданы края шкалы E3 — сроком, а не процентом скидки.
    """
    if target_days <= 0:
        raise ValueError("срок продажи должен быть положительным")
    delta_pct = math.log(target_days / segment.median_days_on_market) / segment.days_elasticity
    return _round_to_step(segment.median_price_rub * (1 + delta_pct / 100))


@dataclass(frozen=True, slots=True)
class PriceAnchor:
    """Точка шкалы: цена и срок, который она означает."""

    price_rub: int
    days: int

    @property
    def days_label(self) -> str:
        """«≈ 12 дней». Точнее показывать нельзя (5.3.3): это ложная точность."""
        return f"≈ {self.days} {_plural_days(self.days)}"


@dataclass(frozen=True, slots=True)
class PriceAnchors:
    """Три точки экрана E3: быстрее, рекомендуем, дороже."""

    fast: PriceAnchor
    recommended: PriceAnchor
    slow: PriceAnchor
    estimate: PriceEstimate


def _plural_days(count: int) -> str:
    tail_100, tail_10 = count % 100, count % 10
    if 11 <= tail_100 <= 14:
        return "дней"
    if tail_10 == 1:
        return "день"
    if 2 <= tail_10 <= 4:
        return "дня"
    return "дней"


def _anchor(price_rub: int, segment: SegmentStats) -> PriceAnchor:
    return PriceAnchor(price_rub=price_rub, days=round(days_on_market(price_rub, segment)))


def build_anchors(estimate: PriceEstimate) -> PriceAnchors:
    """Собрать шкалу «быстрее ↔ дороже» вокруг справедливой цены."""
    segment = estimate.segment
    return PriceAnchors(
        fast=_anchor(price_for_days(FAST_TARGET_DAYS, segment), segment),
        recommended=_anchor(estimate.fair_price_rub, segment),
        slow=_anchor(price_for_days(SLOW_TARGET_DAYS, segment), segment),
        estimate=estimate,
    )
