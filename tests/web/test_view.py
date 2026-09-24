"""Модели представления: бейджи, форматирование, сборка экранов.

Главное, что проверяется, — правило §3.4 дизайн-системы: ни один статус не
передаётся одним лишь цветом. В коде это значит, что бейдж физически не может
существовать без глифа и слова, и проверять это надо не глазами на ревью.
"""

from __future__ import annotations

import pytest

from avtoklik.service.showcase import get_car, list_showcase
from avtoklik.web.view import (
    MARKET_BAND,
    SUSPICIOUS_DISCOUNT,
    build_card_view,
    build_showcase_view,
    format_mileage,
    format_money,
    market_badge,
    seller_badge,
)

NBSP = " "


class TestFormatting:
    def test_price_uses_non_breaking_spaces(self) -> None:
        """Цена — герой экрана, и переносить её по разрядам нельзя."""
        assert format_money(785_000) == f"785{NBSP}000{NBSP}₽"

    def test_missing_price_is_a_dash_not_none(self) -> None:
        assert format_money(None) == "—"

    def test_missing_mileage_is_explained(self) -> None:
        """«пробег не указан» — это факт об объявлении, а не пустое место."""
        assert format_mileage(None) == "пробег не указан"


class TestMarketBadge:
    def test_cheaper_than_estimate_is_a_benefit(self) -> None:
        badge = market_badge(785_000, 845_000)
        assert badge.tone == "benefit"
        assert badge.text == "−7% ниже рынка"

    def test_dearer_than_estimate_warns(self) -> None:
        badge = market_badge(929_000, 890_000)
        assert badge.tone == "warning"
        assert badge.text == "+4% выше рынка"

    @pytest.mark.parametrize("delta", [0.0, MARKET_BAND / 2, -MARKET_BAND / 2])
    def test_small_deviation_is_just_the_market(self, delta: float) -> None:
        """Объявления скачут на проценты сами по себе: «+1% выше рынка» — шум."""
        badge = market_badge(round(800_000 * (1 + delta)), 800_000)
        assert badge.tone == "neutral"
        assert badge.text == "в рынке"

    def test_deep_discount_is_a_question_not_a_bargain(self) -> None:
        """За скидкой в четверть цены обычно стоит то, чего нет в объявлении."""
        badge = market_badge(round(800_000 * (1 - SUSPICIOUS_DISCOUNT)), 800_000)
        assert badge.tone == "warning"
        assert "подозрительно" in badge.text

    def test_without_an_estimate_the_badge_says_so(self) -> None:
        """Молчание честнее, чем «в рынке», выданное от незнания."""
        badge = market_badge(570_000, None)
        assert badge.tone == "unknown"
        assert badge.text == "нет оценки"


class TestColourIsNeverAlone:
    """§3.4: цвет + глиф + слово. Иначе продукт нечитаем для ~8% покупателей."""

    @pytest.mark.parametrize(
        ("price", "estimate"),
        [(785_000, 845_000), (929_000, 890_000), (800_000, 800_000), (570_000, None)],
    )
    def test_every_market_badge_carries_a_glyph_and_words(
        self, price: int, estimate: int | None
    ) -> None:
        badge = market_badge(price, estimate)
        assert badge.glyph
        assert badge.text
        assert badge.glyph in badge.label and badge.text in badge.label

    def test_every_showcase_badge_carries_a_glyph_and_words(self) -> None:
        for item in build_showcase_view(list_showcase()):
            assert item.market.glyph and item.market.text
            if item.seller is not None:
                assert item.seller.glyph and item.seller.text

    def test_risk_card_always_has_a_text_line(self) -> None:
        """Красная рамка — это цвет. Без строки в теле карточки риск невидим."""
        risky = [car for car in list_showcase() if car.has_risk]
        assert risky, "в витрине должен быть хотя бы один рискованный автомобиль"
        for car in risky:
            assert car.risk_note.strip()


class TestSellerBadge:
    def test_resale_signs_do_not_call_the_person_a_reseller(self) -> None:
        """Метка «перекуп» — утверждение о человеке; юридический вопрос открыт."""
        badge = seller_badge("частник", "С этого телефона размещено 11 объявлений")
        assert badge is not None
        assert badge.tone == "risk"
        assert "перекуп" not in badge.text

    def test_unknown_seller_gets_no_badge(self) -> None:
        assert seller_badge("", "") is None


class TestCardView:
    def test_free_block_is_built_without_any_plate(self) -> None:
        """Прогноз ремонта считается по поколению и пробегу — номер не нужен."""
        car = get_car("avito-3141592653")
        assert car is not None
        view = build_card_view(car)
        assert view.repair is not None
        assert view.repair_range.count("₽") == 2

    def test_model_without_collected_defects_shows_no_numbers(self) -> None:
        """По редкой модели экран обязан молчать, а не показывать нули."""
        car = get_car("autoru-2240781")
        assert car is not None
        view = build_card_view(car)
        assert view.repair is None
        assert view.repair_range == ""

    def test_caveats_are_separated_from_the_cost_lines(self) -> None:
        """Оговорка в одном ряду с болячками читается как болячка за ноль рублей."""
        car = get_car("avito-3141592653")
        assert car is not None
        view = build_card_view(car)
        assert all(item.endswith("₽") for item in view.repair_items)
        assert any("откалибрована" in note for note in view.repair_notes)
        assert view.repair is not None
        assert len(view.repair_items) + len(view.repair_notes) == len(view.repair.lines)

    def test_check_offer_prefers_vin_over_plate(self) -> None:
        car = get_car("avito-3141592653")
        assert car is not None
        view = build_card_view(car)
        assert view.check.subject_type == "vin"
