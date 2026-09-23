"""Каскад дедупликации объявлений (§5.2 технической документации).

Склейка — не служебная оптимизация, а продаваемая фича: B3 пишет «Одно и то же
авто: совпали VIN, фото и телефон продавца», C1 — «Размещено на 3 площадках»,
D1 — бейдж «на 3 площадках». Поэтому каждый :class:`MatchResult` несёт не только
число, но и список сработавших признаков: основание склейки показывается
пользователю.

**Почему каскад, а не одна формула.** Ни один признак не работает сам по себе:
VIN площадки скрывают, телефон прячут за «показать номер», фото пережимают и
штампуют водяным знаком, пробег и цена у одного авто на трёх площадках
расходятся. Поэтому идём от точного к вероятностному и останавливаемся на первом
сработавшем уровне:

===== ======================================== ============ ==============
Ур.   Признак                                  Уверенность  ``match_reason``
===== ======================================== ============ ==============
0     Нормализованный VIN                      0.99         ``vin``
1     Нормализованный госномер                 0.97         ``plate``
2     HMAC телефона + модель + год             0.90         ``phone``
3     pHash фото: ≥2 фото с Хэммингом ≤ 6      0.88         ``photo_phash``
4     Нечёткое по атрибутам, score ≥ 0.82      score        ``attrs``
===== ======================================== ============ ==============

Два запрета из спеки реализованы как жёсткие вето, потому что ловят самые злые
ошибки: **разные валидные VIN не склеиваются никогда** (два одинаковых авто у
одного дилера — одинаковые фото, один телефон, одна модель), а разные госномера
при отсутствии VIN опускают уверенность до 0.5 и в кластер не отправляются.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum

from avtoklik.matching.plate import normalize_plate
from avtoklik.matching.vin import normalize_vin

__all__ = [
    "ATTR_MATCH_THRESHOLD",
    "MAX_CLUSTER_SIZE",
    "PHASH_MATCH_MIN_PHOTOS",
    "PHASH_MAX_DISTANCE",
    "CanonicalPrice",
    "ListingFeatures",
    "MatchConfidence",
    "MatchResult",
    "attr_similarity",
    "canonical_price",
    "cluster_listings",
    "describe_reasons",
    "hamming_distance",
    "similarity_score",
]

SCORE_VIN = 0.99
SCORE_PLATE = 0.97
SCORE_PHONE = 0.90
SCORE_PHOTO = 0.88
SCORE_PLATE_CONFLICT = 0.5
"""Разные госномера без VIN: не «не совпало», а «нужна ручная проверка»."""

ATTR_MATCH_THRESHOLD = 0.82
"""Порог уровня 4 — он же порог по умолчанию для кластеризации."""

PHASH_MAX_DISTANCE = 6
"""Максимальное расстояние Хэмминга между pHash из 64 бит."""

PHASH_MATCH_MIN_PHOTOS = 2
"""Одного совпавшего фото мало: площадки переиспользуют кадры из каталога."""

MAX_CLUSTER_SIZE = 8
"""Защита от «слипания» половины базы: площадок всего 4–6, больше неоткуда."""

STRONG_CLUSTER_SIGNALS = frozenset({"vin", "plate", "phone"})
"""Признаки уровней 0–2: без одного из них кластер не растёт больше трёх."""

MILEAGE_SIGMA_KM = 3000.0
"""«≈92 000» против «92 400» — это один автомобиль, разницу гасит гауссиана."""

PRICE_DIVERGENCE_LIMIT = 0.15
PRICE_DIVERGENCE_PENALTY = 0.20

_ATTR_WEIGHTS = {"model": 0.25, "year": 0.15, "mileage": 0.20, "region": 0.10}
_ATTR_WEIGHT_SUM = sum(_ATTR_WEIGHTS.values())

_LIVE_WINDOW = timedelta(days=3)
_SAME_DAY_WINDOW = timedelta(days=1)

_REASON_LABELS = {
    "vin": "VIN",
    "plate": "госномер",
    "phone": "телефон продавца",
    "photo_phash": "фото",
    "attrs": "модель, год и пробег",
    "vin_conflict": "разные VIN",
    "plate_conflict": "разные госномера",
    "same_source_duplicate": "одна и та же площадка",
}


class MatchConfidence(Enum):
    """Уровень уверенности в том, что два объявления — один автомобиль."""

    EXACT = "exact"
    """Уровни 0–1: совпал идентификатор автомобиля. Склеиваем без вопросов."""

    STRONG = "strong"
    """Уровни 2–3: телефон или фото. Склеиваем, показываем основание."""

    WEAK = "weak"
    """Уровень 4 либо конфликт госномеров: в UI как склейку не показываем."""

    NO_MATCH = "no_match"
    """Разные автомобили."""


@dataclass(frozen=True, slots=True)
class ListingFeatures:
    """Признаки объявления, нужные для сопоставления.

    Все поля опциональны: площадки отдают разный набор, и «нет VIN» — норма,
    а не ошибка данных.
    """

    vin: str | None = None
    phone_hash: str | None = None
    """HMAC телефона, а не сам телефон: хранить номера продавцов нам незачем."""

    photo_phashes: list[int] = field(default_factory=list)
    """Перцептивные хэши фото, 64 бита каждый."""

    model_id: str = ""
    year: int = 0
    mileage_km: int = 0
    region_id: str = ""
    price: int = 0

    plate: str | None = None
    """Госномер — уровень 1 каскада; площадки отдают его редко, но отдают."""

    listing_id: str = ""
    source_id: str = ""
    """Площадка: ``avito``, ``autoru``, ``drom``…"""

    status: str = "active"
    last_seen_at: datetime | None = None
    last_price_change_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Результат сравнения пары объявлений."""

    score: float
    """Уверенность 0..1."""

    confidence: MatchConfidence
    reasons: list[str]
    """Все сработавшие признаки в порядке каскада — это текст для B3."""

    match_reason: str
    """Признак, принявший решение (или ``none`` / ``*_conflict``)."""

    def explain(self) -> str:
        """Готовая фраза для экрана B3: «совпали VIN, фото и телефон продавца»."""
        return describe_reasons(self.reasons)


