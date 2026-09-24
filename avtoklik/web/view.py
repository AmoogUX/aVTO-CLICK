"""Модели представления: перевод доменных данных в то, что читает человек.

Слой существует ради одного правила дизайн-системы (§3.4): **ни один статус
не передаётся одним лишь цветом — всегда цвет плюс глиф, слово или форма.**
Если бы бейджи собирались в шаблоне, правило пришлось бы соблюдать в каждом
месте вручную и оно бы поехало на третьей правке вёрстки. Здесь же «−7%»
физически не может существовать без знака `↓` и слова «ниже рынка»: они
лежат в одном объекте.

Второе правило, зашитое здесь же: форматирование чисел. Цена набирается
неразрывными пробелами, иначе «785 000 ₽» переносится по строке в самом
неудачном месте — а цена на карточке это герой экрана (§1.1).
"""

from __future__ import annotations

from dataclasses import dataclass

from avtoklik.api.schemas import RepairForecastOut
from avtoklik.pricing import PriceAnchors
from avtoklik.service.card import CheckOffer, ProductCard, build_product_card
from avtoklik.service.sell import CheckIssue, DraftStatus, FilledField, SellDraft, suggest_price
from avtoklik.service.showcase import ShowcaseCar, region_title

__all__ = [
    "MARKET_BAND",
    "SUSPICIOUS_DISCOUNT",
    "AnchorView",
    "CardView",
    "SellView",
    "ShowcaseItemView",
    "StatusBadge",
    "build_card_view",
    "build_sell_view",
    "build_showcase_view",
    "format_mileage",
    "format_money",
    "market_badge",
    "seller_badge",
]

#: Полоса вокруг оценки, внутри которой цена считается рыночной.
#: Уже цены объявлений скачут на проценты сами по себе, и бейдж «+1% выше
#: рынка» был бы шумом, выдаваемым за сигнал.
MARKET_BAND = 0.03

#: Насколько ниже оценки цена перестаёт быть выгодой и становится вопросом.
#: По §5.D продукт не имеет права называть такую цену «выгодной»: за скидкой
#: в четверть стоимости обычно стоит то, что в объявлении не написано.
SUSPICIOUS_DISCOUNT = 0.25

_NBSP = " "


def format_money(value: int | None) -> str:
    """Цена с неразрывными пробелами: «785 000 ₽»."""
    if value is None:
        return "—"
    return f"{value:,}".replace(",", _NBSP) + f"{_NBSP}₽"


def format_mileage(value: int | None) -> str:
    """Пробег: «92 000 км». ``None`` — продавец его не указал."""
    if value is None:
        return "пробег не указан"
    return f"{value:,}".replace(",", _NBSP) + f"{_NBSP}км"


@dataclass(frozen=True, slots=True)
class StatusBadge:
    """Метка-смысл: тон, глиф и слово — три носителя одного статуса.

    `tone` попадает в CSS-класс и выбирает семантический токен (§2.7).
    `glyph` и `text` дублируют тот же смысл для тех, кто цвет не различает,
    и для скринридера: `label` — это то, что читается вслух целиком.
    """

    tone: str
    glyph: str
    text: str

    @property
    def label(self) -> str:
        return f"{self.glyph} {self.text}".strip()


def market_badge(price_rub: int, market_price_rub: int | None) -> StatusBadge:
    """Отношение цены объявления к оценке АвтоКлик (компонент MarketBadge).

    Пять состояний, и «нет оценки» — полноценное из них: по редкой модели
    ценовой модели может не хватить данных, и молчание тут честнее, чем
    бейдж «в рынке», выданный от незнания.
    """
    if not market_price_rub:
        return StatusBadge(tone="unknown", glyph="—", text="нет оценки")
    delta = (price_rub - market_price_rub) / market_price_rub
    percent = round(abs(delta) * 100)
    if delta <= -SUSPICIOUS_DISCOUNT:
        return StatusBadge(tone="warning", glyph="⚠", text="подозрительно низкая цена")
    if delta <= -MARKET_BAND:
        return StatusBadge(tone="benefit", glyph="↓", text=f"−{percent}% ниже рынка")
    if delta >= MARKET_BAND:
        return StatusBadge(tone="warning", glyph="↑", text=f"+{percent}% выше рынка")
    return StatusBadge(tone="neutral", glyph="≈", text="в рынке")


