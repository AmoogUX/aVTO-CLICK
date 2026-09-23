"""Сборка вердикта проверки — экран B3 (ТЗ, 7.1 ``GET /checks/{id}/verdict``).

Здесь разнородные данные источников (:mod:`avtoklik.service.payloads`),
результат дедупликации (:mod:`avtoklik.matching.dedup`) и прогноз ремонта
(:mod:`avtoklik.knowledge`) превращаются в один экран: «82 из 100 · Можно
смотреть», четыре блока проверок, кластер площадок и честная отметка о том,
по скольким источникам всё это собрано.

Три решения, которые легко потерять при доработке:

* **скоринг вычитает, а не складывает.** Автомобиль по умолчанию хороший
  (100 баллов), и каждый найденный факт отнимает объяснимую величину. Обратный
  порядок («набрать баллов за достоинства») непрозрачен: пользователь не может
  проверить, почему не хватило именно этих баллов;
* **за числом всегда стоит перечень причин.** :class:`ScoreResult` возвращает
  не только балл, но и все сработавшие :class:`ScoreFactor`. Это принцип
  продукта «данные вместо приговора»: вердикт словами — вывод, а не оракул;
* **стоп-фактор — это не «минус много баллов», а другой вердикт.** Залог или
  ограничения ГИБДД блокируют сделку целиком, поэтому они и роняют балл ниже
  порога, и принудительно переключают заголовок — независимо от того, насколько
  хороша машина во всём остальном.

Про прогноз ремонта: схема :class:`~avtoklik.api.schemas.CheckVerdict` поля под
него не имеет, выдумывать поле в чужом модуле нельзя, поэтому
:func:`build_verdict_bundle` отдаёт его отдельным значением
(:class:`RepairNote` внутри :class:`VerdictBundle`). Чтобы прогноз доехал до
экрана B3, схему придётся расширить — см. docstring :func:`build_verdict`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from avtoklik.api.schemas import (
    CheckBlock,
    CheckBlockStatus,
    CheckVerdict,
    DegradedSource,
    ListingCluster,
    ListingPrice,
    PaywallInfo,
    SourcesCoverage,
)
from avtoklik.knowledge import DEFAULT_HORIZON_KM, RepairForecast
from avtoklik.service.payloads import (
    AccidentRecord,
    ListingPayload,
    MileageRecord,
    RegistryPayload,
)

__all__ = [
    "BASE_SCORE",
    "HEADLINE_CAUTION",
    "HEADLINE_GOOD",
    "HEADLINE_STOP",
    "MAX_ACCIDENTS_PENALTY",
    "MAX_OWNERS_PENALTY",
    "MAX_SCORE",
    "MIN_SCORE",
    "OWNER_CHURN_MIN_OWNERS",
    "OWNER_MIN_TENURE_YEARS",
    "PENALTY_ACCIDENT_BY_SEVERITY",
    "PENALTY_ACCIDENT_UNKNOWN",
    "PENALTY_MILEAGE_ROLLBACK",
    "PENALTY_OWNER_CHURN",
    "PENALTY_PER_OWNER_AFTER_FIRST",
    "PENALTY_PLEDGE",
    "PENALTY_RESTRICTIONS",
    "PENALTY_TAXI",
    "SUBTEXT_MIN_PENALTY",
    "ROLLBACK_TOLERANCE_KM",
    "THRESHOLD_CAUTION",
    "THRESHOLD_GOOD",
    "RepairNote",
    "ScoreFactor",
    "ScoreResult",
    "VerdictBundle",
    "build_verdict",
    "build_verdict_bundle",
    "detect_mileage_rollback",
    "score_vehicle",
]

# --------------------------------------------------------------------------- #
# Шкала скоринга                                                              #
# --------------------------------------------------------------------------- #

# Начинаем со 100 и вычитаем за найденное: «ничего не нашли» обязано давать
# ровно 100, иначе пользователь не понимает, чего именно машине не хватило.
BASE_SCORE = 100
MIN_SCORE = 0
MAX_SCORE = 100

# Каждый следующий владелец после первого — это пробел в истории: что с
# машиной делали между продажами, не знает никто. Пять баллов — цена пробела,
# а не наказание за саму перепродажу (вторые руки — норма рынка).
PENALTY_PER_OWNER_AFTER_FIRST = 5
# Потолок: пять и более владельцев уже сказали всё, что могли. Без потолка
# длинный список владельцев в одиночку съедал бы половину шкалы и заслонял
# юридические стоп-факторы, которые для сделки важнее.
MAX_OWNERS_PENALTY = 25

# Владельцы, менявшиеся чаще чем раз в два года, — отдельный сигнал: машину
# перепродают, а не ездят на ней. Умеренный штраф: это подозрение, а не факт.
OWNER_MIN_TENURE_YEARS = 2.0
PENALTY_OWNER_CHURN = 12
# Порог срабатывания: у двух владельцев «средний срок» ещё ничего не значит —
# первый мог купить машину в салоне и продать через год по любой причине.
OWNER_CHURN_MIN_OWNERS = 3

# Тяжесть ДТП различается принципиально, поэтому и вес разный:
#   лёгкое  — кузовная деталь без силовых элементов; на экране B3 это янтарь,
#             а не красный (дизайн-система, §«цвет как приговор»);
#   среднее — несколько зон, вероятен ремонт со снятием узлов;
#   тяжёлое — силовые элементы и геометрия кузова: последствия скрытые, а
#             последующая жизнь автомобиля непредсказуема.
PENALTY_ACCIDENT_BY_SEVERITY = {
    "лёгкое": 12,
    "среднее": 25,
    "тяжёлое": 45,
}
# Тяжесть не указана — считаем по середине. Занижать нельзя (мы не знаем, что
# там было), завышать до тяжёлого — значит наказывать за неполноту данных.
PENALTY_ACCIDENT_UNKNOWN = 25
# Потолок по всем ДТП сразу: аварийная история не должна одна обнулять шкалу,
# иначе битая, но юридически чистая машина и машина в залоге сравняются, а это
# разные для покупателя вещи — вторую нельзя купить вообще.
MAX_ACCIDENTS_PENALTY = 50

# Скрутка обесценивает не только пробег, но и всё, что от него считается:
# прогноз ремонта, оценку цены, ресурс узлов. Поэтому штраф крупный.
PENALTY_MILEAGE_ROLLBACK = 35
# Допуск: сервисные записи округляют одометр («92 000» вместо «92 380»), и
# просадка в пределах этого допуска — шум записи, а не скрутка.
ROLLBACK_TOLERANCE_KM = 500

# Такси сделке не мешает, но означает износ, кратно больший, чем показывает
# пробег: круглосуточная работа, чужие руки, экономия на обслуживании.
PENALTY_TAXI = 25

# Стоп-факторы. Величина подобрана так, чтобы каждый из них в одиночку опускал
# даже идеальную машину ниже THRESHOLD_CAUTION: это не «портит» сделку, а
# блокирует её. Залог — банк вправе забрать машину у нового владельца;
# ограничения — машину не поставить на учёт (тем же флагом ГИБДД отдаёт и
# розыск, поэтому вес общий; появится отдельное поле — появится и константа).
PENALTY_PLEDGE = 65
PENALTY_RESTRICTIONS = 62

# Границы вердикта словами.
THRESHOLD_GOOD = 70
THRESHOLD_CAUTION = 40

# Ниже этого веса фактор в подзаголовок не попадает — см. :func:`_subtext`.
SUBTEXT_MIN_PENALTY = 10

HEADLINE_GOOD = "Можно смотреть"
HEADLINE_CAUTION = "Есть о чём подумать"
HEADLINE_STOP = "Лучше не связываться"

# Порядковая шкала встречаемости болячек для некалиброванного прогноза (5.6.1).
_ORDINAL_FREQUENCY = ((0.30, "часто"), (0.10, "иногда"))
_ORDINAL_FREQUENCY_TAIL = "редко"


# --------------------------------------------------------------------------- #
# Результат скоринга                                                          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ScoreFactor:
    """Один сработавший фактор скоринга — строка объяснения балла.

    `summary` пишется строчными и так, чтобы подставляться в предложение:
    «Один нюанс — лёгкое ДТП в 2022». `blocking` означает стоп-фактор: он не
    просто отнимает баллы, а меняет вердикт словами.
    """

    code: str
    penalty: int
    summary: str
    blocking: bool = False


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """Балл и все причины, по которым он такой.

    Возвращать балл без причин нельзя: на экране показывается вывод, но за ним
    обязаны стоять проверяемые факты — иначе это приговор, а не данные.
    """

    score: int
    factors: tuple[ScoreFactor, ...]

    @property
    def total_penalty(self) -> int:
        """Сумма штрафов до отсечения по границам шкалы."""
        return sum(factor.penalty for factor in self.factors)

    @property
    def blocking_factors(self) -> tuple[ScoreFactor, ...]:
        """Стоп-факторы: их наличие важнее самого балла."""
        return tuple(factor for factor in self.factors if factor.blocking)


@dataclass(frozen=True, slots=True)
class RepairNote:
    """Прогноз ремонта в том виде, в котором его можно показать на экране.

    Существует отдельно от :class:`~avtoklik.api.schemas.CheckVerdict`, потому
    что в схеме ответа поля под прогноз нет (см. :func:`build_verdict`).
    """

    headline: str
    amount_rub: int
    low_rub: int
    high_rub: int
    horizon_km: int
    calibrated: bool
    lines: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VerdictBundle:
    """Всё, что даёт сборка: ответ API плюс то, для чего в схеме места нет."""

    verdict: CheckVerdict
    score: ScoreResult
    repair: RepairNote | None = None


# --------------------------------------------------------------------------- #
# Проверки по данным                                                          #
# --------------------------------------------------------------------------- #


def detect_mileage_rollback(history: Sequence[MileageRecord]) -> bool:
    """Есть ли в истории пробега признаки скрутки.

    Пробег — величина неубывающая по определению: одометр физически не едет
    назад. Значит, любая запись, которая меньше более ранней, — это либо
    скрутка, либо ошибка ввода, и в обоих случаях доверять числу нельзя.

    Записи сортируются по дате (порядок во входе не гарантирован — точки
    приходят из разных источников), совпадающие даты остаются в исходном
    порядке. Просадки в пределах :data:`ROLLBACK_TOLERANCE_KM` игнорируются:
    сервисные записи округляют одометр, и это шум, а не подкрутка.

    Args:
        history: точки истории пробега в любом порядке.

    Returns:
        `True`, если пробег где-то уменьшился со временем.
    """
    if len(history) < 2:
        # Ни одной точки или одна: сравнивать не с чем, а «нет данных» — это не
        # «всё чисто» и не «скрутка». Отвечаем честно: признаков не нашли.
        return False

    ordered = sorted(history, key=lambda record: record.recorded_on)
    peak = ordered[0].mileage_km
    for record in ordered[1:]:
        if record.mileage_km < peak - ROLLBACK_TOLERANCE_KM:
            return True
        # Сравниваем с максимумом, а не с предыдущей точкой: после скрутки
        # показания снова растут, и сравнение соседей заметило бы только один
        # шаг вниз, а «вернулись к прежнему уровню» проглядело бы.
        peak = max(peak, record.mileage_km)
    return False


def _accident_penalty(accident: AccidentRecord) -> int:
    """Штраф за одно ДТП по его тяжести."""
    severity = accident.severity.strip().lower()
    return PENALTY_ACCIDENT_BY_SEVERITY.get(severity, PENALTY_ACCIDENT_UNKNOWN)


def _worst_accident(accidents: Sequence[AccidentRecord]) -> AccidentRecord:
    """Самое тяжёлое ДТП; при равной тяжести — самое свежее (оно ближе к нам)."""
    return max(accidents, key=lambda a: (_accident_penalty(a), a.occurred_on))


def _vehicle_age_years(registry: RegistryPayload, today: date) -> int | None:
    """Возраст автомобиля в годах по году выпуска; `None`, если год неизвестен."""
    if registry.year is None:
        return None
    return max(today.year - registry.year, 0)


def _has_owner_churn(registry: RegistryPayload, today: date) -> bool:
    """Меняли ли владельцев чаще, чем раз в :data:`OWNER_MIN_TENURE_YEARS` года."""
    owners = registry.owners_count
    age = _vehicle_age_years(registry, today)
    if owners is None or age is None or owners < OWNER_CHURN_MIN_OWNERS or age <= 0:
        return False
    return age / owners < OWNER_MIN_TENURE_YEARS


# --------------------------------------------------------------------------- #
# Скоринг                                                                     #
# --------------------------------------------------------------------------- #


def score_vehicle(registry: RegistryPayload, *, today: date | None = None) -> ScoreResult:
    """Посчитать скоринг 0–100 и собрать перечень причин.

    Схема сознательно арифметическая и без весов-коэффициентов: пользователь
    (и поддержка, и суд, если дойдёт) должен уметь сложить штрафы руками и
    получить тот же балл. Все величины — именованные константы модуля с
    обоснованием, почему столько.

    Неизвестное (`None` в регистрационных данных) не штрафуется: отсутствие
    проверки — это не находка. Неполнота показывается пользователю отдельно,
    через :class:`~avtoklik.api.schemas.SourcesCoverage`.

    Args:
        registry: регистрационные данные — обязательный источник проверки.
        today: «сегодня» для расчёта возраста автомобиля; параметр нужен, чтобы
            тесты не зависели от календаря.

    Returns:
        Балл, отсечённый по границам 0..100, и все сработавшие факторы.
    """
    now = today if today is not None else date.today()
    factors: list[ScoreFactor] = []

    # Пробег.
    if detect_mileage_rollback(registry.mileage_history):
        factors.append(
            ScoreFactor(
                code="mileage_rollback",
                penalty=PENALTY_MILEAGE_ROLLBACK,
                summary="пробег в истории уменьшался — похоже на скрутку",
            )
        )

    # Владельцы.
    owners = registry.owners_count
    if owners is not None and owners > 1:
        penalty = min((owners - 1) * PENALTY_PER_OWNER_AFTER_FIRST, MAX_OWNERS_PENALTY)
        factors.append(
            ScoreFactor(
                code="owners",
                penalty=penalty,
                summary=f"{_plural(owners, 'владелец', 'владельца', 'владельцев')}",
            )
        )
    if _has_owner_churn(registry, now):
        factors.append(
            ScoreFactor(
                code="owner_churn",
                penalty=PENALTY_OWNER_CHURN,
                summary="владельцы менялись чаще, чем раз в два года",
            )
        )

    # ДТП: один агрегированный фактор, чтобы сумма факторов сходилась с баллом
    # даже после отсечения по потолку.
    if registry.accidents:
        raw = sum(_accident_penalty(accident) for accident in registry.accidents)
        factors.append(
            ScoreFactor(
                code="accidents",
                penalty=min(raw, MAX_ACCIDENTS_PENALTY),
                summary=_accidents_summary(registry.accidents),
            )
        )

    # Такси.
    if registry.is_taxi:
        factors.append(
            ScoreFactor(
                code="taxi",
                penalty=PENALTY_TAXI,
                summary="машина работала в такси",
            )
        )

    # Стоп-факторы.
    if registry.is_pledged:
        factors.append(
            ScoreFactor(
                code="pledge",
                penalty=PENALTY_PLEDGE,
                summary="залог: машину могут забрать у нового владельца",
                blocking=True,
            )
        )
    if registry.has_restrictions:
        factors.append(
            ScoreFactor(
                code="restrictions",
                penalty=PENALTY_RESTRICTIONS,
                summary="ограничения ГИБДД: машину не поставить на учёт",
                blocking=True,
            )
        )

    total = sum(factor.penalty for factor in factors)
    score = min(max(BASE_SCORE - total, MIN_SCORE), MAX_SCORE)
    return ScoreResult(score=score, factors=tuple(factors))


def _headline(result: ScoreResult) -> str:
    """Вердикт словами по баллу; стоп-фактор перебивает балл."""
    if result.blocking_factors:
        # Даже если арифметика вдруг оставила бы балл высоким (появился новый
        # фактор, сдвинулись константы), стоп-фактор обязан звучать как стоп.
        return HEADLINE_STOP
    if result.score >= THRESHOLD_GOOD:
        return HEADLINE_GOOD
    if result.score >= THRESHOLD_CAUTION:
        return HEADLINE_CAUTION
    return HEADLINE_STOP


def _subtext(result: ScoreResult, coverage: SourcesCoverage) -> str:
    """Подзаголовок: главный найденный нюанс плюс отметка о неполноте (CC2)."""
    blocking = result.blocking_factors
    # Нюансы дешевле порога в подзаголовок не выносим: вторые руки — норма
    # рынка, и называть их «нюансом» значит пугать пользователя пустым местом.
    # В блоках ниже они всё равно видны, а балл их уже учёл.
    notable = [factor for factor in result.factors if factor.penalty >= SUBTEXT_MIN_PENALTY]

    if blocking:
        listed = "; ".join(factor.summary for factor in blocking)
        parts = [f"Нашли стоп-фактор — {listed}."]
    elif len(notable) == 1:
        parts = [f"Серьёзных стоп-факторов не нашли. Один нюанс — {notable[0].summary}."]
    elif notable:
        main = max(notable, key=lambda factor: factor.penalty)
        parts = [f"Серьёзных стоп-факторов не нашли. Главное — {main.summary}, остальное ниже."]
    elif result.factors:
        parts = ["Серьёзных стоп-факторов не нашли. Мелкие детали — в блоках ниже."]
    else:
        parts = ["Серьёзных стоп-факторов не нашли. По документам и истории всё ровно."]

    if not coverage.complete:
        # Пейволл не имеет права продавать данные, которых нет: если источник
        # не ответил, это видно в самом вердикте, а не только в служебном поле.
        parts.append(f"Ответили {coverage.summary} источников.")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Блоки проверок                                                              #
# --------------------------------------------------------------------------- #


def _mileage_block(registry: RegistryPayload) -> CheckBlock:
    """Блок «Пробег»: честный, скрученный или неизвестный."""
    history = registry.mileage_history
    if not history:
        return CheckBlock(
            code="mileage",
            status=CheckBlockStatus.NEUTRAL,
            title="Пробег",
            note="истории пробега нет — сверьте одометр на осмотре",
        )

    latest = max(history, key=lambda record: record.recorded_on)
    value = _format_mileage(latest.mileage_km)
    if detect_mileage_rollback(history):
        return CheckBlock(
            code="mileage",
            status=CheckBlockStatus.DANGER,
            title="Пробег",
            value=value,
            note="в истории он уменьшался — похоже на скрутку",
        )
    return CheckBlock(
        code="mileage",
        status=CheckBlockStatus.OK,
        title="Пробег",
        value=value,
        note="растёт равномерно",
    )


def _owners_block(registry: RegistryPayload, today: date) -> CheckBlock:
    """Блок «Владельцы»: сколько их было и как часто менялись."""
    owners = registry.owners_count
    if owners is None:
        return CheckBlock(
            code="owners",
            status=CheckBlockStatus.NEUTRAL,
            title="Владельцы",
            note="данных о владельцах нет",
        )

    title = _plural(owners, "владелец", "владельца", "владельцев")
    age = _vehicle_age_years(registry, today)
    if _has_owner_churn(registry, today):
        return CheckBlock(
            code="owners",
            status=CheckBlockStatus.WARN,
            title=title,
            note="меняются чаще, чем раз в два года",
        )

    note: str | None = None
    if age is not None and age > 0 and owners > 0:
        years = age / owners
        note = f"в среднем по {_format_years(years)} на владельца"
    status = CheckBlockStatus.OK if owners <= 1 else CheckBlockStatus.NEUTRAL
    if owners > OWNER_CHURN_MIN_OWNERS:
        status = CheckBlockStatus.WARN
    return CheckBlock(code="owners", status=status, title=title, value=None, note=note)


def _accidents_block(registry: RegistryPayload) -> CheckBlock:
    """Блок «ДТП»: сколько и насколько тяжёлые."""
    accidents = registry.accidents
    if not accidents:
        return CheckBlock(
            code="accidents",
            status=CheckBlockStatus.OK,
            title="ДТП не найдены",
            note="по базе ГИБДД записей нет",
        )

    worst = _worst_accident(accidents)
    # Янтарь против красного: лёгкое ДТП — нюанс, тяжёлое — риск. Дизайн-система
    # запрещает красить лёгкое ДТП красным, иначе цвет перестаёт что-то значить.
    status = (
        CheckBlockStatus.DANGER
        if _accident_penalty(worst) >= PENALTY_ACCIDENT_BY_SEVERITY["тяжёлое"]
        else CheckBlockStatus.WARN
    )
    title = f"{len(accidents)} ДТП"
    zone = f", {worst.damage_zone}" if worst.damage_zone else ""
    note = f"{worst.severity.capitalize()}{zone} {worst.occurred_on.year}"
    return CheckBlock(code="accidents", status=status, title=title, note=note)


def _legal_block(registry: RegistryPayload) -> CheckBlock:
    """Блок «Юридическая чистота»: залог, ограничения, такси."""
    title = "Залог · такси · ограничения"
    troubles: list[str] = []
    if registry.is_pledged:
        troubles.append("в залоге")
    if registry.has_restrictions:
        troubles.append("ограничения на регистрацию")
    if registry.is_taxi:
        troubles.append("числится в такси")

    if troubles:
        # Залог и ограничения блокируют сделку, такси — нет: это разный цвет.
        status = (
            CheckBlockStatus.DANGER
            if registry.is_pledged or registry.has_restrictions
            else CheckBlockStatus.WARN
        )
        return CheckBlock(code="legal", status=status, title=title, note=", ".join(troubles))

    checked = (registry.is_pledged, registry.has_restrictions, registry.is_taxi)
    if all(flag is None for flag in checked):
        return CheckBlock(
            code="legal",
            status=CheckBlockStatus.NEUTRAL,
            title=title,
            note="не проверяли — источник не ответил",
        )
    return CheckBlock(code="legal", status=CheckBlockStatus.OK, title=title, note="не числится")


def build_check_blocks(registry: RegistryPayload, *, today: date | None = None) -> list[CheckBlock]:
    """Четыре блока экрана B3 в порядке отрисовки: пробег, владельцы, ДТП, право."""
    now = today if today is not None else date.today()
    return [
        _mileage_block(registry),
        _owners_block(registry, now),
        _accidents_block(registry),
        _legal_block(registry),
    ]


# --------------------------------------------------------------------------- #
# Кластер площадок                                                            #
# --------------------------------------------------------------------------- #


def build_listing_cluster(
    listings: Sequence[ListingPayload],
    *,
    cluster_id: str,
    match_reasons: Sequence[str] = (),
    degraded: Sequence[DegradedSource] = (),
    today: date | None = None,
) -> ListingCluster | None:
    """Собрать блок «Это авто на площадках».

    На вход идёт готовый кластер дедупликации (5.2) и коды признаков, по которым
    объявления склеены: `vin`, `photo_phash`, `phone`… Схема переводит их в
    подпись «совпали VIN, фото и телефон продавца» сама — склейка обязана быть
    объяснимой, иначе выглядит произволом.

    Args:
        listings: объявления одного автомобиля.
        cluster_id: идентификатор кластера из дедупликации.
        match_reasons: коды сработавших признаков в порядке каскада.
        degraded: источники, отдавшие кэш или не ответившие: их цены помечаются
            `stale`, чтобы на экране была плашка «данные от 12:40» (CC2).
        today: «сегодня» для подписи «сегодня / 3 дня».

    Returns:
        Кластер или `None`, если объявлений нет вовсе.
    """
    if not listings:
        return None

    now = today if today is not None else date.today()
    stale_by_source = {entry.source.strip().lower(): entry for entry in degraded}

    prices: list[ListingPrice] = []
    for listing in sorted(listings, key=lambda item: item.price):
        entry = stale_by_source.get(listing.platform.strip().lower())
        # Устаревшей цену делает либо снятое объявление, либо площадка, которая
        # не ответила и отдала кэш: в обоих случаях цена может быть уже не та.
        stale = entry is not None or not listing.is_active
        prices.append(
            ListingPrice(
                source=listing.platform,
                price_rub=max(listing.price, 0),
                url=listing.url or None,
                seen=_humanize_age(listing.price_changed_on or listing.published_on, now),
                stale=stale,
                cached_at=entry.cached_at if entry is not None else None,
            )
        )

    known = [price.price_rub for price in prices if price.price_rub > 0]
    spread = max(known) - min(known) if known else 0
    return ListingCluster(
        id=cluster_id,
        spread_rub=spread,
        match_reasons=list(match_reasons),
        listings=prices,
    )


# --------------------------------------------------------------------------- #
# Прогноз ремонта                                                             #
# --------------------------------------------------------------------------- #


def build_repair_note(
    forecast: RepairForecast,
    *,
    horizon_km: float = DEFAULT_HORIZON_KM,
) -> RepairNote:
    """Переложить прогноз ремонта в формулировки экрана.

    Ключевое здесь — `prevalence_calibrated`. Пока распространённость болячек не
    откалибрована, доля упоминаний в отзывах смещена к жалобам и завышает риск
    (5.6.1), поэтому:

    * проценты показывать нельзя — только порядковая шкала «часто / иногда /
      редко»;
    * сумма подаётся как оценка сверху («не больше»), а не как ожидание: она
      посчитана в предположении, что болячка встречается у всех.

    Args:
        forecast: результат :func:`~avtoklik.knowledge.expected_repair_cost`.
        horizon_km: горизонт, на котором считался прогноз (для подписи).

    Returns:
        Готовые к показу строки и суммы.
    """
    amount = int(round(forecast.expected))
    horizon = int(round(horizon_km))
    horizon_text = _format_mileage(horizon)

    if forecast.prevalence_calibrated:
        headline = f"Риск ремонтов на ближайшие {horizon_text} ≈ {_format_rub(amount)}"
    else:
        headline = (
            f"Ремонт на ближайшие {horizon_text} — не больше {_format_rub(amount)}: "
            "оценка сверху, встречаемость болячек ещё не откалибрована"
        )

    lines: list[str] = []
    for risk in forecast.top_contributors:
        if forecast.prevalence_calibrated:
            share = f"{risk.failure_probability * 100:.0f} %"
        else:
            share = _ordinal_frequency(risk.failure_probability)
        lines.append(
            f"{risk.defect.title} — {share}, {_format_rub(int(round(risk.expected_cost)))}"
        )

    return RepairNote(
        headline=headline,
        amount_rub=amount,
        low_rub=int(round(forecast.low)),
        high_rub=int(round(forecast.high)),
        horizon_km=horizon,
        calibrated=forecast.prevalence_calibrated,
        lines=tuple(lines),
    )


# --------------------------------------------------------------------------- #
# Сборка                                                                      #
# --------------------------------------------------------------------------- #


def build_verdict_bundle(
    registry: RegistryPayload,
    *,
    sources_ok: int,
    sources_total: int,
    listings: Sequence[ListingPayload] = (),
    match_reasons: Sequence[str] = (),
    cluster_id: str = "",
    degraded: Sequence[DegradedSource] = (),
    repair_forecast: RepairForecast | None = None,
    repair_horizon_km: float = DEFAULT_HORIZON_KM,
    paywall: PaywallInfo | None = None,
    today: date | None = None,
) -> VerdictBundle:
    """Собрать вердикт целиком и вернуть вместе с тем, чему в схеме места нет.

    Вердикт выдаётся и при неполном покрытии: «82 из 100 по 5 из 6 источников» —
    штатный ответ, а не ошибка (CC2). Единственное жёсткое условие остаётся за
    оркестратором: без регистрационного источника сюда вообще не заходят.

    Args:
        registry: регистрационные данные (ГИБДД и аналоги).
        sources_ok: сколько источников ответило.
        sources_total: сколько источников опрашивали.
        listings: объявления одного кластера — результат дедупликации (5.2).
        match_reasons: признаки, по которым объявления склеены.
        cluster_id: идентификатор кластера.
        degraded: источники, отдавшие кэш или не ответившие.
        repair_forecast: прогноз затрат на ремонт под пробег (5.6).
        repair_horizon_km: горизонт прогноза — для подписи «на 20 т. км».
        paywall: предложение полного отчёта (B4).
        today: «сегодня»; параметр нужен тестам и повторной сборке отчёта.

    Returns:
        :class:`VerdictBundle`: ответ API, разбор скоринга и прогноз ремонта.
    """
    now = today if today is not None else date.today()
    coverage = SourcesCoverage(sources_ok=sources_ok, sources_total=sources_total)
    score = score_vehicle(registry, today=now)

    verdict = CheckVerdict(
        score=score.score,
        headline=_headline(score),
        subtext=_subtext(score, coverage),
        checks=build_check_blocks(registry, today=now),
        cluster=build_listing_cluster(
            listings,
            cluster_id=cluster_id or _fallback_cluster_id(registry),
            match_reasons=match_reasons,
            degraded=degraded,
            today=now,
        ),
        degraded=list(degraded),
        coverage=coverage,
        paywall=paywall,
    )
    repair = (
        build_repair_note(repair_forecast, horizon_km=repair_horizon_km)
        if repair_forecast
        else None
    )
    return VerdictBundle(verdict=verdict, score=score, repair=repair)


def build_verdict(
    registry: RegistryPayload,
    *,
    sources_ok: int,
    sources_total: int,
    listings: Sequence[ListingPayload] = (),
    match_reasons: Sequence[str] = (),
    cluster_id: str = "",
    degraded: Sequence[DegradedSource] = (),
    repair_forecast: RepairForecast | None = None,
    repair_horizon_km: float = DEFAULT_HORIZON_KM,
    paywall: PaywallInfo | None = None,
    today: date | None = None,
) -> CheckVerdict:
    """Собрать вердикт проверки — тело ответа `GET /checks/{id}/verdict` (B3).

    Тонкая обёртка над :func:`build_verdict_bundle` для вызывающих, которым
    нужен только ответ API.

    **Ограничение схемы.** :class:`~avtoklik.api.schemas.CheckVerdict` не имеет
    поля под прогноз ремонта, а придумывать поле в чужом модуле нельзя, поэтому
    здесь прогноз теряется: `repair_forecast` влияет только на бандл. Чтобы
    строка «риск ремонтов на ближайшие 20 т. км ≈ 34 000 ₽» доехала до экрана,
    схему нужно расширить полем вида `repair: RepairForecastOut | None` (сумма,
    вилка, горизонт, флаг калибровки и порядковая шкала вместо процентов).
    До тех пор берите :func:`build_verdict_bundle` и показывайте
    :class:`RepairNote` отдельно.

    Аргументы и поведение — см. :func:`build_verdict_bundle`.
    """
    return build_verdict_bundle(
        registry,
        sources_ok=sources_ok,
        sources_total=sources_total,
        listings=listings,
        match_reasons=match_reasons,
        cluster_id=cluster_id,
        degraded=degraded,
        repair_forecast=repair_forecast,
        repair_horizon_km=repair_horizon_km,
        paywall=paywall,
        today=today,
    ).verdict


# --------------------------------------------------------------------------- #
# Форматирование                                                              #
# --------------------------------------------------------------------------- #


def _fallback_cluster_id(registry: RegistryPayload) -> str:
    """Идентификатор кластера, когда дедупликация его не передала."""
    return registry.vin or "cluster"


def _plural(count: int, one: str, few: str, many: str) -> str:
    """«1 владелец», «2 владельца», «5 владельцев» — с числом в начале."""
    return f"{count} {_plural_form(count, one, few, many)}"


def _plural_form(count: int, one: str, few: str, many: str) -> str:
    """Русская форма слова для числа (без самого числа)."""
    tail_100 = abs(count) % 100
    tail_10 = abs(count) % 10
    if 11 <= tail_100 <= 14:
        return many
    if tail_10 == 1:
        return one
    if 2 <= tail_10 <= 4:
        return few
    return many


def _format_mileage(km: int) -> str:
    """«92 т. км» — так пробег подписан на экране B3."""
    if km < 1_000:
        return f"{km} км"
    return f"{km // 1_000} т. км"


def _format_rub(value: int) -> str:
    """«785 000 ₽» — пробелы вместо запятых, как в русской типографике."""
    return f"{value:,}".replace(",", " ") + " ₽"


def _format_years(years: float) -> str:
    """«4 года», «3,5 года» — срок владения человеческими словами."""
    rounded = round(years * 2) / 2
    if rounded == int(rounded):
        whole = int(rounded)
        return f"{whole} {_plural_form(whole, 'год', 'года', 'лет')}"
    return f"{rounded:.1f}".replace(".", ",") + " года"


def _humanize_age(day: date | None, today: date) -> str | None:
    """«сегодня», «вчера», «3 дня» — когда цену видели живой."""
    if day is None:
        return None
    days = (today - day).days
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    return _plural(days, "день", "дня", "дней")


def _ordinal_frequency(probability: float) -> str:
    """Порядковая шкала вместо процентов, когда встречаемость не откалибрована."""
    for threshold, label in _ORDINAL_FREQUENCY:
        if probability >= threshold:
            return label
    return _ORDINAL_FREQUENCY_TAIL


def _accidents_summary(accidents: Sequence[AccidentRecord]) -> str:
    """«лёгкое ДТП в 2022» / «3 ДТП, самое тяжёлое — в 2019»."""
    worst = _worst_accident(accidents)
    severity = worst.severity.strip().lower() or "неизвестной тяжести"
    if len(accidents) == 1:
        return f"{severity} ДТП в {worst.occurred_on.year}"
    return f"{len(accidents)} ДТП, самое серьёзное — {severity} в {worst.occurred_on.year}"
