"""Новый вход в продукт: витрина → карточка товара (D1 → C1).

Запуск: ``PYTHONPATH=. python3 examples/demo_card.py``

Показывает то, ради чего переставлен вход: человек открывает карточку и сразу,
без единого ввода, видит что с машиной может быть не так и во сколько обойдётся
ремонт. Ниже — предложение купить проверку по базам.

Ни одного обращения к внешним источникам здесь нет: бесплатная часть считается
по объявлению и собственной базе знаний (docs/03, раздел 5.5а).
"""

from __future__ import annotations

from avtoklik.service.card import ProductCard, build_product_card
from avtoklik.service.defects import RIO_III_GENERATION_ID
from avtoklik.service.payloads import ListingPayload


def rub(value: int) -> str:
    """Рубли с неразрывной группировкой — как на экране."""
    return f"{value:,}".replace(",", " ")


def show(card: ProductCard) -> None:
    """Напечатать карточку так, как её увидит пользователь."""
    print(f"\n{'=' * 70}")
    print(f"  {card.title}, {card.year}   ·   {card.platform}")
    print(f"  {rub(card.price_rub)} ₽   ·   пробег {rub(card.mileage_km or 0)} км")

    print("\n  ── бесплатно, сразу при открытии ──")
    if card.repair is None:
        print("    По этой модели данных пока мало.")
    else:
        repair = card.repair
        print(f"    {repair.headline}")
        interval = f"от {rub(repair.low_rub)} до {rub(repair.high_rub)}"
        print(f"      {rub(repair.amount_rub)} ₽   ({interval})")
        for line in repair.lines:
            print(f"      · {line}")

    print("\n  ── за деньги, по кнопке ──")
    offer = card.check
    if offer.needs_manual_input:
        print(f"    {offer.title} · {offer.price_rub} ₽")
        print(f"      {offer.note}")
    else:
        print(f"    {offer.title} · {offer.price_rub} ₽")
        print(f"      по {offer.subject_type}: {offer.subject_value}")
        print(f"      {offer.note}")


def main() -> None:
    """Две карточки: с данными о болячках и без них."""
    rio = ListingPayload(
        listing_id="avito-1",
        platform="Авито",
        price=785_000,
        vin="Z94C241BBHR123456",
        plate="К999КК799",
        mileage_km=92_000,
        year=2017,
        model_id="Kia Rio III",
        region_id=16,
    )
    show(build_product_card(rio, RIO_III_GENERATION_ID))

    # Редкая модель: болячек в базе нет, но проверку продать можно.
    rare = ListingPayload(
        listing_id="drom-7",
        platform="Дром",
        price=1_240_000,
        plate="В123ВВ716",
        mileage_km=140_000,
        year=2016,
        model_id="Subaru Forester SJ",
        region_id=16,
    )
    show(build_product_card(rare, -1))


if __name__ == "__main__":
    main()