def seller_badge(seller_kind: str, risk_note: str) -> StatusBadge | None:
    """Кто продавец (компонент SellerBadge).

    Формулировка признака перепродажи намеренно предположительная. Метка
    «перекуп» — это утверждение о человеке, и по §8 ответ на него ещё не
    получен от юристов; до тех пор интерфейс описывает наблюдаемый факт
    («объявлений с одного телефона»), а не даёт характеристику.
    """
    if risk_note:
        return StatusBadge(tone="risk", glyph="⚠", text="признаки перепродажи")
    if seller_kind == "салон":
        return StatusBadge(tone="neutral", glyph="✓", text="салон")
    if seller_kind == "частник":
        return StatusBadge(tone="neutral", glyph="✓", text="частник")
    return None


@dataclass(frozen=True, slots=True)
class ShowcaseItemView:
    """Карточка объявления в ленте витрины (компонент ListingCard)."""

    listing_id: str
    title: str
    subtitle: str
    price: str
    mileage: str
    platform: str
    region: str
    market: StatusBadge
    seller: StatusBadge | None
    risk_note: str
    also_on: tuple[str, ...]

    @property
    def has_risk(self) -> bool:
        return bool(self.risk_note)


@dataclass(frozen=True, slots=True)
class CardView:
    """Карточка автомобиля целиком (экран C1).

    Порядок полей повторяет порядок блоков на экране, и это не косметика:
    5.5а требует, чтобы бесплатная часть (`repair`) шла **до** предложения
    купить проверку (`check`). Человек сначала получает пользу, потом видит
    цену следующего шага.
    """

    listing_id: str
    title: str
    subtitle: str
    price: str
    market_price: str
    market: StatusBadge
    seller: StatusBadge | None
    mileage: str
    year: str
    platform: str
    region: str
    url: str
    risk_note: str
    repair: RepairForecastOut | None
    repair_range: str
    #: Строки с суммами — то, из чего складывается прогноз.
    repair_items: tuple[str, ...]
    #: Оговорки расчёта. Отделены от сумм намеренно: строка «встречаемость
    #: ещё не откалибрована», поставленная в один ряд с болячками, читается
    #: как четвёртая болячка ценой в ноль рублей.
    repair_notes: tuple[str, ...]
    check: CheckOffer
    check_price: str
    also_on: tuple[str, ...]

    @property
    def has_risk(self) -> bool:
        return bool(self.risk_note)


def _subtitle(car: ShowcaseCar) -> str:
    """Строка под названием: год и пробег — то, по чему сравнивают в ленте."""
    parts = [str(car.listing.year)] if car.listing.year else []
    parts.append(format_mileage(car.listing.mileage_km))
    return " · ".join(parts)


def build_showcase_view(cars: tuple[ShowcaseCar, ...]) -> tuple[ShowcaseItemView, ...]:
    """Собрать ленту витрины.

    Внешние источники не опрашиваются: витрина обязана открываться мгновенно
    и не стоить нам платных запросов (5.5а).
    """
    return tuple(
        ShowcaseItemView(
            listing_id=car.listing_id,
            title=car.title,
            subtitle=_subtitle(car),
            price=format_money(car.listing.price),
            mileage=format_mileage(car.listing.mileage_km),
            platform=car.listing.platform,
            region=region_title(car.listing.region_id),
            market=market_badge(car.listing.price, car.market_price_rub),
            seller=seller_badge(car.seller_kind, car.risk_note),
            risk_note=car.risk_note,
            also_on=car.also_on,
        )
        for car in cars
    )


