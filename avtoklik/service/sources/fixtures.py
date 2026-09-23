"""Данные сценария из концепции: два автомобиля, шесть источников.

Первый автомобиль — эталонный из макетов B2/B3: **Kia Rio III, 2017, К999КК799,
1.6 AT, 92 000 км, 2 владельца, одно лёгкое ДТП в 2022 (задняя часть, крышка
багажника), без залога, в такси не числится**. Он продаётся одновременно на трёх
площадках — Авито 785 000 ₽ (сегодня), Авто.ру 799 000 ₽ (3 дня назад), Дром
810 000 ₽ (8 дней назад), — и это одно и то же авто: совпадают VIN, телефон
продавца и фотографии. Разброс 785 000…810 000 даёт ровно те 25 000 ₽, которые
обещает экран B3 и на которых держится весь аргумент продукта.

Второй автомобиль — Hyundai Solaris II, 2018, У777МН178 — нужен для негативного
вердикта: пробег скручен (143 000 км в 2023 году против 61 000 в 2024), тяжёлое
ДТП с лонжероном, четыре владельца, залог, ограничения и отметка «такси». Он же
проверяет, что дедупликация **не** склеивает разные автомобили: VIN другой,
фотографии — побитовые дополнения рио-шных, то есть расходятся на все 64 бита.

Все даты отсчитываются от :data:`SCENARIO_TODAY`, а не от ``date.today()``:
«сегодня» и «3 дня назад» из макета должны означать одно и то же при каждом
прогоне, иначе тест на канонические цены начнёт зависеть от календаря.

Отзывы владельцев берутся из учебного корпуса :mod:`avtoklik.collector.fixtures` —
там уже лежат тексты про рулевую рейку Rio 3 и катализатор Solaris со
стоимостями ремонта, и заводить второй корпус рядом незачем.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from types import MappingProxyType
from typing import TypeVar

from avtoklik.collector.fixtures import load_review
from avtoklik.service.payloads import (
    AccidentRecord,
    ListingPayload,
    MileageRecord,
    RegistryPayload,
    ReviewsPayload,
)
from avtoklik.service.sources.base import DromPayload, NomerogramPayload, PhotoSighting

__all__ = [
    "AUTORU_FIXTURES",
    "AVITO_FIXTURES",
    "DROM_FIXTURES",
    "GIBDD_FIXTURES",
    "NOMEROGRAM_FIXTURES",
    "RIO_LISTINGS",
    "RIO_MODEL_ID",
    "RIO_PHOTOS",
    "RIO_PLATE",
    "RIO_PRICE_SPREAD_RUB",
    "RIO_VIN",
    "SCENARIO_TODAY",
    "SOCIAL_FIXTURES",
    "SOLARIS_LISTINGS",
    "SOLARIS_MODEL_ID",
    "SOLARIS_PHOTOS",
    "SOLARIS_PLATE",
    "SOLARIS_VIN",
    "UNKNOWN_PLATE",
]

SCENARIO_TODAY = date(2026, 9, 23)
"""«Сегодня» сценария. Все относительные даты макета отсчитываются отсюда."""

# ─────────────────────────────────────────────────────────────────────────────
# Автомобиль 1 — Kia Rio III, эталон экранов B2/B3
# ─────────────────────────────────────────────────────────────────────────────

RIO_PLATE = "К999КК799"
RIO_VIN = "Z94C241BBHR123456"
RIO_MODEL_ID = "kia-rio-3"
RIO_REGION_ID = 77

RIO_PHONE_HASH = "hmac-sha256:3f8a1c94b7e2d605"
"""Один продавец на трёх площадках. Сам телефон не хранится (§5.2)."""

RIO_PHOTOS: tuple[int, ...] = (
    0x9E3779B97F4A7C15,
    0xC2B2AE3D27D4EB4F,
    0x165667B19E3779F9,
    0x27D4EB2F165667C5,
)
"""Перцептивные хэши четырёх кадров продавца, 64 бита каждый — как отдал Авито."""

# Авто.ру пережимает загруженные кадры по-своему: у каждого хэша уплывает один
# бит. Дром пережимает сильнее — по два бита, причём другие. Итого расстояние
# Авито↔Авто.ру = 1, Авито↔Дром = 2, Авто.ру↔Дром = 3 бита на кадр: всё
# заведомо внутри порога PHASH_MAX_DISTANCE = 6, и дедупликация обязана склеить.
_AUTORU_NOISE = (0b0001, 0b0010, 0b0100, 0b1000)
_DROM_NOISE = (0b0110, 0b1100, 0b11000, 0b110000)

RIO_PHOTOS_AUTORU: tuple[int, ...] = tuple(
    phash ^ noise for phash, noise in zip(RIO_PHOTOS, _AUTORU_NOISE, strict=True)
)
RIO_PHOTOS_DROM: tuple[int, ...] = tuple(
    phash ^ noise for phash, noise in zip(RIO_PHOTOS, _DROM_NOISE, strict=True)
)

RIO_MILEAGE_HISTORY = (
    MileageRecord(date(2019, 8, 14), 41_000, "ТО у дилера"),
    MileageRecord(date(2021, 9, 2), 63_500, "техосмотр"),
    MileageRecord(date(2023, 9, 8), 78_200, "техосмотр"),
    MileageRecord(date(2025, 8, 21), 91_400, "ТО у дилера"),
)
"""Монотонно растущий пробег — признак честной истории."""

RIO_ACCIDENTS = (
    AccidentRecord(
        occurred_on=date(2022, 5, 14),
        severity="лёгкое",
        damage_zone="задняя часть, крышка багажника",
        repaired=True,
    ),
)

RIO_GIBDD = RegistryPayload(
    # ГИБДД отвечает по госномеру и VIN в ответе не возвращает: переход
    # «госномер → VIN» по спеке закрывает Номерограм, см. NOMEROGRAM_FIXTURES.
    vin=None,
    brand="Kia",
    model="Rio III",
    year=2017,
    engine="1.6 AT",
    owners_count=2,
    mileage_history=RIO_MILEAGE_HISTORY,
    accidents=RIO_ACCIDENTS,
    is_pledged=False,
    is_taxi=False,
    has_restrictions=False,
)

RIO_NOMEROGRAM = NomerogramPayload(
    registry=RegistryPayload(
        vin=RIO_VIN,
        brand="Kia",
        model="Rio III",
        year=2017,
        engine="1.6 AT",
        owners_count=2,
    ),
    photo_history=(
        PhotoSighting(date(2022, 11, 3), "Дром", RIO_PHOTOS[0], "https://drom.example/22-11"),
        PhotoSighting(SCENARIO_TODAY - timedelta(days=8), "Дром", RIO_PHOTOS_DROM[0]),
        PhotoSighting(SCENARIO_TODAY - timedelta(days=3), "Авто.ру", RIO_PHOTOS_AUTORU[0]),
        PhotoSighting(SCENARIO_TODAY, "Авито", RIO_PHOTOS[0]),
    ),
)

RIO_AVITO = ListingPayload(
    listing_id="avito-3141592653",
    platform="Авито",
    url="https://avito.example/kia_rio_2017_3141592653",
    price=785_000,
    published_on=SCENARIO_TODAY,
    price_changed_on=SCENARIO_TODAY,
    vin=RIO_VIN,
    plate=RIO_PLATE,
    phone_hash=RIO_PHONE_HASH,
    photo_phashes=RIO_PHOTOS,
    mileage_km=92_000,
    year=2017,
    model_id=RIO_MODEL_ID,
    region_id=RIO_REGION_ID,
    seller_kind="частник",
)

RIO_AUTORU = ListingPayload(
    listing_id="autoru-1093281",
    platform="Авто.ру",
    url="https://auto.example/cars/used/sale/1093281",
    price=799_000,
    published_on=SCENARIO_TODAY - timedelta(days=3),
    price_changed_on=SCENARIO_TODAY - timedelta(days=3),
    vin=RIO_VIN,
    # Госномер Авто.ру в выдаче закрывает — типичная ситуация, уровень 1
    # каскада по этой паре не срабатывает, и склейка держится на VIN и фото.
    plate=None,
    phone_hash=RIO_PHONE_HASH,
    photo_phashes=RIO_PHOTOS_AUTORU,
    mileage_km=92_000,
    year=2017,
    model_id=RIO_MODEL_ID,
    region_id=RIO_REGION_ID,
    seller_kind="частник",
)

RIO_DROM = ListingPayload(
    listing_id="drom-77215508",
    platform="Дром",
    url="https://drom.example/kia/rio/77215508.html",
    price=810_000,
    published_on=SCENARIO_TODAY - timedelta(days=8),
    price_changed_on=SCENARIO_TODAY - timedelta(days=8),
    vin=RIO_VIN,
    plate=None,
    phone_hash=RIO_PHONE_HASH,
    photo_phashes=RIO_PHOTOS_DROM,
    # «≈92 000» против «91 800» — тот же автомобиль, продавец округлил.
    mileage_km=91_800,
    year=2017,
    model_id=RIO_MODEL_ID,
    region_id=RIO_REGION_ID,
    seller_kind="частник",
)

RIO_LISTINGS: tuple[ListingPayload, ...] = (RIO_AVITO, RIO_AUTORU, RIO_DROM)

RIO_PRICE_SPREAD_RUB = 25_000
"""«Разница 25 000 ₽» с экрана B3: 810 000 − 785 000. Число из дизайна."""

RIO_REVIEWS = ReviewsPayload(
    generation_id=RIO_MODEL_ID,
    texts=(load_review("01_rio3_rulevaya_reyka"),),
)

# ─────────────────────────────────────────────────────────────────────────────
# Автомобиль 2 — Hyundai Solaris II, негативный сценарий
# ─────────────────────────────────────────────────────────────────────────────

SOLARIS_PLATE = "У777МН178"
SOLARIS_VIN = "Z94K241CBJR654321"
SOLARIS_MODEL_ID = "hyundai-solaris-2"
SOLARIS_REGION_ID = 78

SOLARIS_PHONE_HASH = "hmac-sha256:b41d0e77aa9c3218"
"""Тот же продавец на трёх площадках — и, по сценарию, ещё на десятке машин."""

SOLARIS_PHOTOS: tuple[int, ...] = tuple(phash ^ 0xFFFFFFFFFFFFFFFF for phash in RIO_PHOTOS)
"""Побитовые дополнения хэшей Rio: расстояние Хэмминга ровно 64 бита.

