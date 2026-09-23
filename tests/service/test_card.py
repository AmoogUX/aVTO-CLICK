"""Карточка товара: бесплатная часть и платная (5.5а).

Главное, что проверяется здесь, — разделение двух путей данных. Бесплатный
блок обязан собираться из объявления и своей базы знаний, без госномера и без
единого обращения к внешним источникам; платный блок только описывает оффер.
"""

from __future__ import annotations

from datetime import date

from avtoklik.service.card import (
    build_check_offer,
    build_product_card,
    build_repair_forecast,
)
from avtoklik.service.defects import RIO_III_GENERATION_ID
from avtoklik.service.payloads import ListingPayload

UNKNOWN_GENERATION = -1


def listing(**overrides: object) -> ListingPayload:
    """Объявление эталонного автомобиля дизайна."""
    base: dict[str, object] = {
        "listing_id": "avito-1",
        "platform": "Авито",
        "price": 785_000,
        "published_on": date(2026, 9, 23),
        "vin": "Z94C241BBHR123456",
        "plate": "К999КК799",
        "mileage_km": 92_000,
        "year": 2017,
        "model_id": "Kia Rio III",
        "region_id": 16,
    }
    base.update(overrides)
    return ListingPayload(**base)  # type: ignore[arg-type]


class TestFreePart:
    """Болячки и стоимость ремонта — показываются сразу, без ввода."""

    def test_forecast_needs_only_generation_and_mileage(self) -> None:
        """Госномер в расчёт не входит вовсе: этого требует новая точка входа."""
        forecast = build_repair_forecast(RIO_III_GENERATION_ID, 92_000)
        assert forecast is not None
        assert forecast.amount_rub > 0

    def test_forecast_grows_with_mileage(self) -> None:
        """Чем больше пробег, тем ближе износовые отказы — сумма обязана расти."""
        low = build_repair_forecast(RIO_III_GENERATION_ID, 40_000)
        high = build_repair_forecast(RIO_III_GENERATION_ID, 140_000)
        assert low is not None and high is not None
        assert high.amount_rub > low.amount_rub

    def test_interval_brackets_the_estimate(self) -> None:
        forecast = build_repair_forecast(RIO_III_GENERATION_ID, 92_000)
        assert forecast is not None
        assert forecast.low_rub <= forecast.amount_rub <= forecast.high_rub

    def test_uncalibrated_forecast_hides_percentages(self) -> None:
        forecast = build_repair_forecast(RIO_III_GENERATION_ID, 92_000)
        assert forecast is not None
        assert forecast.calibrated is False
        assert "%" not in forecast.headline
        assert "%" not in " ".join(forecast.lines)

    def test_no_data_for_unknown_model(self) -> None:
        """По модели без каталога блок не рисуется — это честнее нулей."""
        assert build_repair_forecast(UNKNOWN_GENERATION, 92_000) is None

    def test_no_forecast_without_mileage(self) -> None:
        """Без пробега считать не из чего: горизонт отсчитывается от него."""
        assert build_repair_forecast(RIO_III_GENERATION_ID, None) is None

    def test_negative_mileage_is_rejected(self) -> None:
        assert build_repair_forecast(RIO_III_GENERATION_ID, -1) is None


class TestPaidPart:
    """Блок покупки проверки по базам."""

    def test_vin_wins_over_plate(self) -> None:
        """VIN опознаёт автомобиль однозначно, госномер перевешивают."""
        offer = build_check_offer(listing())
        assert offer.subject_type == "vin"
        assert offer.subject_value == "Z94C241BBHR123456"

    def test_falls_back_to_plate(self) -> None:
        offer = build_check_offer(listing(vin=None))
        assert offer.subject_type == "plate"
        assert offer.subject_value == "К999КК799"

    def test_offer_survives_without_identifiers(self) -> None:
        """Объявление без номера — не повод прятать блок: пользователь уже
        настроился купить, и молчаливое исчезновение хуже просьбы ввести номер."""
        offer = build_check_offer(listing(vin=None, plate=None))
        assert offer.available is True
        assert offer.needs_manual_input is True
        assert offer.subject_value is None

    def test_plate_is_normalized(self) -> None:
        """Латиница из объявления приводится к кириллице, иначе проверка не найдёт авто."""
        offer = build_check_offer(listing(vin=None, plate="k999kk799"))
        assert offer.subject_value == "К999КК799"


class TestCardAssembly:
    """Карточка целиком."""

    def test_card_has_both_blocks(self) -> None:
        card = build_product_card(listing(), RIO_III_GENERATION_ID)
        assert card.repair is not None
        assert card.check.available is True

    def test_card_keeps_listing_facts(self) -> None:
        card = build_product_card(listing(), RIO_III_GENERATION_ID)
        assert card.price_rub == 785_000
        assert card.mileage_km == 92_000
        assert card.year == 2017
        assert card.platform == "Авито"

    def test_card_without_repair_data_still_sells_check(self) -> None:
        """Два блока независимы: отсутствие болячек не мешает продать проверку."""
        card = build_product_card(listing(), UNKNOWN_GENERATION)
        assert card.repair is None
        assert card.check.available is True

    def test_card_without_identifiers_still_shows_repair(self) -> None:
        """И наоборот: без госномера бесплатная часть работает в полном объёме."""
        card = build_product_card(listing(vin=None, plate=None), RIO_III_GENERATION_ID)
        assert card.repair is not None
        assert card.check.needs_manual_input is True
