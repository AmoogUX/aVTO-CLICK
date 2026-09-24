"""Оценка цены и шкала «быстрее ↔ дороже».

Эталон — экран E3 из дизайн-концепции: «214 похожих в продаже · медиана
1 305 т», рекомендация 1 292 000 ₽ и ≈12 дней, края шкалы 1 249 т/≈6 дней
и 1 340 т/≈25 дней. Если расчёт перестанет давать эти числа, разошлись либо
формулы, либо срез сегмента — и то и другое стоит заметить.
"""

from __future__ import annotations

import math

import pytest

from avtoklik.pricing import (
    MIN_SEGMENT_LISTINGS,
    build_anchors,
    days_on_market,
    estimate_price,
    price_for_days,
    segment_for,
)

OCTAVIA = "skoda-octavia-a7"
OCTAVIA_MILEAGE = 134_000


class TestFairPrice:
    def test_reference_car_matches_the_mockup(self) -> None:
        estimate = estimate_price(OCTAVIA, OCTAVIA_MILEAGE)
        assert estimate is not None
        assert estimate.fair_price_rub == 1_292_000

    def test_explanation_names_the_segment_and_the_mileage(self) -> None:
        """Число без объяснения продавец не примет — это требование 5.3 п. 4."""
        estimate = estimate_price(OCTAVIA, OCTAVIA_MILEAGE)
        assert estimate is not None
        assert "214 похожих" in estimate.reasons[0]
        assert "1305 т" in estimate.reasons[0]
        assert "пробег выше среднего" in estimate.reasons[1]

    def test_lower_mileage_raises_the_price(self) -> None:
        cheap = estimate_price(OCTAVIA, 160_000)
        dear = estimate_price(OCTAVIA, 60_000)
        assert cheap is not None and dear is not None
        assert dear.fair_price_rub > cheap.fair_price_rub

    def test_price_is_rounded_to_thousands(self) -> None:
        """Слайдер ходит тысячами: оценка в 1 292 173 ₽ обещает несуществующую точность."""
        for mileage in range(60_000, 160_000, 7_000):
            estimate = estimate_price(OCTAVIA, mileage)
            assert estimate is not None
            assert estimate.fair_price_rub % 1_000 == 0


class TestSilenceInsteadOfInvention:
    """5.3.4: на редкой модели пустое место лучше выдуманного числа."""

    def test_thin_segment_gives_no_estimate(self) -> None:
        segment = segment_for("kia-rio-3")
        assert segment is not None
        assert segment.listings_count < MIN_SEGMENT_LISTINGS
        assert estimate_price("kia-rio-3", 92_000) is None

    def test_unknown_model_gives_no_estimate(self) -> None:
        assert estimate_price("renault-logan-2", 112_000) is None
        assert estimate_price(None, 112_000) is None

    def test_missing_mileage_gives_no_estimate(self) -> None:
        """Без пробега поправку считать не из чего, а медиана сегмента — не оценка."""
        assert estimate_price(OCTAVIA, None) is None


class TestTimeOnMarket:
    def test_asking_more_takes_longer(self) -> None:
        segment = segment_for(OCTAVIA)
        assert segment is not None
        cheap = days_on_market(1_249_000, segment)
        dear = days_on_market(1_340_000, segment)
        assert cheap < dear

    def test_median_price_gives_the_segment_exposure(self) -> None:
        segment = segment_for(OCTAVIA)
        assert segment is not None
        assert days_on_market(segment.median_price_rub, segment) == pytest.approx(
            segment.median_days_on_market
        )

    def test_price_for_days_inverts_days_on_market(self) -> None:
        segment = segment_for(OCTAVIA)
        assert segment is not None
        for target in (6.0, 12.0, 25.0, 40.0):
            price = price_for_days(target, segment)
            # Цена округлена до тысячи, поэтому срок сходится не побитово.
            assert days_on_market(price, segment) == pytest.approx(target, rel=0.01)

    def test_zero_days_is_refused_rather_than_silently_clamped(self) -> None:
        segment = segment_for(OCTAVIA)
        assert segment is not None
        with pytest.raises(ValueError):
            price_for_days(0, segment)


class TestAnchors:
    def test_three_anchors_match_the_mockup(self) -> None:
        estimate = estimate_price(OCTAVIA, OCTAVIA_MILEAGE)
        assert estimate is not None
        anchors = build_anchors(estimate)
        assert (anchors.fast.price_rub, anchors.fast.days) == (1_249_000, 6)
        assert (anchors.recommended.price_rub, anchors.recommended.days) == (1_292_000, 12)
        assert (anchors.slow.price_rub, anchors.slow.days) == (1_340_000, 25)

    def test_scale_is_monotone(self) -> None:
        estimate = estimate_price(OCTAVIA, OCTAVIA_MILEAGE)
        assert estimate is not None
        anchors = build_anchors(estimate)
        assert anchors.fast.price_rub < anchors.recommended.price_rub < anchors.slow.price_rub
        assert anchors.fast.days < anchors.recommended.days < anchors.slow.days

    def test_recommendation_equals_the_estimate_shown_to_buyers(self) -> None:
        """Продавцу и покупателю называется одно число, иначе продукт противоречит себе."""
        estimate = estimate_price(OCTAVIA, OCTAVIA_MILEAGE)
        assert estimate is not None
        assert build_anchors(estimate).recommended.price_rub == estimate.fair_price_rub

    @pytest.mark.parametrize(
        ("days", "expected"),
        [(1, "≈ 1 день"), (2, "≈ 2 дня"), (6, "≈ 6 дней"), (12, "≈ 12 дней"), (21, "≈ 21 день")],
    )
    def test_days_are_never_shown_more_precisely_than_whole(self, days: int, expected: str) -> None:
        estimate = estimate_price(OCTAVIA, OCTAVIA_MILEAGE)
        assert estimate is not None
        anchors = build_anchors(estimate)
        anchor = type(anchors.fast)(price_rub=1_000_000, days=days)
        assert anchor.days_label == expected


class TestElasticityProvenance:
    def test_elasticity_reproduces_the_three_mockup_anchors(self) -> None:
        """Коэффициент подогнан под макет, а не оценён по данным.

        Тест фиксирует именно это: он проверяет не «правильность» упругости,
        а то, что она всё ещё воспроизводит точки, по которым её подбирали.
        Когда появятся факты продаж, коэффициент пересчитается, и тест
        придётся переписать осознанно.
        """
        segment = segment_for(OCTAVIA)
        assert segment is not None
        for price, expected_days in ((1_249_000, 6), (1_292_000, 12), (1_340_000, 25)):
            assert round(days_on_market(price, segment)) == expected_days

    def test_days_grow_exponentially_not_linearly(self) -> None:
        """Удвоение отклонения цены удорожает ожидание сильнее, чем вдвое."""
        segment = segment_for(OCTAVIA)
        assert segment is not None
        base = segment.median_price_rub
        one = days_on_market(round(base * 1.02), segment)
        two = days_on_market(round(base * 1.04), segment)
        assert two > 2 * one - segment.median_days_on_market
        assert math.isclose(
            two / segment.median_days_on_market,
            (one / segment.median_days_on_market) ** 2,
            rel_tol=0.01,
        )
