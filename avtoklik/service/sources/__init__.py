"""Источники проверки: шесть адаптеров экрана B2 на фикстурах из дизайна.

Сетевых запросов и парсеров реальных площадок здесь нет — и это решение
проекта, а не временная мера: правовой статус сбора с Авито, Авто.ру и Дрома не
закрыт (§8.4, риск R3). Адаптеры реализуют контракт
:class:`~avtoklik.collector.base.SourceAdapter` и отдают данные сценария из
концепции, поэтому весь тракт «оркестратор → дедупликация → вердикт» можно
собрать и проверить целиком уже сейчас, а подмена фикстур на сеть не затронет
ничего выше адаптера.

Быстрый старт::

    from avtoklik.collector.base import SourceQuery
    from avtoklik.service.sources import default_sources

    query = SourceQuery(key="К999КК799", plate="К999КК799")
    results = [await source.fetch(query) for source in default_sources()]
"""

from __future__ import annotations

from avtoklik.collector.base import SourceAdapter
from avtoklik.service.sources.adapters import (
    CLASSIFIED_BUDGET_SECONDS,
    GIBDD_BUDGET_SECONDS,
    NOMEROGRAM_BUDGET_SECONDS,
    AutoruAdapter,
    AvitoAdapter,
    DromAdapter,
    GibddAdapter,
    NomerogramAdapter,
    SocialAdapter,
)
from avtoklik.service.sources.base import (
    DromPayload,
    FixtureSourceAdapter,
    NomerogramPayload,
    PhotoSighting,
    SourceUnavailableError,
    fixture_keys,
)
from avtoklik.service.sources.fixtures import (
    AUTORU_FIXTURES,
    AVITO_FIXTURES,
    DROM_FIXTURES,
    GIBDD_FIXTURES,
    NOMEROGRAM_FIXTURES,
    RIO_AUTORU,
    RIO_AVITO,
    RIO_DROM,
    RIO_LISTINGS,
    RIO_MODEL_ID,
    RIO_PHONE_HASH,
    RIO_PHOTOS,
    RIO_PHOTOS_AUTORU,
    RIO_PHOTOS_DROM,
    RIO_PLATE,
    RIO_PRICE_SPREAD_RUB,
    RIO_VIN,
    SCENARIO_TODAY,
    SOCIAL_FIXTURES,
    SOLARIS_AVITO,
    SOLARIS_DROM,
    SOLARIS_LISTINGS,
    SOLARIS_MODEL_ID,
    SOLARIS_PHONE_HASH,
    SOLARIS_PHOTOS,
    SOLARIS_PLATE,
    SOLARIS_SOCIAL,
    SOLARIS_VIN,
    UNKNOWN_PLATE,
)

__all__ = [
    "AUTORU_FIXTURES",
    "AVITO_FIXTURES",
    "CLASSIFIED_BUDGET_SECONDS",
    "DROM_FIXTURES",
    "GIBDD_BUDGET_SECONDS",
    "GIBDD_FIXTURES",
    "NOMEROGRAM_BUDGET_SECONDS",
    "NOMEROGRAM_FIXTURES",
    "RIO_AUTORU",
    "RIO_AVITO",
    "RIO_DROM",
    "RIO_LISTINGS",
    "RIO_MODEL_ID",
    "RIO_PHONE_HASH",
    "RIO_PHOTOS",
    "RIO_PHOTOS_AUTORU",
    "RIO_PHOTOS_DROM",
    "RIO_PLATE",
    "RIO_PRICE_SPREAD_RUB",
    "RIO_VIN",
    "SCENARIO_TODAY",
    "SOCIAL_FIXTURES",
    "SOLARIS_AVITO",
    "SOLARIS_DROM",
    "SOLARIS_LISTINGS",
    "SOLARIS_MODEL_ID",
    "SOLARIS_PHONE_HASH",
    "SOLARIS_PHOTOS",
    "SOLARIS_PLATE",
    "SOLARIS_SOCIAL",
    "SOLARIS_VIN",
    "UNKNOWN_PLATE",
    "AutoruAdapter",
    "AvitoAdapter",
    "DromAdapter",
    "DromPayload",
    "FixtureSourceAdapter",
    "GibddAdapter",
    "NomerogramAdapter",
    "NomerogramPayload",
    "PhotoSighting",
    "SocialAdapter",
    "SourceUnavailableError",
    "default_sources",
    "fixture_keys",
]


def default_sources() -> list[SourceAdapter]:
    """Шесть источников в порядке строк экрана B2.

    Порядок — не косметика: он совпадает с колонкой ``sources.priority`` и
    задаёт, в каком виде пользователь видит «рентген». Сверху то, от чего
    зависит вердикт (ГИБДД, Номерограм), ниже — площадки, которые наполняют
    блок «Это авто на площадках», и последними борды с худшим качеством данных.

    Тип возврата — базовый :class:`SourceAdapter`, а не фикстурный потомок:
    оркестратору всё равно, откуда адаптер берёт данные, и в день, когда
    появится сетевая реализация, эта сигнатура не поменяется.

    Returns:
        Новые экземпляры адаптеров. Именно новые: у адаптера есть настройки
        прогона (задержка, имитация отказа), и общий список на всё приложение
        рано или поздно привёл бы к тому, что настройка одного теста утекла
        в другой.
    """
    return [
        GibddAdapter(),
        NomerogramAdapter(),
        AvitoAdapter(),
        AutoruAdapter(),
        DromAdapter(),
        SocialAdapter(),
    ]
