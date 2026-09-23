"""Каркас источников, работающих на фикстурах.

**Почему фикстуры, а не парсеры.** Правовой статус сбора с Авито, Авто.ру и
Дрома в проекте не закрыт (§8.4 технической документации, риск R3), поэтому
боевых парсеров здесь нет и не будет, пока условия доступа не определят. Это
осознанное проектное решение, а не временная заглушка: адаптеры ниже реализуют
тот же контракт :class:`~avtoklik.collector.base.SourceAdapter`, что и будущие
сетевые, и когда доступ появится, меняется только тело ``fetch_payload`` —
оркестратор, вердикт и экран B2 не узнают о подмене.

Ни одного сетевого вызова в этом пакете нет.

Кроме данных, каркас умеет три вещи, без которых нечем проверять деградацию:

* ``delay_seconds`` — медленный источник (сценарий «не успел к мягкому дедлайну»);
* ``fail=True`` — источник упал (CC2: «Дром не отвечает, показываем данные от 12:40»);
* отсутствие ключа в фикстурах — источник ответил, но данных нет (CC5).

Последнее — не ошибка. Различие принципиальное: ``FAILED`` означает «мы не знаем,
что там», и оркестратор имеет право деградировать на кэш; пустой ответ означает
«источник знает, что ничего нет», и деградировать не на что. Поэтому неизвестный
госномер даёт ``DONE`` с пустой полезной нагрузкой, а не ``FAILED``.
"""

from __future__ import annotations

import asyncio
from abc import abstractmethod
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import ClassVar, Generic, TypeVar

from avtoklik.collector.base import (
    DEFAULT_SOURCE_TIMEOUT_SECONDS,
    SourceAdapter,
    SourceQuery,
)
from avtoklik.matching.plate import normalize_plate
from avtoklik.matching.vin import normalize_vin
from avtoklik.service.payloads import ListingPayload, RegistryPayload, ReviewsPayload

__all__ = [
    "DromPayload",
    "FixtureSourceAdapter",
    "NomerogramPayload",
    "PhotoSighting",
    "SourceUnavailableError",
    "fixture_keys",
]

PayloadT = TypeVar("PayloadT")


class SourceUnavailableError(RuntimeError):
    """Имитация отказа площадки: капча, бан по IP, 5xx, сменившаяся вёрстка.

    Наружу это исключение не выходит: :meth:`SourceAdapter.fetch` ловит всё и
    отдаёт ``SourceResult`` со статусом ``FAILED`` (инвариант §5.1).
    """


@dataclass(frozen=True, slots=True)
class PhotoSighting:
    """Одно появление фотографии автомобиля на площадке.

    Номерограм торгует именно этим: «этот кадр уже висел на Дроме в 2023 году».
    Хранится перцептивный хэш, а не сам файл, — по нему же работает дедупликация.
    """

    seen_on: date
    platform: str
    phash: int
    listing_url: str = ""

    def __post_init__(self) -> None:
        if self.phash < 0:
            raise ValueError("перцептивный хэш не может быть отрицательным")


