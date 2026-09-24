"""Витрина автомобилей — источник данных экранов D1 и C1.

Вход в продукт сместился на витрину (5.5а): человек не вводит госномер, а
открывает карточку конкретного объявления. Значит, продукту нужен список
объявлений и способ достать одно по идентификатору — раньше такого слоя не
было вовсе, потому что всё начиналось с ввода номера.

Данные здесь — фикстуры сценария, а не выдача живых площадок: адаптеры Авито,
Авто.ру и Дрома намеренно не реализованы, пока не закрыт правовой вопрос
(Б2). Форма записи совпадает с целевой, поэтому подключение настоящего
источника меняет реализацию :func:`list_showcase`, а не то, что её вызывает.

Отдельно про :attr:`ShowcaseCar.market_price_rub`. Это «оценка АвтоКлик» с
экранов C1 и D1, то есть выход ценовой модели из 5.3. Модели пока нет, и
выдумывать её внутри витрины нельзя, поэтому оценка лежит в записи как
данность, а там, где её нет, бейдж честно показывает «нет оценки». Когда
5.3 заработает, поле начнёт заполняться расчётом.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from avtoklik.service.defects import RIO_III_GENERATION_ID, SOLARIS_II_GENERATION_ID
from avtoklik.service.payloads import ListingPayload
from avtoklik.service.sources.fixtures import (
    RIO_AVITO,
    SCENARIO_TODAY,
    SOLARIS_AVITO,
)

__all__ = [
    "OWN_PLATFORM",
    "UNKNOWN_GENERATION",
    "ShowcaseCar",
    "generation_for_model",
    "get_car",
    "list_showcase",
    "region_title",
    "register_published",
    "reset_published",
]

#: Поколение неизвестно — по такой модели болячек не собрано (штатное состояние).
UNKNOWN_GENERATION = -1

#: Площадка собственных объявлений. Витрина, собранная только из чужих
#: объявлений, зависит от переговоров с площадками; свои объявления от них
#: не зависят и наполняют её параллельно.
OWN_PLATFORM = "АвтоКлик"

_MODEL_GENERATIONS = {
    "kia-rio-3": RIO_III_GENERATION_ID,
    "hyundai-solaris-2": SOLARIS_II_GENERATION_ID,
}


def generation_for_model(model_id: str | None) -> int:
    """Поколение по ключу модели.

    Неизвестная модель — это :data:`UNKNOWN_GENERATION`, а не ошибка: по ней
    просто нет собранных болячек, и карточка честно скажет об этом.
    """
    if not model_id:
        return UNKNOWN_GENERATION
    return _MODEL_GENERATIONS.get(model_id, UNKNOWN_GENERATION)


_REGIONS = {
    77: "Москва",
    78: "Санкт-Петербург",
    16: "Казань",
    66: "Екатеринбург",
}


def region_title(region_id: int | None) -> str:
    """Человеческое название региона. Незнакомый код — пустая строка, не «None»."""
    if region_id is None:
        return ""
    return _REGIONS.get(region_id, "")


@dataclass(frozen=True, slots=True)
class ShowcaseCar:
    """Автомобиль витрины: объявление плюс то, что знает о нём сам сервис.

    `title` хранится отдельно от ``listing.model_id``: идентификатор модели —
    это ключ (`kia-rio-3`), и показывать его человеку нельзя. Пока нет
    справочника моделей, название живёт рядом с объявлением.

    `risk_note` — текстовая расшифровка рискованной карточки. Она обязательна:
    по §3.4 дизайн-системы ни один статус не передаётся одним лишь цветом, и
    красная рамка без строки в теле карточки — прямое нарушение правила.
    """

    listing: ListingPayload
    title: str
    generation_id: int
    market_price_rub: int | None = None
    seller_kind: str = ""
    risk_note: str = ""
    #: Площадки, на которых найдено то же самое авто (блок дедупликации, 5.2).
    also_on: tuple[str, ...] = ()

    @property
    def listing_id(self) -> str:
        return self.listing.listing_id

    @property
    def has_risk(self) -> bool:
        return bool(self.risk_note)


def _rio_sibling(
    listing_id: str,
    *,
    platform: str,
    price: int,
    mileage_km: int,
    year: int,
    region_id: int,
    days_ago: int,
    plate: str,
    vin: str,
    seller_kind: str,
) -> ListingPayload:
    """Ещё один Rio III в витрине.

    Витрина из одного автомобиля не проверяет ни сортировку, ни бейджи, ни
    сетку, поэтому рядом с эталонным объявлением из сценария стоят однотипные
    соседи с другим пробегом и ценой.
    """
    return ListingPayload(
        listing_id=listing_id,
        platform=platform,
        url=f"https://{platform.lower()}.example/{listing_id}",
        price=price,
        published_on=SCENARIO_TODAY - timedelta(days=days_ago),
        price_changed_on=SCENARIO_TODAY - timedelta(days=days_ago),
        vin=vin,
        plate=plate,
        mileage_km=mileage_km,
        year=year,
        model_id="kia-rio-3",
        region_id=region_id,
        seller_kind=seller_kind,
    )


_CARS: tuple[ShowcaseCar, ...] = (
    ShowcaseCar(
        # Эталон макетов B2/B3: тот же объект, что опрашивают источники,
        # поэтому карточка и проверка говорят об одном автомобиле.
        listing=RIO_AVITO,
        title="Kia Rio III",
        generation_id=RIO_III_GENERATION_ID,
        market_price_rub=845_000,
        seller_kind="частник",
        also_on=("Авто.ру", "Дром"),
    ),
    ShowcaseCar(
        listing=_rio_sibling(
            "avito-8123450077",
            platform="Авито",
            price=619_000,
            mileage_km=148_000,
            year=2015,
            region_id=16,
            days_ago=5,
            plate="Е214ТК716",
            vin="Z94CB41AAFR502233",
            seller_kind="частник",
        ),
        title="Kia Rio III",
        generation_id=RIO_III_GENERATION_ID,
        market_price_rub=625_000,
        seller_kind="частник",
    ),
    ShowcaseCar(
        listing=_rio_sibling(
            "drom-90441207",
            platform="Дром",
            price=929_000,
            mileage_km=61_000,
            year=2018,
            region_id=66,
            days_ago=1,
            plate="Х505ОР196",
            vin="Z94C241BBJR778001",
            seller_kind="салон",
        ),
        title="Kia Rio III",
        generation_id=RIO_III_GENERATION_ID,
        market_price_rub=890_000,
        seller_kind="салон",
    ),
    ShowcaseCar(
        # Негативный сценарий фикстур: скрученный пробег, залог, четыре владельца.
        listing=SOLARIS_AVITO,
        title="Hyundai Solaris II",
        generation_id=SOLARIS_II_GENERATION_ID,
        market_price_rub=760_000,
        seller_kind="частник",
        risk_note="С этого телефона размещено 11 объявлений — похоже на перепродажу",
    ),
    ShowcaseCar(
        listing=ListingPayload(
            listing_id="autoru-2240781",
            platform="Авто.ру",
            url="https://auto.example/cars/used/sale/2240781",
            price=570_000,
            published_on=SCENARIO_TODAY - timedelta(days=4),
            vin="VF1LSRAA456789012",
            plate=None,
            mileage_km=112_000,
            year=2016,
            model_id="renault-logan-2",
            region_id=77,
            seller_kind="частник",
        ),
        title="Renault Logan II",
        # По этой модели болячки ещё не собраны: карточка обязана честно
        # показать «данных пока мало», а не выдуманный прогноз ремонта.
        generation_id=UNKNOWN_GENERATION,
        market_price_rub=None,
        seller_kind="частник",
    ),
    ShowcaseCar(
        listing=ListingPayload(
            listing_id="drom-71330925",
            platform="Дром",
            url="https://drom.example/skoda/octavia/71330925.html",
            price=1_190_000,
            published_on=SCENARIO_TODAY - timedelta(days=11),
            vin="TMBJJ7NE9H0123456",
            plate="О777ТТ178",
            mileage_km=134_000,
            year=2017,
            model_id="skoda-octavia-a7",
            region_id=78,
            seller_kind="салон",
        ),
        title="Škoda Octavia A7",
        generation_id=UNKNOWN_GENERATION,
        market_price_rub=1_150_000,
        seller_kind="салон",
    ),
)

_BY_ID = {car.listing_id: car for car in _CARS}

# Опубликованные собственные объявления. Хранилище в памяти — временная замена
# таблице: витрине важно только то, что свои объявления попадают в неё тем же
# путём, что и чужие, и ничем в ней не выделены, кроме площадки.
_PUBLISHED: list[ShowcaseCar] = []


def register_published(car: ShowcaseCar) -> None:
    """Добавить в витрину опубликованное собственное объявление."""
    _PUBLISHED.append(car)


def reset_published() -> None:
    """Очистить опубликованные объявления. Нужно тестам: хранилище общее."""
    _PUBLISHED.clear()


def list_showcase() -> tuple[ShowcaseCar, ...]:
    """Все объявления витрины: свои сверху, затем собранные с площадок.

    Свои идут первыми не ради привилегии, а потому что они свежие: это
    объявления, опубликованные прямо сейчас, и продавец должен увидеть своё
    сразу после публикации.

    Сортировка «сначала выгодные» из D1 сюда не зашита намеренно: по §5.D она
    наша редакционная власть над чужой выдачей и требует объяснения алгоритма,
    поэтому порядок выбирается на уровне представления и может быть заменён.
    """
    return (*reversed(_PUBLISHED), *_CARS)


def get_car(listing_id: str) -> ShowcaseCar | None:
    """Объявление по идентификатору. ``None`` — такого объявления нет."""
    own = next((car for car in _PUBLISHED if car.listing_id == listing_id), None)
    return own or _BY_ID.get(listing_id)
