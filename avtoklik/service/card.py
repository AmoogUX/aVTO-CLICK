"""Сборка карточки товара — экран C1.

Вход в продукт: человек приходит из витрины и открывает карточку конкретного
автомобиля. Госномер он не вводит (5.5а).

На карточке сходятся два разных пути данных, и здесь они разделены явно:

* **бесплатная часть** — цена, пробег, болячки поколения и ожидаемые затраты
  на ремонт. Считается синхронно по своему хранилищу: модель, поколение и
  пробег уже известны из объявления, внешние источники не опрашиваются вовсе;
* **платная часть** — предложение купить проверку по базам. Здесь только
  описание оффера; сам опрос источников запускается после оплаты и живёт
  в :mod:`avtoklik.service.orchestrator`.

Разделение принципиально: бесплатная часть обязана открываться мгновенно и
не стоить нам внешних запросов, иначе мы платим за каждого посетителя, включая
тех, кто ничего не купит.
"""

from __future__ import annotations

from dataclasses import dataclass

from avtoklik.api.schemas import RepairForecastOut
from avtoklik.knowledge import DEFAULT_HORIZON_KM, expected_repair_cost
from avtoklik.matching.plate import normalize_plate
from avtoklik.matching.vin import normalize_vin
from avtoklik.service.defects import defects_for_model, remedies_for_defect
from avtoklik.service.payloads import ListingPayload

__all__ = [
    "CheckOffer",
    "ProductCard",
    "build_product_card",
    "build_repair_forecast",
]


@dataclass(frozen=True, slots=True)
class CheckOffer:
    """Блок покупки проверки по базам — нижняя часть карточки.

    `subject` — то, по чему проверка будет запущена. Он может отсутствовать:
    объявление не обязано содержать госномер. В этом случае блок не исчезает,
    а честно просит ввести номер вручную — молча пропасть было бы хуже, потому
    что пользователь уже настроился купить.
    """

    available: bool
    price_rub: int
    subject_type: str | None = None
    subject_value: str | None = None
    needs_manual_input: bool = False
    title: str = "Проверить по базам"
    note: str = ""


@dataclass(frozen=True, slots=True)
class ProductCard:
    """Карточка автомобиля целиком: что видно сразу и что предлагается купить."""

    listing_id: str
    title: str
    price_rub: int
    mileage_km: int | None
    year: int | None
    platform: str
    #: Бесплатный блок. `None` — по этой модели данных пока мало, и блок
    #: не рисуется: показывать нули хуже, чем не показывать ничего.
    repair: RepairForecastOut | None
    #: Платный блок.
    check: CheckOffer


def build_repair_forecast(
    generation_id: int, mileage_km: int | None, horizon_km: int = int(DEFAULT_HORIZON_KM)
) -> RepairForecastOut | None:
    """Прогноз затрат на ремонт по модели и пробегу — бесплатная часть карточки.

    Госномер не участвует: всё, что нужно расчёту, — поколение и пробег, а они
    известны из объявления. Именно поэтому блок показывается сразу и всем.

    Возвращает `None`, когда считать не из чего: нет пробега или по модели ещё
    нет собранных болячек. Это штатное состояние, а не ошибка.
    """
    if mileage_km is None or mileage_km < 0:
        return None
    defects = defects_for_model(generation_id)
    if not defects:
        return None
    remedies = {defect.id: remedies_for_defect(defect.id) for defect in defects}
    forecast = expected_repair_cost(mileage_km, defects, remedies, horizon_km=horizon_km)
    if forecast.expected <= 0:
        return None

    calibrated = forecast.prevalence_calibrated
    horizon_t_km = horizon_km // 1000
    headline = (
        f"Ремонт на ближайшие {horizon_t_km} т. км"
        if calibrated
        else f"Ремонт на ближайшие {horizon_t_km} т. км — оценка сверху"
    )
    lines = [
        f"{risk.defect.title} — {round(risk.contribution):,} ₽".replace(",", " ")
        for risk in forecast.top_contributors[:3]
    ]
    if not calibrated:
        # Требование 5.6.4: без калибровки по данным СТО проценты встречаемости
        # показывать нельзя — доля упоминаний в отзывах смещена к жалобам.
        lines.append("Встречаемость болячек ещё не откалибрована, поэтому доли не показываем")
    return RepairForecastOut(
        amount_rub=round(forecast.expected),
        low_rub=round(forecast.low),
        high_rub=round(forecast.high),
        horizon_km=horizon_km,
        calibrated=calibrated,
        headline=headline,
        lines=lines,
    )


def build_check_offer(listing: ListingPayload, price_rub: int = 199) -> CheckOffer:
    """Блок покупки проверки по базам.

    Госномер или VIN берутся из объявления, если они там есть. VIN
    предпочтительнее: он опознаёт автомобиль однозначно, тогда как госномер
    перевешивают.
    """
    vin = normalize_vin(listing.vin) if listing.vin else None
    if vin:
        return CheckOffer(
            available=True,
            price_rub=price_rub,
            subject_type="vin",
            subject_value=vin,
            note="История из ГИБДД и других баз: ДТП, владельцы, залог, пробеги",
        )
    plate = normalize_plate(listing.plate) if listing.plate else None
    if plate:
        return CheckOffer(
            available=True,
            price_rub=price_rub,
            subject_type="plate",
            subject_value=plate,
            note="История из ГИБДД и других баз: ДТП, владельцы, залог, пробеги",
        )
    return CheckOffer(
        available=True,
        price_rub=price_rub,
        needs_manual_input=True,
        note="Продавец не указал госномер — введите его, чтобы мы проверили авто по базам",
    )


def build_product_card(
    listing: ListingPayload,
    generation_id: int,
    *,
    check_price_rub: int = 199,
    horizon_km: int = int(DEFAULT_HORIZON_KM),
) -> ProductCard:
    """Собрать карточку автомобиля: бесплатная часть плюс предложение проверки.

    Ни одного обращения к внешним источникам: всё берётся из объявления и
    собственной базы знаний. Это и есть смысл разделения — открытие карточки
    не должно стоить нам платных запросов (5.5а).
    """
    return ProductCard(
        listing_id=listing.listing_id,
        title=f"{listing.model_id or 'Автомобиль'}".strip(),
        price_rub=listing.price,
        mileage_km=listing.mileage_km,
        year=listing.year,
        platform=listing.platform,
        repair=build_repair_forecast(generation_id, listing.mileage_km, horizon_km=horizon_km),
        check=build_check_offer(listing, price_rub=check_price_rub),
    )
