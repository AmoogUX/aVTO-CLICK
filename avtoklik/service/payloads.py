"""Контракт полезной нагрузки источников.

Оркестратор проверки (5.1) опрашивает разнородные источники: одни отдают
регистрационные данные, другие — объявления, третьи — отзывы владельцев.
Чтобы сборка вердикта не разбирала сырые словари каждого источника, здесь
зафиксировано, что именно кладётся в ``SourceResult.payload``.

Граница простая: адаптер отвечает за «сходить и разобрать», эти типы — за
«что получилось», а сборка вердикта (:mod:`avtoklik.service.verdict`) — за
«что это значит для пользователя». Разные источники могут давать одно и то
же поле (пробег знают и ГИБДД, и объявление), и решение о том, кому верить,
принимается в сборке вердикта, а не в адаптере.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

__all__ = [
    "AccidentRecord",
    "ListingPayload",
    "MileageRecord",
    "RegistryPayload",
    "ReviewsPayload",
]


@dataclass(frozen=True, slots=True)
class AccidentRecord:
    """Запись о ДТП (экран B3, блок «1 ДТП · лёгкое, зад 2022»)."""

    occurred_on: date
    severity: str  # 'лёгкое' | 'среднее' | 'тяжёлое'
    damage_zone: str
    repaired: bool | None = None


@dataclass(frozen=True, slots=True)
class MileageRecord:
    """Точка истории пробега. Нужна, чтобы отличить честный пробег от скрученного."""

    recorded_on: date
    mileage_km: int
    source_note: str = ""


@dataclass(frozen=True, slots=True)
class RegistryPayload:
    """Данные регистрационного источника (ГИБДД и аналоги).

    Обязательный источник проверки: без него вердикт не выдаётся (5.1).
    """

    vin: str | None = None
    brand: str = ""
    model: str = ""
    year: int | None = None
    engine: str = ""
    owners_count: int | None = None
    mileage_history: tuple[MileageRecord, ...] = field(default_factory=tuple)
    accidents: tuple[AccidentRecord, ...] = field(default_factory=tuple)
    is_pledged: bool | None = None  # залог
    is_taxi: bool | None = None
    has_restrictions: bool | None = None


@dataclass(frozen=True, slots=True)
class ListingPayload:
    """Объявление с площадки.

    ``photo_phashes`` и ``phone_hash`` нужны дедупликации (5.2): по ним
    решается, что объявления на разных площадках — одно и то же авто.
    Сам телефон не хранится и сюда не попадает.
    """

    listing_id: str
    platform: str  # 'Авито' | 'Авто.ру' | 'Дром' | ...
    url: str = ""
    price: int = 0
    published_on: date | None = None
    price_changed_on: date | None = None
    vin: str | None = None
    plate: str | None = None
    phone_hash: str | None = None
    photo_phashes: tuple[int, ...] = field(default_factory=tuple)
    mileage_km: int | None = None
    year: int | None = None
    model_id: str | None = None
    region_id: int | None = None
    seller_kind: str = ""  # 'частник' | 'салон' | ''
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class ReviewsPayload:
    """Корпус отзывов владельцев по модели — вход модуля болячек (5.6)."""

    generation_id: str = ""
    texts: tuple[str, ...] = field(default_factory=tuple)