Значения подобраны так специально. Порог склейки — 6 бит из 64, и фикстуры
должны доказывать не только «похожее склеилось», но и «непохожее не склеилось»
с максимально возможным запасом, чтобы тест не начал мигать от правки порога.
"""

SOLARIS_MILEAGE_HISTORY = (
    MileageRecord(date(2021, 6, 11), 98_000, "техосмотр"),
    MileageRecord(date(2023, 7, 19), 143_000, "ТО у дилера"),
    # Вот она, скрутка: между двумя записями пробег уменьшился на 82 000 км.
    MileageRecord(date(2024, 8, 5), 61_000, "техосмотр"),
    MileageRecord(date(2025, 9, 30), 74_500, "техосмотр"),
)

SOLARIS_ACCIDENTS = (
    AccidentRecord(
        occurred_on=date(2021, 3, 27),
        severity="среднее",
        damage_zone="левый борт, обе двери",
        repaired=True,
    ),
    AccidentRecord(
        occurred_on=date(2023, 11, 8),
        severity="тяжёлое",
        damage_zone="передняя часть, лонжерон, сработали подушки",
        repaired=True,
    ),
)

SOLARIS_GIBDD = RegistryPayload(
    vin=None,
    brand="Hyundai",
    model="Solaris II",
    year=2018,
    engine="1.6 AT",
    owners_count=4,
    mileage_history=SOLARIS_MILEAGE_HISTORY,
    accidents=SOLARIS_ACCIDENTS,
    is_pledged=True,
    is_taxi=True,
    has_restrictions=True,
)

SOLARIS_NOMEROGRAM = NomerogramPayload(
    registry=RegistryPayload(
        vin=SOLARIS_VIN,
        brand="Hyundai",
        model="Solaris II",
        year=2018,
        engine="1.6 AT",
        owners_count=4,
    ),
    photo_history=(
        PhotoSighting(date(2024, 2, 17), "Авито", SOLARIS_PHOTOS[0]),
        PhotoSighting(date(2025, 5, 6), "Авито", SOLARIS_PHOTOS[1]),
        PhotoSighting(SCENARIO_TODAY - timedelta(days=12), "Дром", SOLARIS_PHOTOS[0]),
    ),
)

SOLARIS_AVITO = ListingPayload(
    listing_id="avito-2718281828",
    platform="Авито",
    url="https://avito.example/hyundai_solaris_2018_2718281828",
    price=699_000,
    published_on=SCENARIO_TODAY - timedelta(days=2),
    price_changed_on=SCENARIO_TODAY - timedelta(days=2),
    vin=SOLARIS_VIN,
    plate=SOLARIS_PLATE,
    phone_hash=SOLARIS_PHONE_HASH,
    photo_phashes=SOLARIS_PHOTOS,
    mileage_km=78_000,
    year=2018,
    model_id=SOLARIS_MODEL_ID,
    region_id=SOLARIS_REGION_ID,
    seller_kind="частник",
)

SOLARIS_DROM = ListingPayload(
    listing_id="drom-64129930",
    platform="Дром",
    url="https://drom.example/hyundai/solaris/64129930.html",
    price=715_000,
    published_on=SCENARIO_TODAY - timedelta(days=12),
    price_changed_on=SCENARIO_TODAY - timedelta(days=12),
    vin=SOLARIS_VIN,
    plate=None,
    phone_hash=SOLARIS_PHONE_HASH,
    photo_phashes=SOLARIS_PHOTOS,
    mileage_km=78_000,
    year=2018,
    model_id=SOLARIS_MODEL_ID,
    region_id=SOLARIS_REGION_ID,
    seller_kind="частник",
)

SOLARIS_SOCIAL = ListingPayload(
    listing_id="vk-wall-40551-88231",
    platform="VK",
    url="https://vk.example/wall-40551_88231",
    price=689_000,
    published_on=SCENARIO_TODAY - timedelta(days=5),
    # Борда — источник с худшим качеством: ни VIN, ни госномера, год и пробег
    # вытащены регулярками из текста поста, цена «договорная от 689».
    vin=None,
    plate=None,
    phone_hash=SOLARIS_PHONE_HASH,
    photo_phashes=SOLARIS_PHOTOS[:2],
    mileage_km=78_000,
    year=2018,
    model_id=SOLARIS_MODEL_ID,
    region_id=SOLARIS_REGION_ID,
    seller_kind="",
)

SOLARIS_LISTINGS: tuple[ListingPayload, ...] = (SOLARIS_AVITO, SOLARIS_DROM, SOLARIS_SOCIAL)

SOLARIS_REVIEWS = ReviewsPayload(
    generation_id=SOLARIS_MODEL_ID,
    texts=(load_review("02_solaris_katalizator"),),
)

UNKNOWN_PLATE = "А001АА777"
"""Номер, которого нет ни в одной таблице, — сценарий CC5 «не находится в базах»."""

# ─────────────────────────────────────────────────────────────────────────────
# Таблицы источников
# ─────────────────────────────────────────────────────────────────────────────

_T = TypeVar("_T")


def _table(*rows: tuple[str, str, _T]) -> Mapping[str, _T]:
    """Собирает таблицу фикстур: каждая запись доступна и по госномеру, и по VIN.

    Проверку запускают то с номера (B1), то с VIN (обходной путь CC5), и один и
    тот же автомобиль обязан находиться в обоих случаях.

    Args:
        rows: тройки «госномер, VIN, полезная нагрузка».

    Returns:
        Неизменяемое отображение «канонический ключ → нагрузка».
    """
    built: dict[str, _T] = {}
    for plate, vin, payload in rows:
        built[plate] = payload
        built[vin] = payload
    return MappingProxyType(built)


GIBDD_FIXTURES: Mapping[str, RegistryPayload] = _table(
    (RIO_PLATE, RIO_VIN, RIO_GIBDD),
    (SOLARIS_PLATE, SOLARIS_VIN, SOLARIS_GIBDD),
)

NOMEROGRAM_FIXTURES: Mapping[str, NomerogramPayload] = _table(
    (RIO_PLATE, RIO_VIN, RIO_NOMEROGRAM),
    (SOLARIS_PLATE, SOLARIS_VIN, SOLARIS_NOMEROGRAM),
)

AVITO_FIXTURES: Mapping[str, ListingPayload] = _table(
    (RIO_PLATE, RIO_VIN, RIO_AVITO),
    (SOLARIS_PLATE, SOLARIS_VIN, SOLARIS_AVITO),
)

# У Solaris объявления на Авто.ру нет: площадка ответила, но ничего не нашла.
AUTORU_FIXTURES: Mapping[str, ListingPayload] = _table((RIO_PLATE, RIO_VIN, RIO_AUTORU))

DROM_FIXTURES: Mapping[str, DromPayload] = _table(
    (RIO_PLATE, RIO_VIN, DromPayload(listing=RIO_DROM, reviews=RIO_REVIEWS)),
    (SOLARIS_PLATE, SOLARIS_VIN, DromPayload(listing=SOLARIS_DROM, reviews=SOLARIS_REVIEWS)),
)

# По Rio борды молчат — и это нормальный ответ, а не отказ: на экране B3 авто
# честно значится на трёх площадках, а не на четырёх.
SOCIAL_FIXTURES: Mapping[str, tuple[ListingPayload, ...]] = _table(
    (RIO_PLATE, RIO_VIN, ()),
    (SOLARIS_PLATE, SOLARIS_VIN, (SOLARIS_SOCIAL,)),
)