@dataclass(frozen=True, slots=True)
class CanonicalPrice:
    """Каноническая цена кластера и разброс по площадкам (B3, C1)."""

    price: int
    listing: ListingFeatures
    price_min: int
    price_max: int

    @property
    def spread(self) -> int:
        """Разброс цен внутри кластера — «разброс 25 000 ₽» на экране B3."""
        return self.price_max - self.price_min


def describe_reasons(reasons: list[str]) -> str:
    """Переводит коды признаков в человеческую фразу для UI."""
    if not reasons:
        return "совпадений нет"
    labels = [_REASON_LABELS.get(code, code) for code in reasons]
    if len(labels) == 1:
        return f"совпал {labels[0]}"
    return "совпали " + ", ".join(labels[:-1]) + " и " + labels[-1]


def hamming_distance(a: int, b: int) -> int:
    """Расстояние Хэмминга между двумя перцептивными хэшами.

    Args:
        a: первый хэш (неотрицательное целое).
        b: второй хэш.

    Returns:
        Число различающихся бит.

    Raises:
        ValueError: если хэш отрицательный — pHash беззнаковый, отрицательное
            значение означает ошибку на стороне вызывающего кода.
    """
    if a < 0 or b < 0:
        raise ValueError("перцептивный хэш не может быть отрицательным")
    return (a ^ b).bit_count()


def _matched_photo_count(a: ListingFeatures, b: ListingFeatures) -> int:
    """Считает, сколько фото нашли себе пару с расстоянием ≤ порога.

    Пары считаются взаимно однозначными: одно фото объявления ``b`` не может
    «закрыть» сразу несколько фото объявления ``a``, иначе серия почти одинаковых
    ракурсов дала бы ложные два совпадения.
    """
    used: set[int] = set()
    matched = 0
    for left in a.photo_phashes:
        for index, right in enumerate(b.photo_phashes):
            if index in used:
                continue
            if hamming_distance(left, right) <= PHASH_MAX_DISTANCE:
                used.add(index)
                matched += 1
                break
    return matched


def _gauss(delta: float, sigma: float) -> float:
    """Гауссиана: 1.0 при нулевой разнице, плавно убывает к нулю."""
    return math.exp(-(delta**2) / (2 * sigma**2))


