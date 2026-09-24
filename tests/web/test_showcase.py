"""Витрина как источник данных: что обязана гарантировать сама запись."""

from __future__ import annotations

from avtoklik.service.card import build_product_card
from avtoklik.service.showcase import UNKNOWN_GENERATION, get_car, list_showcase, region_title


class TestCatalogue:
    def test_identifiers_are_unique(self) -> None:
        """Идентификатор объявления — ключ маршрута, дубль ломает ссылку."""
        ids = [car.listing_id for car in list_showcase()]
        assert len(ids) == len(set(ids))

    def test_every_car_resolves_by_its_identifier(self) -> None:
        for car in list_showcase():
            assert get_car(car.listing_id) is car

    def test_unknown_identifier_returns_none(self) -> None:
        assert get_car("no-such-listing") is None

    def test_titles_are_human_not_slugs(self) -> None:
        """`kia-rio-3` — это ключ модели, и показывать его человеку нельзя."""
        for car in list_showcase():
            assert "-" not in car.title.replace("—", "")
            assert car.title[0].isupper()

    def test_unknown_region_is_empty_not_none(self) -> None:
        assert region_title(None) == ""
        assert region_title(999) == ""


class TestCoverage:
    """Витрина должна покрывать все состояния карточки, иначе их негде увидеть."""

    def test_there_is_a_car_without_collected_defects(self) -> None:
        assert any(car.generation_id == UNKNOWN_GENERATION for car in list_showcase())

    def test_there_is_a_car_with_a_risk_note(self) -> None:
        assert any(car.has_risk for car in list_showcase())

    def test_there_is_a_car_without_a_price_estimate(self) -> None:
        assert any(car.market_price_rub is None for car in list_showcase())

    def test_there_is_a_car_sold_on_several_platforms(self) -> None:
        assert any(car.also_on for car in list_showcase())


class TestOpeningACardCostsNothing:
    def test_card_assembles_from_the_listing_alone(self) -> None:
        """Ни одного внешнего запроса: иначе мы платим за каждого посетителя."""
        for car in list_showcase():
            card = build_product_card(car.listing, car.generation_id)
            assert card.listing_id == car.listing_id
            assert card.check.available
