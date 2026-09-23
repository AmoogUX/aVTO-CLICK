"""Шесть источников экрана B2, ровно в том составе и порядке, что в макете.

| Код | Подпись B2 | Что отдаёт | Бюджет |
|---|---|---|---|
| ``gibdd`` | ГИБДД · регистрация, ДТП | :class:`RegistryPayload` | 20 с |
| ``nomerogram`` | Номерограм · история фото | :class:`NomerogramPayload` | 12 с |
| ``avito`` | Авито · объявления | :class:`ListingPayload` \\| ``None`` | 10 с |
| ``autoru`` | Авто.ру · история цены | :class:`ListingPayload` \\| ``None`` | 10 с |
| ``drom`` | Дром · отзывы владельцев | :class:`DromPayload` | 10 с |
| ``social`` | Соцсети · борды VK/TG | ``tuple[ListingPayload, ...]`` | 10 с |

Бюджеты взяты из §8.2: классифайды 10 с, Номерограм 12 с, ГИБДД 20 с — он
официальный и медленный, и именно поэтому он же единственный обязательный.

**«Ничего не нашлось» у классифайдов — это ``None``, а не пустой
``ListingPayload``.** Пустой объект пришлось бы отличать по магическому
значению (``listing_id == ""``), и рано или поздно он утёк бы в блок «Это авто
на площадках» как объявление без цены. ``None`` такой ошибки не допускает:
проверка обязательна на типах.
"""

from __future__ import annotations

from collections.abc import Mapping

from avtoklik.service.payloads import ListingPayload, RegistryPayload
from avtoklik.service.sources.base import DromPayload, FixtureSourceAdapter, NomerogramPayload
from avtoklik.service.sources.fixtures import (
    AUTORU_FIXTURES,
    AVITO_FIXTURES,
    DROM_FIXTURES,
    GIBDD_FIXTURES,
    NOMEROGRAM_FIXTURES,
    SOCIAL_FIXTURES,
)

__all__ = [
    "AutoruAdapter",
    "AvitoAdapter",
    "DromAdapter",
    "GibddAdapter",
    "NomerogramAdapter",
    "SocialAdapter",
]

CLASSIFIED_BUDGET_SECONDS = 10.0
"""Бюджет классифайда из §8.2."""

NOMEROGRAM_BUDGET_SECONDS = 12.0
GIBDD_BUDGET_SECONDS = 20.0


class GibddAdapter(FixtureSourceAdapter[RegistryPayload]):
    """ГИБДД: регистрации, владельцы, ДТП, ограничения и залог.

    Единственный обязательный источник проверки: без него вердикт не выдаётся
    вообще, показывается «не хватает главного» с ретраем (§8.2). VIN в ответе
    не приходит — проверка идёт по госномеру, а переход «госномер → VIN»
    закрывает Номерограм.
    """

    code = "gibdd"
    label = "ГИБДД · регистрация, ДТП"
    budget_seconds = GIBDD_BUDGET_SECONDS
    required = True

    @property
    def fixtures(self) -> Mapping[str, RegistryPayload]:
        """Регистрационные карточки обоих автомобилей сценария."""
        return GIBDD_FIXTURES

    def empty_payload(self) -> RegistryPayload:
        """Номер в базе не значится (CC5): карточка есть, но она пустая."""
        return RegistryPayload()


class NomerogramAdapter(FixtureSourceAdapter[NomerogramPayload]):
    """Номерограм: VIN по госномеру и история появления фотографий.

    Не обязателен для вердикта, но фактически несущий: без его VIN уровень 0
    каскада дедупликации нечем закрыть, а история кадров отвечает на вопрос
    «эта машина уже висела в продаже полгода назад».
    """

    code = "nomerogram"
    label = "Номерограм · история фото"
    budget_seconds = NOMEROGRAM_BUDGET_SECONDS

    @property
    def fixtures(self) -> Mapping[str, NomerogramPayload]:
        """VIN и история кадров обоих автомобилей сценария."""
        return NOMEROGRAM_FIXTURES

    def empty_payload(self) -> NomerogramPayload:
        """Ни VIN, ни истории — номер сервису неизвестен (CC5)."""
        return NomerogramPayload()


class AvitoAdapter(FixtureSourceAdapter[ListingPayload | None]):
    """Авито: действующее объявление по автомобилю. В сценарии — 785 000 ₽."""

    code = "avito"
    label = "Авито · объявления"
    budget_seconds = CLASSIFIED_BUDGET_SECONDS

    @property
    def fixtures(self) -> Mapping[str, ListingPayload | None]:
        """Объявления Авито по обоим автомобилям сценария."""
        return AVITO_FIXTURES

    def empty_payload(self) -> ListingPayload | None:
        """Объявления нет — площадка ответила пустой выдачей."""
        return None


class AutoruAdapter(FixtureSourceAdapter[ListingPayload | None]):
    """Авто.ру: объявление и история цены. В сценарии — 799 000 ₽, 3 дня назад.

    По Solaris объявления нет: это штатный пустой ответ, а не отказ площадки, и
    блок «Это авто на площадках» просто покажет на одну строку меньше.
    """

    code = "autoru"
    label = "Авто.ру · история цены"
    budget_seconds = CLASSIFIED_BUDGET_SECONDS

    @property
    def fixtures(self) -> Mapping[str, ListingPayload | None]:
        """Объявления Авто.ру; у Solaris записи нет."""
        return AUTORU_FIXTURES

    def empty_payload(self) -> ListingPayload | None:
        """Объявления нет — площадка ответила пустой выдачей."""
        return None


class DromAdapter(FixtureSourceAdapter[DromPayload]):
    """Дром: объявление плюс отзывы владельцев по поколению.

    Единственный источник с составной нагрузкой. Решение и его обоснование —
    в docstring :class:`~avtoklik.service.sources.base.DromPayload`: на экране
    B2 Дром подписан как источник отзывов, но третья цена блока «Это авто на
    площадках» (810 000 ₽) тоже его, и терять одно ради другого незачем.
    """

    code = "drom"
    label = "Дром · отзывы владельцев"
    budget_seconds = CLASSIFIED_BUDGET_SECONDS

    @property
    def fixtures(self) -> Mapping[str, DromPayload]:
        """Объявления и корпуса отзывов обоих автомобилей сценария."""
        return DROM_FIXTURES

    def empty_payload(self) -> DromPayload:
        """Ни объявления, ни отзывов по этому ключу."""
        return DromPayload()


class SocialAdapter(FixtureSourceAdapter[tuple[ListingPayload, ...]]):
    """Соцсети: борды VK и Telegram. Самый шумный источник с худшим качеством.

    Отдаёт список, а не одно объявление: один автомобиль на бордах перепощен
    по нескольку раз, а VIN и госномер в постах обычно отсутствуют вовсе — всё,
    что есть, вытащено регулярками из свободного текста. Пустой список — рядовой
    и ожидаемый ответ (по Rio борды молчат), поэтому источник и не обязателен.
    """

    code = "social"
    label = "Соцсети · борды VK/TG"
    budget_seconds = CLASSIFIED_BUDGET_SECONDS

    @property
    def fixtures(self) -> Mapping[str, tuple[ListingPayload, ...]]:
        """Посты бордов по обоим автомобилям сценария."""
        return SOCIAL_FIXTURES

    def empty_payload(self) -> tuple[ListingPayload, ...]:
        """Борды ничего не нашли."""
        return ()