def attr_similarity(a: ListingFeatures, b: ListingFeatures) -> float:
    """Нечёткий скоринг по атрибутам — уровень 4 каскада.

    Веса взяты из §5.2 и нормированы: спека учитывает ещё код двигателя, цвет
    кузова, геокоординаты и TF-IDF по описанию, которых в :class:`ListingFeatures`
    нет (они приходят не от всех площадок). Оставшиеся веса поделены на их сумму,
    чтобы порог 0.82 сохранил смысл «почти всё совпало». Как только накопится
    размеченный модераторами набор пар, эти веса заменяются обученным
    классификатором — здесь они стартовые, из здравого смысла.

    Args:
        a: первое объявление.
        b: второе объявление.

    Returns:
        Оценку схожести 0..1.
    """
    raw = 0.0
    if a.model_id and a.model_id == b.model_id:
        raw += _ATTR_WEIGHTS["model"]
    if a.year and a.year == b.year:
        raw += _ATTR_WEIGHTS["year"]
    raw += _ATTR_WEIGHTS["mileage"] * _gauss(float(a.mileage_km - b.mileage_km), MILEAGE_SIGMA_KM)
    if a.region_id and a.region_id == b.region_id:
        # Замена geo_proximity из спеки: точных координат у нас нет, регион —
        # самое близкое, что отдают все площадки.
        raw += _ATTR_WEIGHTS["region"]

    score = raw / _ATTR_WEIGHT_SUM

    # Штраф: слишком разная цена — скорее всего, разные машины.
    top_price = max(a.price, b.price)
    if top_price > 0 and abs(a.price - b.price) / top_price > PRICE_DIVERGENCE_LIMIT:
        score -= PRICE_DIVERGENCE_PENALTY

    return min(1.0, max(0.0, score))


def _identity_pair(left: str | None, right: str | None) -> tuple[str, str] | None:
    """Возвращает пару непустых значений или ``None``, если одного из них нет."""
    if left and right:
        return left, right
    return None


def similarity_score(a: ListingFeatures, b: ListingFeatures) -> MatchResult:
    """Сравнивает два объявления каскадом уровней 0–4.

    Решение принимает самый точный сработавший уровень, но в ``reasons``
    собираются **все** сработавшие признаки: экран B3 обещает пользователю
    «совпали VIN, фото и телефон продавца», то есть перечисление, а не один код.

    Args:
        a: первое объявление.
        b: второе объявление.

    Returns:
        :class:`MatchResult` с оценкой, уверенностью и основаниями.
    """
    reasons: list[str] = []

    vins = _identity_pair(normalize_vin(a.vin or ""), normalize_vin(b.vin or ""))
    if vins is not None:
        if vins[0] != vins[1]:
            # Жёсткое вето: два валидных разных VIN — это два автомобиля, чем бы
            # ни совпали фото и телефон (типичный случай — дилер с двумя
            # одинаковыми машинами и одной фотосессией).
            return MatchResult(0.0, MatchConfidence.NO_MATCH, ["vin_conflict"], "vin_conflict")
        reasons.append("vin")

    plates = _identity_pair(normalize_plate(a.plate or ""), normalize_plate(b.plate or ""))
    plate_conflict = plates is not None and plates[0] != plates[1]
    if plates is not None and plates[0] == plates[1]:
        reasons.append("plate")

    # Уровень 2 — телефон сам по себе слаб (перекуп торгует десятком машин),
    # поэтому спека требует к нему совпадения модели и года.
    phones = _identity_pair(a.phone_hash, b.phone_hash)
    if (
        phones is not None
        and phones[0] == phones[1]
        and (a.model_id, a.year) == (b.model_id, b.year)
    ):
        reasons.append("phone")

    if _matched_photo_count(a, b) >= PHASH_MATCH_MIN_PHOTOS:
        reasons.append("photo_phash")

    attrs = attr_similarity(a, b)
    if attrs >= ATTR_MATCH_THRESHOLD:
        reasons.append("attrs")

    if a.source_id and a.source_id == b.source_id and reasons:
        # Продавец пересоздал объявление: склеивать можно, но для скоринга
        # перекупа это отдельный сигнал, поэтому помечаем явно.
        reasons.append("same_source_duplicate")

    if "vin" in reasons:
        return MatchResult(SCORE_VIN, MatchConfidence.EXACT, reasons, "vin")
    if plate_conflict:
        # VIN не совпал (иначе вышли бы выше), а номера разные — понижаем до 0.5
        # и в кластер без ручного подтверждения не отправляем.
        return MatchResult(
            SCORE_PLATE_CONFLICT,
            MatchConfidence.WEAK,
            ["plate_conflict", *reasons],
            "plate_conflict",
        )
    if "plate" in reasons:
        return MatchResult(SCORE_PLATE, MatchConfidence.EXACT, reasons, "plate")
    if "phone" in reasons:
        return MatchResult(SCORE_PHONE, MatchConfidence.STRONG, reasons, "phone")
    if "photo_phash" in reasons:
        return MatchResult(SCORE_PHOTO, MatchConfidence.STRONG, reasons, "photo_phash")
    if "attrs" in reasons:
        return MatchResult(attrs, MatchConfidence.WEAK, reasons, "attrs")
    return MatchResult(attrs, MatchConfidence.NO_MATCH, [], "none")