@dataclass(frozen=True, slots=True)
class NomerogramPayload:
    """Ответ Номерограма: регистрационные данные плюс история фотографий.

    Почему контейнер, а не голый :class:`RegistryPayload`: у Номерограма две
    разные ценности. Первая — переход «госномер → VIN», без которого не работает
    уровень 0 дедупликации, и она ложится в ``RegistryPayload`` как есть. Вторая —
    история кадров, для которой в ``RegistryPayload`` поля нет и заводить его
    нельзя: это свойство объявлений, а не регистрационной записи. Заводить новый
    тип в :mod:`avtoklik.service.payloads` ради одного источника тоже неверно —
    там лежат виды данных, а здесь композиция конкретного источника.
    """

    registry: RegistryPayload = field(default_factory=RegistryPayload)
    photo_history: tuple[PhotoSighting, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Источник ответил, но про этот номер не знает ничего (CC5)."""
        return self.registry.vin is None and not self.photo_history


@dataclass(frozen=True, slots=True)
class DromPayload:
    """Ответ Дрома: объявление и корпус отзывов владельцев.

    На экране B2 Дром подписан как «Дром · отзывы владельцев», но объявление он
    отдаёт тоже — и третья цена в блоке «Это авто на площадках» (810 000 ₽)
    именно его. Один источник честно закрывает две потребности, поэтому payload —
    контейнер с двумя полями, а не выбор одного из двух: разбирать
    ``isinstance`` на стороне вердикта было бы хуже во всех отношениях.

    ``listing is None`` — объявления по этому автомобилю на Дроме нет; отзывы
    при этом могут быть, потому что они про модель, а не про конкретное авто.
    """

    listing: ListingPayload | None = None
    reviews: ReviewsPayload = field(default_factory=ReviewsPayload)

    @property
    def is_empty(self) -> bool:
        """Ни объявления, ни отзывов."""
        return self.listing is None and not self.reviews.texts


def fixture_keys(query: SourceQuery) -> Iterator[str]:
    """Перечисляет ключи, под которыми стоит искать запрос в фикстурах.

    Источник опрашивают то по госномеру (B1), то по VIN (обходной путь CC5),
    то по ключу кэша — и это может быть одна и та же строка в разном виде
    (``к999кк799``, ``K999KK799``, ``К999КК799``). Нормализация здесь, а не в
    данных: таблицы фикстур держат канонические ключи и только их.

    Args:
        query: предмет проверки.

    Yields:
        Кандидатов на ключ фикстуры, от самого точного к самому грубому,
        без повторов.
    """
    seen: set[str] = set()
    raw_values = [query.key, query.plate or "", query.vin or ""]
    for raw in raw_values:
        if not raw:
            continue
        for candidate in (normalize_plate(raw), normalize_vin(raw), raw.strip().upper()):
            if candidate and candidate not in seen:
                seen.add(candidate)
                yield candidate


class FixtureSourceAdapter(SourceAdapter, Generic[PayloadT]):
    """Общий родитель источников на фикстурах.

    Подкласс объявляет четыре вещи: код (``code``), подпись экрана B2 (``label``),
    таблицу данных (:meth:`fixtures`) и пустой ответ (:meth:`empty_payload`).
    Всё остальное — поиск по ключу, имитация задержки и отказа, инвариант
    «не бросать исключений» — уже здесь.

    Args:
        delay_seconds: сколько «идти» в источник. Больше ``timeout_seconds`` —
            получится ``FAILED`` по таймауту, ровно как у настоящего медленного
            источника.
        fail: источник отвечает отказом.
        timeout_seconds: личный бюджет вместо штатного ``budget_seconds``.
            Нужен тестам, чтобы не ждать по 10 секунд реального времени.
    """

    #: Код источника, он же `sources.code` в схеме БД.
    code: ClassVar[str] = ""

    #: Подпись строки на экране B2; приходит с сервера, клиент её не хранит (§7.3).
    label: ClassVar[str] = ""

    #: Штатный бюджет источника (`sources.budget_ms`).
    budget_seconds: ClassVar[float] = DEFAULT_SOURCE_TIMEOUT_SECONDS

    #: Обязателен ли источник для выдачи вердикта (`verdict_requirements`).
    required: ClassVar[bool] = False

    def __init__(
        self,
        *,
        delay_seconds: float = 0.0,
        fail: bool = False,
        timeout_seconds: float | None = None,
    ) -> None:
        if delay_seconds < 0:
            raise ValueError("задержка не может быть отрицательной")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("бюджет источника должен быть положительным")
        self.delay_seconds = delay_seconds
        self.fail = fail
        self._timeout_override = timeout_seconds

    @property
    def source_id(self) -> str:
        """Код источника: `gibdd`, `nomerogram`, `avito`, …"""
        return self.code

    @property
    def title(self) -> str:
        """Подпись строки на экране B2."""
        return self.label

    @property
    def timeout_seconds(self) -> float:
        """Личный бюджет источника; у ГИБДД он больше, чем у классифайдов (§8.2)."""
        return self.budget_seconds if self._timeout_override is None else self._timeout_override

    @property
    def is_required(self) -> bool:
        """Без обязательного источника вердикт не выдаётся вовсе (§5.1)."""
        return self.required

    @property
    @abstractmethod
    def fixtures(self) -> Mapping[str, PayloadT]:
        """Таблица «канонический ключ → готовый ответ источника»."""

    @abstractmethod
    def empty_payload(self) -> PayloadT:
        """Ответ «источник отработал, данных по этому ключу нет» (CC5)."""

    def lookup(self, query: SourceQuery) -> PayloadT:
        """Находит ответ в фикстурах или отдаёт пустой.

        Args:
            query: предмет проверки.

        Returns:
            Полезную нагрузку источника; для неизвестного ключа — пустую.
        """
        table = self.fixtures
        for key in fixture_keys(query):
            found = table.get(key)
            if found is not None:
                return found
        return self.empty_payload()

    async def fetch_payload(self, query: SourceQuery) -> PayloadT:
        """Имитирует поход в источник: сначала время, потом отказ, потом данные.

        Порядок намеренный: настоящая площадка тоже сначала тратит бюджет и лишь
        затем отдаёт капчу, поэтому «медленный отказ» должен быть выразим.
        """
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        if self.fail:
            raise SourceUnavailableError(f"{self.source_id}: источник не отвечает")
        return self.lookup(query)