def build_card_view(car: ShowcaseCar, *, check_price_rub: int = 199) -> CardView:
    """Собрать экран C1 по объявлению витрины."""
    card: ProductCard = build_product_card(
        car.listing, car.generation_id, check_price_rub=check_price_rub
    )
    repair = card.repair
    repair_range = (
        f"{format_money(repair.low_rub)} — {format_money(repair.high_rub)}" if repair else ""
    )
    lines = repair.lines if repair else ()
    repair_items = tuple(line for line in lines if line.rstrip().endswith("₽"))
    repair_notes = tuple(line for line in lines if not line.rstrip().endswith("₽"))
    return CardView(
        listing_id=card.listing_id,
        title=car.title,
        subtitle=_subtitle(car),
        price=format_money(card.price_rub),
        market_price=format_money(car.market_price_rub),
        market=market_badge(card.price_rub, car.market_price_rub),
        seller=seller_badge(car.seller_kind, car.risk_note),
        mileage=format_mileage(card.mileage_km),
        year=str(card.year) if card.year else "—",
        platform=card.platform,
        region=region_title(car.listing.region_id),
        url=car.listing.url,
        risk_note=car.risk_note,
        repair=repair,
        repair_range=repair_range,
        repair_items=repair_items,
        repair_notes=repair_notes,
        check=card.check,
        check_price=format_money(card.check.price_rub),
        also_on=car.also_on,
    )


@dataclass(frozen=True, slots=True)
class AnchorView:
    """Точка шкалы «быстрее ↔ дороже» в том виде, в каком её читают."""

    price: str
    price_rub: int
    days: str
    caption: str


@dataclass(frozen=True, slots=True)
class SellView:
    """Экран продажи: что заполнено, что придержано, сколько просить."""

    draft_id: str
    title: str
    subject_type: str | None
    subject_value: str | None
    filled: tuple[FilledField, ...]
    withheld: tuple[str, ...]
    ownership_confirmed: bool
    mileage: str
    year: str
    region: str
    #: ``None`` — сегмент слишком тонкий, оценки нет, цену назначает продавец.
    anchors: tuple[AnchorView, ...]
    recommended: AnchorView | None
    reasons: tuple[str, ...]
    price: str
    price_rub: int | None
    status: DraftStatus
    issues: tuple[CheckIssue, ...]
    listing_id: str

    @property
    def is_published(self) -> bool:
        return self.status is DraftStatus.PUBLISHED

    @property
    def is_rejected(self) -> bool:
        return self.status is DraftStatus.REJECTED

    @property
    def has_estimate(self) -> bool:
        return self.recommended is not None


def _anchor_view(price_rub: int, days: str, caption: str) -> AnchorView:
    return AnchorView(
        price=format_money(price_rub), price_rub=price_rub, days=days, caption=caption
    )


def build_sell_view(draft: SellDraft) -> SellView:
    """Собрать экран продажи по черновику.

    Шкала цен строится здесь, а не в шаблоне, по той же причине, что и бейджи:
    правило 5.3.3 «срок не показывается точнее целого дня» должно жить в одном
    месте, а не повторяться в каждой вёрстке.
    """
    anchors: PriceAnchors | None = suggest_price(draft)
    scale: tuple[AnchorView, ...] = ()
    recommended: AnchorView | None = None
    reasons: tuple[str, ...] = ()
    if anchors is not None:
        scale = (
            _anchor_view(anchors.fast.price_rub, anchors.fast.days_label, "быстрее"),
            _anchor_view(
                anchors.recommended.price_rub, anchors.recommended.days_label, "рекомендуем"
            ),
            _anchor_view(anchors.slow.price_rub, anchors.slow.days_label, "дороже"),
        )
        recommended = scale[1]
        reasons = anchors.estimate.reasons
    return SellView(
        draft_id=draft.draft_id,
        title=draft.title or "Автомобиль",
        subject_type=draft.subject_type,
        subject_value=draft.subject_value,
        filled=draft.filled,
        withheld=draft.withheld,
        ownership_confirmed=draft.ownership_confirmed,
        mileage=format_mileage(draft.mileage_km),
        year=str(draft.year) if draft.year else "—",
        region=region_title(draft.region_id),
        anchors=scale,
        recommended=recommended,
        reasons=reasons,
        price=format_money(draft.price_rub),
        price_rub=draft.price_rub,
        status=draft.status,
        issues=draft.issues,
        listing_id=f"avtoklik-{draft.draft_id}",
    )
