"""Флоу продажи: черновик объявления, автозаполнение, цена, публикация.

Зачем это в релизе 1. Витрина, собранная только из чужих объявлений, зависит
от переговоров с площадками, которые могут не состояться. Свои объявления —
второй источник наполнения, не зависящий ни от чьего разрешения, и заодно
единственный источник **точной** разметки для прогноза срока продажи: у своих
объявлений мы видим весь жизненный цикл, включая факт и цену сделки (5.3.2).

Ограничение, встроенное в модуль, а не оставленное на интерфейс
---------------------------------------------------------------
Автозаполнение по госномеру — это выдача данных об автомобиле человеку,
который ввёл номер. Владение при этом никак не проверяется, а по номеру
чужой машины из ГИБДД приходит число владельцев и история ДТП. Поэтому
:func:`autofill` разделяет ответ на две части: сведения о модели отдаются
всегда, сведения об истории конкретного автомобиля — только после
подтверждения владения. Забыть это ограничение в шаблоне нельзя: закрытых
полей просто нет в ответе, пока не передано подтверждение.

Чего здесь нет
--------------
Очереди человеческой модерации (E-13) и загрузки фотографий (T-12-2).
Публикация проходит автоматические проверки и сразу попадает в витрину;
когда появится очередь, между ними встанет статус «на модерации», и
состояние :data:`DraftStatus.PUBLISHED` начнёт выставлять модератор.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum

from avtoklik.matching.plate import normalize_plate
from avtoklik.matching.vin import normalize_vin
from avtoklik.pricing import PriceAnchors, PriceEstimate, build_anchors, estimate_price
from avtoklik.service.payloads import ListingPayload, RegistryPayload
from avtoklik.service.showcase import (
    OWN_PLATFORM,
    ShowcaseCar,
    generation_for_model,
    register_published,
)
from avtoklik.service.sources.fixtures import GIBDD_FIXTURES

__all__ = [
    "MAX_PLAUSIBLE_MILEAGE_KM",
    "Autofill",
    "CheckIssue",
    "DraftStatus",
    "FilledField",
    "SellDraft",
    "autofill",
    "create_draft",
    "get_draft",
    "publish_draft",
    "reset_drafts",
    "run_auto_checks",
    "set_price",
    "suggest_price",
]

#: Выше этого пробега объявление почти наверняка содержит опечатку.
MAX_PLAUSIBLE_MILEAGE_KM = 1_000_000

#: Ниже этого — тоже опечатка (или машина не б/у, и ей здесь не место).
MIN_PLAUSIBLE_MILEAGE_KM = 100


class DraftStatus(StrEnum):
    """Состояние черновика.

    `REJECTED` — не тупик, а список правок (CC6): каждое замечание несёт то,
    что нужно исправить, и черновик остаётся редактируемым.
    """

    DRAFT = "draft"
    PUBLISHED = "published"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class FilledField:
    """Поле, которое сервис заполнил сам.

    `source` показывается рядом со значением: продавец должен видеть, откуда
    сервис это взял, иначе автозаполнение выглядит угадыванием.
    """

    name: str
    label: str
    value: str
    source: str


@dataclass(frozen=True, slots=True)
class Autofill:
    """Результат автозаполнения по госномеру или VIN.

    `withheld` — поля, которые сервис знает, но не отдаёт без подтверждения
    владения. Список не пустой ровно тогда, когда владение не подтверждено:
    интерфейсу нужно объяснить продавцу, что он получит после подтверждения,
    не показывая самих значений.
    """

    found: bool
    subject_type: str | None = None
    subject_value: str | None = None
    model_id: str | None = None
    title: str = ""
    filled: tuple[FilledField, ...] = ()
    withheld: tuple[str, ...] = ()
    ownership_confirmed: bool = False


@dataclass(frozen=True, slots=True)
class CheckIssue:
    """Замечание автопроверки: что не так и что с этим делать (§1.6)."""

    field: str
    problem: str
    fix: str


@dataclass(frozen=True, slots=True)
class SellDraft:
    """Черновик объявления.

    Неизменяемый: каждое изменение порождает новый черновик с тем же
    идентификатором. Так «черновик создан вчера, 21:14» (T-12-5) остаётся
    восстановимым состоянием, а не результатом мутаций непонятного порядка.
    """

    draft_id: str
    created_on: date
    subject_type: str | None = None
    subject_value: str | None = None
    model_id: str | None = None
    title: str = ""
    year: int | None = None
    mileage_km: int | None = None
    region_id: int | None = None
    description: str = ""
    price_rub: int | None = None
    ownership_confirmed: bool = False
    status: DraftStatus = DraftStatus.DRAFT
    issues: tuple[CheckIssue, ...] = field(default_factory=tuple)
    #: Результат автозаполнения, сохранённый вместе с черновиком: продавец
    #: возвращается к нему по ссылке через час, и повторно дёргать источник
    #: ради тех же четырёх полей незачем.
    filled: tuple[FilledField, ...] = field(default_factory=tuple)
    #: Что придержано до подтверждения владения — названия, без значений.
    withheld: tuple[str, ...] = field(default_factory=tuple)


# Каталог моделей. Пока он маленький и живёт в коде: настоящий справочник
# комплектаций — отдельная задача (T-12-1), и до неё ответ ГИБДД «Kia / Rio III»
# надо как-то превращать в ключ модели.
_CATALOGUE: dict[tuple[str, str], tuple[str, str]] = {
    ("Kia", "Rio III"): ("kia-rio-3", "Kia Rio III"),
    ("Hyundai", "Solaris II"): ("hyundai-solaris-2", "Hyundai Solaris II"),
    ("Škoda", "Octavia A7"): ("skoda-octavia-a7", "Škoda Octavia A7"),
}

_PHONE = re.compile(r"(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}")
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)

_DRAFTS: dict[str, SellDraft] = {}


def reset_drafts() -> None:
    """Очистить хранилище черновиков. Нужно тестам: хранилище в памяти общее."""
    _DRAFTS.clear()


def _subject_of(raw: str) -> tuple[str, str] | tuple[None, None]:
    """Опознать, что ввёл продавец: госномер или VIN."""
    vin = normalize_vin(raw)
    if vin:
        return "vin", vin
    plate = normalize_plate(raw)
    if plate:
        return "plate", plate
    return None, None


def _registry_of(subject_value: str) -> RegistryPayload | None:
    """Ответ регистрационного источника. Фикстуры: живых адаптеров нет."""
    return GIBDD_FIXTURES.get(subject_value)


def autofill(raw_subject: str, *, ownership_confirmed: bool = False) -> Autofill:
    """Заполнить, что можно, по госномеру или VIN.

    Сведения о модели (марка, модель, год, двигатель) отдаются всегда: это
    характеристики выпуска, а не история конкретного автомобиля. Число
    владельцев, ДТП и история пробегов — только при подтверждённом владении.
    Без подтверждения они не «скрыты в интерфейсе», а отсутствуют в ответе.
    """
    subject_type, subject_value = _subject_of(raw_subject)
    if subject_type is None or subject_value is None:
        return Autofill(found=False)

    registry = _registry_of(subject_value)
    if registry is None:
        return Autofill(found=False, subject_type=subject_type, subject_value=subject_value)

    model_id, title = _CATALOGUE.get(
        (registry.brand, registry.model), (None, f"{registry.brand} {registry.model}".strip())
    )
    filled = [
        FilledField("brand", "Марка", registry.brand, "ГИБДД"),
        FilledField("model", "Модель", registry.model, "ГИБДД"),
    ]
    if registry.year:
        filled.append(FilledField("year", "Год выпуска", str(registry.year), "ГИБДД"))
    if registry.engine:
        filled.append(FilledField("engine", "Двигатель", registry.engine, "ГИБДД"))

    withheld: list[str] = []
    if ownership_confirmed:
        if registry.owners_count is not None:
            filled.append(
                FilledField("owners", "Владельцев по ПТС", str(registry.owners_count), "ГИБДД")
            )
        if registry.mileage_history:
            last = max(registry.mileage_history, key=lambda record: record.recorded_on)
            filled.append(
                FilledField("mileage", "Пробег на последнем ТО", f"{last.mileage_km} км", "ГИБДД")
            )
    else:
        # Перечисляем названия, но не значения: продавец должен понимать, что
        # он получит, не получая этого до подтверждения.
        if registry.owners_count is not None:
            withheld.append("число владельцев по ПТС")
        if registry.mileage_history:
            withheld.append("история пробегов")
        if registry.accidents:
            withheld.append("записи о ДТП")

    return Autofill(
        found=True,
        subject_type=subject_type,
        subject_value=subject_value,
        model_id=model_id,
        title=title,
        filled=tuple(filled),
        withheld=tuple(withheld),
        ownership_confirmed=ownership_confirmed,
    )


def create_draft(
    raw_subject: str,
    *,
    mileage_km: int | None = None,
    region_id: int | None = None,
    description: str = "",
    ownership_confirmed: bool = False,
    today: date | None = None,
) -> tuple[SellDraft, Autofill]:
    """Создать черновик и сразу заполнить, что можно.

    Черновик создаётся даже когда автозаполнение ничего не нашло: продавец
    редкой машины обязан иметь возможность заполнить всё руками, иначе
    продукт отказывает ему в услуге из-за собственного незнания.
    """
    found = autofill(raw_subject, ownership_confirmed=ownership_confirmed)
    draft = SellDraft(
        draft_id=str(uuid.uuid4()),
        created_on=today or date.today(),
        subject_type=found.subject_type,
        subject_value=found.subject_value,
        model_id=found.model_id,
        title=found.title,
        year=_year_of(found),
        mileage_km=mileage_km,
        region_id=region_id,
        description=description,
        ownership_confirmed=ownership_confirmed,
        filled=found.filled,
        withheld=found.withheld,
    )
    _DRAFTS[draft.draft_id] = draft
    return draft, found


def _year_of(found: Autofill) -> int | None:
    for item in found.filled:
        if item.name == "year":
            return int(item.value)
    return None


def get_draft(draft_id: str) -> SellDraft | None:
    """Черновик по идентификатору. ``None`` — такого черновика нет."""
    return _DRAFTS.get(draft_id)


def suggest_price(draft: SellDraft) -> PriceAnchors | None:
    """Шкала «быстрее ↔ дороже» для черновика.

    ``None`` — оценки нет: по 5.3.4 интерфейс в этом случае предлагает
    продавцу назначить цену самому, а не подставляет медиану от трёх машин.
    """
    estimate: PriceEstimate | None = estimate_price(draft.model_id, draft.mileage_km)
    if estimate is None:
        return None
    return build_anchors(estimate)


def set_price(draft_id: str, price_rub: int) -> SellDraft | None:
    """Назначить цену. Своя цена разрешена и не ограничена нашей шкалой.

    Ограничивать продавца рекомендацией было бы подменой: оценка — это наш
    совет, а цена — его решение.
    """
    draft = _DRAFTS.get(draft_id)
    if draft is None:
        return None
    if price_rub <= 0:
        raise ValueError("цена должна быть положительной")
    updated = replace(draft, price_rub=price_rub)
    _DRAFTS[draft_id] = updated
    return updated


def run_auto_checks(draft: SellDraft) -> tuple[CheckIssue, ...]:
    """Автоматические проверки перед публикацией (T-13-3).

    Ловят то, что видно из самого черновика. Водяные знаки и чужие фото
    остаются за кадром: фотографий в черновике пока нет.
    """
    issues: list[CheckIssue] = []
    if draft.price_rub is None:
        issues.append(
            CheckIssue(
                field="price",
                problem="Цена не указана",
                fix="Назначьте цену — можно взять нашу рекомендацию",
            )
        )
    if draft.mileage_km is None:
        issues.append(
            CheckIssue(
                field="mileage",
                problem="Пробег не указан",
                fix="Укажите пробег по одометру",
            )
        )
    elif not MIN_PLAUSIBLE_MILEAGE_KM <= draft.mileage_km <= MAX_PLAUSIBLE_MILEAGE_KM:
        issues.append(
            CheckIssue(
                field="mileage",
                problem=f"Пробег {draft.mileage_km} км выглядит опечаткой",
                fix="Проверьте число: обычно это от сотни до нескольких сотен тысяч",
            )
        )
    if _PHONE.search(draft.description):
        issues.append(
            CheckIssue(
                field="description",
                problem="В описании есть телефон",
                fix="Уберите номер: покупатели напишут через сервис, а телефон в тексте "
                "сразу собирают перекупы",
            )
        )
    if _URL.search(draft.description):
        issues.append(
            CheckIssue(
                field="description",
                problem="В описании есть ссылка",
                fix="Уберите ссылку — внешние ссылки в объявлениях не публикуются",
            )
        )
    return tuple(issues)


def publish_draft(draft_id: str) -> SellDraft | None:
    """Опубликовать черновик: автопроверки, затем выход в витрину.

    Замечания не выбрасывают черновик, а переводят его в `REJECTED` вместе со
    списком правок: по CC6 отказ — это to-do, а не тупик.
    """
    draft = _DRAFTS.get(draft_id)
    if draft is None:
        return None
    issues = run_auto_checks(draft)
    if issues:
        rejected = replace(draft, status=DraftStatus.REJECTED, issues=issues)
        _DRAFTS[draft_id] = rejected
        return rejected

    published = replace(draft, status=DraftStatus.PUBLISHED, issues=())
    _DRAFTS[draft_id] = published
    register_published(_as_showcase_car(published))
    return published


def _as_showcase_car(draft: SellDraft) -> ShowcaseCar:
    """Превратить опубликованный черновик в карточку витрины.

    Оценка сохраняется в карточке отдельно от цены: продавец мог назначить
    свою, и покупатель должен видеть обе — иначе бейдж «в рынке» превращается
    в утверждение, которое нечем поверить.
    """
    assert draft.price_rub is not None  # гарантировано автопроверками
    anchors = suggest_price(draft)
    listing = ListingPayload(
        listing_id=f"avtoklik-{draft.draft_id}",
        platform=OWN_PLATFORM,
        url="",
        price=draft.price_rub,
        published_on=draft.created_on,
        vin=draft.subject_value if draft.subject_type == "vin" else None,
        plate=draft.subject_value if draft.subject_type == "plate" else None,
        mileage_km=draft.mileage_km,
        year=draft.year,
        model_id=draft.model_id,
        region_id=draft.region_id,
        seller_kind="частник",
    )
    return ShowcaseCar(
        listing=listing,
        title=draft.title or "Автомобиль",
        generation_id=generation_for_model(draft.model_id),
        market_price_rub=anchors.estimate.fair_price_rub if anchors else None,
        seller_kind="частник",
    )