def canonical_price(
    listings: list[ListingFeatures],
    now: datetime | None = None,
) -> CanonicalPrice:
    """Выбирает каноническую цену кластера по правилу §5.2.

    Правило спеки — «самое свежее, при равенстве — самое дешёвое»:

    1. берём только активные объявления, виденные за последние трое суток;
    2. если живых нет — показываем минимальную цену как есть (всё протухло);
    3. среди живых сортируем по дате последнего изменения цены (а если её нет —
       по дате последнего просмотра) и берём самое свежее;
    4. среди одинаково свежих (в пределах суток) берём минимальную цену.

    Логика защищает и пользователя (не покажем цену, которую продавец уже
    снизил), и нас (мы показываем реально существующую цену конкретной площадки,
    а рядом честно перечисляем остальные). Пример B3: Авито «сегодня» 785 000,
    Авто.ру «3 дня» 799 000, Дром «8 дней» 810 000 — канон Авито.

    Args:
        listings: объявления одного кластера.
        now: текущий момент; по умолчанию — ``datetime.now(UTC)``. Параметр
            вынесен наружу ради воспроизводимых тестов.

    Returns:
        Каноническую цену вместе с минимумом, максимумом и разбросом.

    Raises:
        ValueError: если кластер пуст.
    """
    if not listings:
        raise ValueError("кластер не может быть пустым")

    moment = now if now is not None else datetime.now(UTC)
    prices = [item.price for item in listings]

    # Пара (объявление, дата просмотра) вместо голого списка — чтобы дальше не
    # тащить за собой Optional и не проверять его в каждом сравнении.
    live: list[tuple[ListingFeatures, datetime]] = []
    for item in listings:
        seen = item.last_seen_at
        if item.status == "active" and seen is not None and seen > moment - _LIVE_WINDOW:
            live.append((item, seen))

    if not live:
        stale_choice = min(listings, key=lambda item: item.price)
        return CanonicalPrice(stale_choice.price, stale_choice, min(prices), max(prices))

    def freshness(pair: tuple[ListingFeatures, datetime]) -> datetime:
        item, seen = pair
        changed = item.last_price_change_at
        return changed if changed is not None else seen

    _, freshest_seen = max(live, key=freshness)
    same_day = [pair for pair in live if abs(freshest_seen - pair[1]) < _SAME_DAY_WINDOW]
    choice = min(same_day, key=lambda pair: pair[0].price)[0]
    return CanonicalPrice(choice.price, choice, min(prices), max(prices))


def _can_join(
    cluster: list[ListingFeatures],
    candidate: ListingFeatures,
    threshold: float,
) -> bool:
    """Решает, присоединять ли объявление к существующему кластеру."""
    if len(cluster) >= MAX_CLUSTER_SIZE:
        # Жёсткий предел размера — классическая защита от транзитивного
        # «слипания» половины базы в один кластер.
        return False

    best: MatchResult | None = None
    for member in cluster:
        result = similarity_score(member, candidate)
        if result.match_reason in {"vin_conflict", "plate_conflict"}:
            # Достаточно одного конфликта с любым членом кластера, чтобы
            # запретить вход: иначе объявление «протащит» себя через соседа.
            return False
        passes = result.score >= threshold and result.confidence is not MatchConfidence.NO_MATCH
        if passes and (best is None or result.score > best.score):
            best = result

    if best is None:
        return False
    # Кластер больше трёх обязан держаться на признаке уровня 0–2, иначе
    # нечёткие совпадения затянут в него половину блока-кандидата.
    return len(cluster) < 3 or best.match_reason in STRONG_CLUSTER_SIGNALS


def cluster_listings(
    listings: list[ListingFeatures],
    threshold: float = ATTR_MATCH_THRESHOLD,
) -> list[list[ListingFeatures]]:
    """Жадно раскладывает объявления по кластерам «один автомобиль».

    Объявление присоединяется к первому кластеру, с которым прошло порог и не
    получило вето (конфликт VIN или госномера). Это упрощённая версия
    инкрементальной схемы из §5.2: в бою кластеры живут в Postgres и
    объединяются транзитивно через union-find, здесь же — однопроходная жадная
    склейка, достаточная для офлайн-прогонов и тестов.

    Args:
        listings: объявления-кандидаты (в бою — уже отобранные по блокирующему
            ключу ``(model_id, year, region_id)``).
        threshold: минимальная оценка для склейки.

    Returns:
        Список кластеров; порядок объявлений внутри кластера — как во входе.
    """
    clusters: list[list[ListingFeatures]] = []
    for item in listings:
        for cluster in clusters:
            if _can_join(cluster, item, threshold):
                cluster.append(item)
                break
        else:
            clusters.append([item])
    return clusters
