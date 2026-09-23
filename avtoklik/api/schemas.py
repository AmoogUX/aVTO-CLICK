"""Схемы запросов и ответов флоу B «проверка автомобиля» (ТЗ, разделы 7.1 и 7.3).

Слой схем намеренно не знает ничего о том, откуда берутся данные: ни о
`avtoklik.collector`, ни о `avtoklik.matching`, ни о `avtoklik.knowledge`.
Он описывает только контракт на проводе — то, что видит мобильный клиент.
Связь с внутренними модулями идёт через протоколы из :mod:`avtoklik.api.deps`.

Три решения контракта, которые легко потерять при реализации (ТЗ, 7.3):

* **прогресс считает сервер, а не клиент** — иначе iOS, Android и веб посчитают
  по-разному, и «64 %» будет означать три разные вещи. Поэтому здесь есть
  :func:`progress_from_sources`, и она же — единственный источник числа;
* **в кадре прогресса всегда полный список источников**, а не дельта: на шести
  элементах экономить нечего, зато клиент не обязан склеивать состояние при
  реконнекте;
* **признак неполноты — это данные, а не ошибка** (CC2). Вердикт «82 из 100 по
  5 из 6 источников» — штатный ответ, поэтому :class:`SourcesCoverage` лежит в
  теле вердикта и всегда заполнена.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

__all__ = [
    "RepairForecastOut",
    "ALLOWED_PLATE_LETTERS",
    "CheckBlock",
    "CheckBlockStatus",
    "CheckCreateRequest",
    "CheckCreatedResponse",
    "CheckStatus",
    "CheckStatusResponse",
    "CheckVerdict",
    "DegradedSource",
    "DoneFrame",
    "ErrorFrame",
    "ErrorResponse",
    "ListingCluster",
    "ListingPrice",
    "PLATE_HINT",
    "PaywallInfo",
    "ProgressFrame",
    "SourceDegradedFrame",
    "SourceState",
    "SourceStatus",
    "SourcesCoverage",
    "StartedCheck",
    "Subject",
    "SubjectType",
    "VIN_HINT",
    "VehicleBrief",
    "progress_from_sources",
]

# CC5: на российском госномере используются только те кириллические буквы,
# которые графически совпадают с латиницей. Пользователь набирает их вперемешку
# (раскладка не переключена), поэтому латиницу мы не отвергаем, а приводим.
ALLOWED_PLATE_LETTERS = "АВЕКМНОРСТУХ"
_LATIN_TO_CYRILLIC = str.maketrans("ABEKMHOPCTYX", ALLOWED_PLATE_LETTERS)
_PLATE_RE = re.compile(
    rf"^[{ALLOWED_PLATE_LETTERS}]\d{{3}}[{ALLOWED_PLATE_LETTERS}]{{2}}\d{{2,3}}$"
)
# В VIN не бывает I, O и Q — их исключили, чтобы не путать с 1 и 0.
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_PLATE_NOISE_RE = re.compile(r"[\s\-_]+")

PLATE_HINT = "Допустимы " + " ".join(ALLOWED_PLATE_LETTERS)
VIN_HINT = "VIN — 17 символов, латиница и цифры, без I, O и Q"


class SubjectType(StrEnum):
    """Чем идентифицируем автомобиль. `photo` появится вместе с распознаванием фото."""

    PLATE = "plate"
    VIN = "vin"


class SourceState(StrEnum):
    """Состояние опроса источника.

    Значения отображаются на подписи экрана B2 один-в-один: `queued` → «В ОЧЕРЕДИ»,
    `running` → «ИЩЕМ…», `ok` → «ГОТОВО», `from_cache` → «ГОТОВО · кэш 12:40»,
    `timeout`/`error` → «не ответил». Клиент не занимается логикой, только
    отрисовкой, — это убирает расхождения между iOS и Android.
    """

    QUEUED = "queued"
    RUNNING = "running"
    OK = "ok"
    FROM_CACHE = "from_cache"
    TIMEOUT = "timeout"
    ERROR = "error"
    LATE = "late"


class CheckStatus(StrEnum):
    """Состояние проверки целиком. `partial` — полноценный результат (CC2), не ошибка."""

    QUEUED = "queued"
    RUNNING = "running"
    PARTIAL = "partial"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


class CheckBlockStatus(StrEnum):
    """Цвет плашки блока проверки на экране B3."""

    OK = "ok"
    NEUTRAL = "neutral"
    WARN = "warn"
    DANGER = "danger"


# Состояния, после которых источник больше не изменится: по ним считается прогресс.
_TERMINAL_STATES = frozenset(
    {
        SourceState.OK,
        SourceState.FROM_CACHE,
        SourceState.TIMEOUT,
        SourceState.ERROR,
        SourceState.LATE,
    }
)


class Subject(BaseModel):
    """Нормализованный предмет проверки: чем именно ищем автомобиль."""

    model_config = ConfigDict(frozen=True)

    type: SubjectType
    value: str = Field(min_length=1, max_length=32)

    @property
    def key(self) -> str:
        """Ключ предмета — он же ключ кэша и отпечаток для идемпотентности."""
        return f"{self.type.value}:{self.value}"


class CheckCreateRequest(BaseModel):
    """Тело `POST /api/v1/checks`.

    Спека (7.1) описывает пару `subject_type` + `value`; на проводе мы принимаем
    более явную форму — отдельные поля `plate` и `vin`, — потому что она делает
    невозможным рассогласование «тип сказали один, значение прислали другое».
    Приведение к спековой паре даёт :attr:`subject`.
    """

    model_config = ConfigDict(extra="forbid")

    plate: str | None = Field(default=None, description="Госномер, например К999КК799")
    vin: str | None = Field(default=None, description="VIN, 17 символов")

    @model_validator(mode="after")
    def _exactly_one_subject(self) -> Self:
        """Ровно один идентификатор: ни «оба сразу», ни «ни одного»."""
        filled = [name for name, value in (("plate", self.plate), ("vin", self.vin)) if value]
        if not filled:
            raise ValueError("нужен госномер или VIN")
        if len(filled) > 1:
            raise ValueError("госномер и VIN одновременно — неоднозначный запрос, нужен один")
        return self

    @field_validator("plate", mode="before")
    @classmethod
    def _normalize_plate(cls, value: Any) -> Any:
        """Привести госномер к каноническому виду.

        Пользователь набирает номер как попало: «к999кк 799», «K999KK799» в
        латинской раскладке. Отвергать за раскладку — грубо, поэтому приводим
        (CC5). Валидация живёт в валидаторе поля, а не модели, чтобы в ответе
        422 было видно, какое именно поле не понравилось.
        """
        if not isinstance(value, str):
            return value
        cleaned = _PLATE_NOISE_RE.sub("", value).upper().translate(_LATIN_TO_CYRILLIC)
        if not cleaned:
            return None
        if not _PLATE_RE.match(cleaned):
            raise ValueError(PLATE_HINT)
        return cleaned

    @field_validator("vin", mode="before")
    @classmethod
    def _normalize_vin(cls, value: Any) -> Any:
        """Привести VIN к каноническому виду: верхний регистр, без разделителей."""
        if not isinstance(value, str):
            return value
        cleaned = _PLATE_NOISE_RE.sub("", value).upper()
        if not cleaned:
            return None
        if not _VIN_RE.match(cleaned):
            raise ValueError(VIN_HINT)
        return cleaned

    @property
    def subject(self) -> Subject:
        """Нормализованный предмет проверки — то, что уходит в оркестратор."""
        if self.vin:
            return Subject(type=SubjectType.VIN, value=self.vin)
        if self.plate is None:  # недостижимо: гарантировано валидатором
            raise ValueError("нужен госномер или VIN")
        return Subject(type=SubjectType.PLATE, value=self.plate)


class VehicleBrief(BaseModel):
    """Короткая карточка автомобиля — шапка экрана B2, пока идёт проверка."""

    title: str
    year: int | None = None
    vin: str | None = None
    plate: str | None = None


class SourceStatus(BaseModel):
    """Один источник в кадре прогресса.

    `title` приходит с сервера (ТЗ, 7.3): добавили седьмой источник — он появился
    на экране без релиза приложения. Поля `title` и `order` необязательны только
    затем, чтобы оркестратор мог прислать дельту; наружу API всё равно отдаёт
    полный список — слияние делает :mod:`avtoklik.api.routes_check`.
    """

    code: str
    title: str | None = None
    state: SourceState = SourceState.QUEUED
    order: int | None = None
    cache_age_s: int | None = None


def progress_from_sources(sources: list[SourceStatus]) -> float:
    """Посчитать прогресс проверки по состояниям источников.

    Прогресс считает сервер и только сервер (ТЗ, 7.3). Источник в терминальном
    состоянии даёт полный вес, `running` — половину: пользователю важно видеть,
    что стрелка движется, пока ГИБДД думает свои 20 секунд.
    """
    if not sources:
        return 0.0
    weight = 0.0
    for source in sources:
        if source.state in _TERMINAL_STATES:
            weight += 1.0
        elif source.state is SourceState.RUNNING:
            weight += 0.5
    return round(weight / len(sources), 2)


class SourcesCoverage(BaseModel):
    """Сколько источников из скольких ответили — CC2.

    Это не служебная телеметрия, а часть честности пейволла: пользователь платит
    за отчёт и имеет право видеть, что он собран по 5 источникам из 6.
    """

    sources_ok: int = Field(ge=0)
    sources_total: int = Field(gt=0)

    @model_validator(mode="after")
    def _ok_not_greater_than_total(self) -> Self:
        if self.sources_ok > self.sources_total:
            raise ValueError("ответивших источников не может быть больше, чем всего")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def complete(self) -> bool:
        """`False` → UI обязан показать плашку неполноты."""
        return self.sources_ok >= self.sources_total

    @computed_field  # type: ignore[prop-decorator]
    @property
    def summary(self) -> str:
        """Готовая подпись «5 из 6» — чтобы клиент не собирал строку сам."""
        return f"{self.sources_ok} из {self.sources_total}"


class CheckBlock(BaseModel):
    """Блок проверки на экране B3: пробег, владельцы, ДТП, залог."""

    code: str
    status: CheckBlockStatus
    title: str
    value: str | None = None
    note: str | None = None


class ListingPrice(BaseModel):
    """Цена на одной площадке. `stale` → плашка «данные от 12:40» (CC2)."""

    source: str
    price_rub: int = Field(ge=0)
    url: str | None = None
    seen: str | None = None
    stale: bool = False
    cached_at: str | None = None


# Коды признаков склейки из :mod:`avtoklik.matching.dedup` — в человеческие слова.
# Словарь обязан покрывать все коды каскада: непереведённый код утечёт прямо
# в подпись под ценами («совпали attrs, телефон»), а это текст для пользователя.
_MATCH_REASON_TITLES = {
    "vin": "VIN",
    "photo_phash": "фото",
    "phone": "телефон",
    "plate": "госномер",
    "attrs": "характеристики",
    "geo_price": "город и цена",
    "text": "текст объявления",
}

#: Порядок в подписи задан дизайном: на экране B3 написано «совпали VIN, фото
#: и телефон продавца». Алфавитный порядок кодов дал бы другую фразу, поэтому
#: порядок фиксируется здесь явно, а не наследуется от множества признаков.
_MATCH_REASON_ORDER = ("vin", "photo_phash", "phone", "plate", "attrs", "geo_price", "text")


class ListingCluster(BaseModel):
    """Одно и то же авто на разных площадках — результат дедупликации (5.2).

    Пометка дедупликации обязана быть объяснимой: пользователь должен понимать,
    почему три объявления склеены в одно, иначе склейка выглядит произволом.
    """

    id: str
    spread_rub: int = Field(ge=0, description="Разброс цен между площадками")
    match_reasons: list[str] = Field(default_factory=list)
    listings: list[ListingPrice] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def deduplicated(self) -> bool:
        """`True`, если в кластер склеено больше одного объявления."""
        return len(self.listings) > 1

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dedup_note(self) -> str | None:
        """Человеческая подпись склейки: «совпали VIN, фото и телефон»."""
        if not self.deduplicated or not self.match_reasons:
            return None
        ordered = sorted(
            self.match_reasons,
            key=lambda r: (
                _MATCH_REASON_ORDER.index(r)
                if r in _MATCH_REASON_ORDER
                else len(_MATCH_REASON_ORDER)
            ),
        )
        titles = [_MATCH_REASON_TITLES.get(reason, reason) for reason in ordered]
        listed = titles[0] if len(titles) == 1 else ", ".join(titles[:-1]) + " и " + titles[-1]
        return f"совпали {listed}"


class DegradedSource(BaseModel):
    """Источник, отдавший кэш или не ответивший вовсе (CC2 — массив, а не флаг)."""

    source: str
    reason: str
    cached_at: str | None = None
    message: str | None = None
    retry_eta_min: tuple[int, int] | None = None


class PaywallInfo(BaseModel):
    """Предложение купить полный отчёт — экран B4."""

    report_id: str
    price_rub: int = Field(ge=0)
    preview_blocks: list[str] = Field(default_factory=list)


class RepairForecastOut(BaseModel):
    """Прогноз затрат на ремонт под пробег автомобиля — экран C2 и блок на B3.

    Почему интервал, а не одно число. Это математическое ожидание суммы
    случайных величин с большой дисперсией: у конкретного владельца затраты
    будут либо около нуля, либо заметно выше среднего. Одна цифра обещала бы
    точность, которой у модели нет, поэтому клиенту всегда приходят и границы,
    и разбор — из чего сумма складывается.

    `calibrated` — главный флаг этого блока. Пока распространённость болячек
    не откалибрована по статистике обращений СТО, доля упоминаний в отзывах
    завышена (человек пишет отзыв, когда сломалось), и показывать проценты
    нельзя: `lines` в этом случае несёт порядковую шкалу, а сумму надо
    подавать как оценку сверху. Клиент обязан смотреть на этот флаг, а не
    только на числа.
    """

    amount_rub: int = Field(ge=0, description="Ожидаемые затраты, ₽")
    low_rub: int = Field(ge=0, description="Нижняя граница интервала, ₽")
    high_rub: int = Field(ge=0, description="Верхняя граница интервала, ₽")
    horizon_km: int = Field(gt=0, description="Горизонт прогноза по пробегу, км")
    calibrated: bool = Field(
        description="Откалибрована ли распространённость. False — проценты не показывать"
    )
    headline: str = ""
    lines: list[str] = Field(default_factory=list, description="Разбор: из чего складывается сумма")

    @model_validator(mode="after")
    def _interval_is_sane(self) -> Self:
        """Границы не должны противоречить точке: иначе на экране будет бессмыслица."""
        if self.low_rub > self.high_rub:
            raise ValueError("нижняя граница прогноза больше верхней")
        return self


class CheckVerdict(BaseModel):
    """Вердикт проверки — экран B3 (ТЗ, 7.1 `GET /checks/{id}/verdict`)."""

    score: int = Field(ge=0, le=100)
    headline: str
    subtext: str | None = None
    checks: list[CheckBlock] = Field(default_factory=list)
    cluster: ListingCluster | None = None
    degraded: list[DegradedSource] = Field(default_factory=list)
    coverage: SourcesCoverage
    #: Прогноз ремонта под пробег этого автомобиля. `None` — по модели нет
    #: данных или пробег неизвестен; блок на экране в этом случае не рисуется,
    #: а не показывается нулями.
    repair: RepairForecastOut | None = None
    paywall: PaywallInfo | None = None


class StartedCheck(BaseModel):
    """То, что оркестратор возвращает на запуск проверки."""

    check_id: str
    eta_sec: int = Field(default=40, ge=0)
    status: CheckStatus = CheckStatus.QUEUED


class CheckCreatedResponse(BaseModel):
    """Ответ `POST /api/v1/checks`."""

    check_id: str
    stream_url: str
    status_url: str
    eta_sec: int = Field(ge=0)
    status: CheckStatus = CheckStatus.QUEUED


class CheckStatusResponse(BaseModel):
    """Ответ `GET /api/v1/checks/{id}` — резервный поллинг вместо SSE.

    Форма совпадает с кадром `progress` не случайно: это одно и то же состояние,
    просто доставленное иначе (ТЗ, 5.1). Клиент, упавший с SSE на поллинг,
    не должен переписывать разбор ответа.
    """

    check_id: str
    status: CheckStatus
    progress: float | None = Field(default=None, ge=0.0, le=1.0)
    elapsed_ms: int = Field(default=0, ge=0)
    vehicle: VehicleBrief | None = None
    sources: list[SourceStatus] = Field(default_factory=list)
    verdict: CheckVerdict | None = None

    @model_validator(mode="after")
    def _server_side_progress(self) -> Self:
        """Досчитать прогресс, если оркестратор его не проставил.

        Число обязано быть одинаковым в SSE и в поллинге, поэтому считаем его
        в одном месте, а не в каждом транспорте.
        """
        if self.progress is None:
            self.progress = progress_from_sources(self.sources)
            # Поле досчитано сервером, а не прислано клиентом: убираем его из
            # model_fields_set, чтобы транспорт мог отличить «не задано» от
            # «задано оркестратором» и пересчитать прогресс по полному списку.
            self.model_fields_set.discard("progress")
        return self


class ProgressFrame(BaseModel):
    """Кадр `event: progress` (ТЗ, 7.3)."""

    status: CheckStatus
    progress: float | None = Field(default=None, ge=0.0, le=1.0)
    elapsed_ms: int = Field(default=0, ge=0)
    vehicle: VehicleBrief | None = None
    sources: list[SourceStatus] = Field(default_factory=list)

    @model_validator(mode="after")
    def _server_side_progress(self) -> Self:
        if self.progress is None:
            self.progress = progress_from_sources(self.sources)
            # Поле досчитано сервером, а не прислано клиентом: убираем его из
            # model_fields_set, чтобы транспорт мог отличить «не задано» от
            # «задано оркестратором» и пересчитать прогресс по полному списку.
            self.model_fields_set.discard("progress")
        return self


class SourceDegradedFrame(BaseModel):
    """Кадр `event: source_degraded` — плашка CC2 на экране B2."""

    code: str
    reason: str
    cached_at: str | None = None
    message: str | None = None
    retry_eta_min: tuple[int, int] | None = None


class DoneFrame(BaseModel):
    """Кадр `event: done`. `sources_ok` / `sources_total` — тот же признак неполноты."""

    status: CheckStatus
    verdict_url: str
    sources_ok: int = Field(ge=0)
    sources_total: int = Field(gt=0)


class ErrorFrame(BaseModel):
    """Кадр `event: error` — критический источник недоступен, вердикта не будет."""

    code: str
    sources: list[str] = Field(default_factory=list)
    retry_after_sec: int | None = None


class ErrorResponse(BaseModel):
    """Тело ошибки API (ТЗ, 7.1): машинный `code` + человеческий `detail`."""

    code: str
    detail: str | None = None
    hint: str | None = None
    alternatives: list[str] | None = None
    issues: list[dict[str, str]] | None = None
