"""Тесты источников на фикстурах (экран B2, сценарии CC2 и CC5).

Тесты здесь проверяют не «код не падает», а три обещания продукта:

1. экран B2 показывает ровно шесть строк в заданном порядке и с подписями,
   которые пришли с сервера;
2. деградация — состояние, а не ошибка: упавший и не успевший источник дают
   ``FAILED`` и никого не роняют (CC2), неизвестный номер даёт пустой, но
   валидный ответ (CC5);
3. фикстуры **согласованы** с дедупликацией: три объявления Kia Rio с трёх
   площадок склеиваются в одно авто с разбросом 25 000 ₽ (B3), а Rio и Solaris
   не склеиваются ни при каких обстоятельствах.

Третий пункт — ключевой. Без него фикстуры остались бы красивыми числами,
которые никогда не проверялись вместе с алгоритмом, ради которого написаны.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from avtoklik.collector.base import SourceQuery, SourceStatus
from avtoklik.matching.dedup import (
    PHASH_MAX_DISTANCE,
    ListingFeatures,
    MatchConfidence,
    canonical_price,
    cluster_listings,
    hamming_distance,
    similarity_score,
)
from avtoklik.service.payloads import ListingPayload, RegistryPayload
from avtoklik.service.sources import (
    AutoruAdapter,
    AvitoAdapter,
    DromAdapter,
    DromPayload,
    FixtureSourceAdapter,
    GibddAdapter,
    NomerogramAdapter,
    NomerogramPayload,
    SocialAdapter,
    default_sources,
    fixture_keys,
)
from avtoklik.service.sources.fixtures import (
    RIO_AVITO,
    RIO_LISTINGS,
    RIO_PHOTOS,
    RIO_PLATE,
    RIO_PRICE_SPREAD_RUB,
    RIO_VIN,
    SCENARIO_TODAY,
    SOLARIS_LISTINGS,
    SOLARIS_PHOTOS,
    SOLARIS_PLATE,
    SOLARIS_VIN,
    UNKNOWN_PLATE,
)

# Момент «сейчас» сценария: все три объявления Rio мы видели сегодня, а вот
# цены у них от разных дат — ровно это и разводит канонический выбор.
NOW = datetime(SCENARIO_TODAY.year, SCENARIO_TODAY.month, SCENARIO_TODAY.day, 12, 0, tzinfo=UTC)

#: Ожидаемый порядок и состав строк экрана B2.
B2_SCREEN = (
    ("gibdd", "ГИБДД · регистрация, ДТП", True),
    ("nomerogram", "Номерограм · история фото", False),
    ("avito", "Авито · объявления", False),
    ("autoru", "Авто.ру · история цены", False),
    ("drom", "Дром · отзывы владельцев", False),
    ("social", "Соцсети · борды VK/TG", False),
)

#: Адаптер и тип полезной нагрузки, который он обязан отдать по известному номеру.
ADAPTERS_AND_PAYLOADS: tuple[tuple[type[FixtureSourceAdapter[Any]], type[object]], ...] = (
    (GibddAdapter, RegistryPayload),
    (NomerogramAdapter, NomerogramPayload),
    (AvitoAdapter, ListingPayload),
    (AutoruAdapter, ListingPayload),
    (DromAdapter, DromPayload),
    (SocialAdapter, tuple),
)


def rio_query() -> SourceQuery:
    """Проверка эталонного Kia Rio по госномеру — то, что вводится на B1."""
    return SourceQuery(key=RIO_PLATE, plate=RIO_PLATE, model_hint="Kia Rio III")


def solaris_query() -> SourceQuery:
    """Проверка Hyundai Solaris — негативный сценарий."""
    return SourceQuery(key=SOLARIS_PLATE, plate=SOLARIS_PLATE)


def features(listing: ListingPayload, *, seen_at: datetime = NOW) -> ListingFeatures:
    """Переводит объявление источника в признаки для дедупликации.

    Перевод намеренно живёт в тестах, а не в пакете источников: собирать
    ``ListingFeatures`` — работа сборки вердикта, а адаптер отвечает только за
    «сходить и разобрать». Здесь он нужен, чтобы доказать согласованность
    фикстур с каскадом §5.2.

    Args:
        listing: объявление из фикстур.
        seen_at: когда краулер последний раз видел объявление живым.

    Returns:
        Признаки объявления для :func:`similarity_score`.
    """
    changed = listing.price_changed_on
    # Час в дате условный: важен только день, но canonical_price сравнивает моменты.
    changed_at = (
        None
        if changed is None
        else datetime(changed.year, changed.month, changed.day, 9, tzinfo=UTC)
    )
    return ListingFeatures(
        vin=listing.vin,
        phone_hash=listing.phone_hash,
        photo_phashes=list(listing.photo_phashes),
        model_id=listing.model_id or "",
        year=listing.year or 0,
        mileage_km=listing.mileage_km or 0,
        region_id=str(listing.region_id),
        price=listing.price,
        plate=listing.plate,
        listing_id=listing.listing_id,
        source_id=listing.platform,
        status="active" if listing.is_active else "archived",
        last_seen_at=seen_at,
        last_price_change_at=changed_at,
    )


class TestB2Screen:
    """Состав и порядок строк экрана «просвечиваем» (B2)."""

    def test_six_sources_in_screen_order(self) -> None:
        sources = default_sources()
        assert [(s.source_id, s.title, s.is_required) for s in sources] == list(B2_SCREEN)

    def test_only_gibdd_is_required(self) -> None:
        # §8.2: нет ГИБДД — нет вердикта вовсе; всё остальное деградирует мягко.
        required = [s.source_id for s in default_sources() if s.is_required]
        assert required == ["gibdd"]

    def test_budgets_follow_spec(self) -> None:
        # §8.2: классифайды 10 с, Номерограм 12 с, ГИБДД 20 с.
        budgets = {s.source_id: s.timeout_seconds for s in default_sources()}
        assert budgets == {
            "gibdd": 20.0,
            "nomerogram": 12.0,
            "avito": 10.0,
            "autoru": 10.0,
            "drom": 10.0,
            "social": 10.0,
        }

    def test_each_call_builds_fresh_adapters(self) -> None:
        # Настройка одного прогона не должна утекать в следующий.
        first, second = default_sources(), default_sources()
        assert all(a is not b for a, b in zip(first, second, strict=True))


class TestSuccessfulFetch:
    """Каждый источник отвечает DONE и ожидаемым типом нагрузки."""

    @pytest.mark.parametrize(("adapter_type", "payload_type"), ADAPTERS_AND_PAYLOADS)
    async def test_status_and_payload_type(
        self,
        adapter_type: type[FixtureSourceAdapter[Any]],
        payload_type: type[object],
    ) -> None:
        result = await adapter_type().fetch(rio_query())
        assert result.status is SourceStatus.DONE
        assert result.is_answer
        assert isinstance(result.payload, payload_type)

    @pytest.mark.parametrize(("adapter_type", "_payload_type"), ADAPTERS_AND_PAYLOADS)
    async def test_result_carries_attribution(
        self,
        adapter_type: type[FixtureSourceAdapter[Any]],
        _payload_type: type[object],
    ) -> None:
        # §5.6.5: без source_id и fetched_at нельзя показать «данные от 12:40».
        adapter = adapter_type()
        result = await adapter.fetch(rio_query())
        assert result.source_id == adapter.source_id
        assert result.fetched_at > 0
        assert result.from_cache is False

    async def test_gibdd_gives_the_b3_facts(self) -> None:
        payload = (await GibddAdapter().fetch(rio_query())).payload
        assert isinstance(payload, RegistryPayload)
        assert (payload.brand, payload.model, payload.year) == ("Kia", "Rio III", 2017)
        assert payload.owners_count == 2
        assert payload.is_pledged is False and payload.is_taxi is False
        # Блок «1 ДТП · лёгкое, зад 2022» на экране B3.
        assert len(payload.accidents) == 1
        assert payload.accidents[0].severity == "лёгкое"
        assert payload.accidents[0].occurred_on.year == 2022

    async def test_gibdd_mileage_history_is_monotonic(self) -> None:
        payload = (await GibddAdapter().fetch(rio_query())).payload
        assert isinstance(payload, RegistryPayload)
        values = [record.mileage_km for record in payload.mileage_history]
        assert values == sorted(values), "у честного Rio пробег не должен убывать"

    async def test_solaris_mileage_history_shows_rollback(self) -> None:
        payload = (await GibddAdapter().fetch(solaris_query())).payload
        assert isinstance(payload, RegistryPayload)
        values = [record.mileage_km for record in payload.mileage_history]
        assert values != sorted(values), "негативный сценарий обязан содержать скрутку"
        assert any(record.severity == "тяжёлое" for record in payload.accidents)
        assert payload.is_taxi is True

    async def test_nomerogram_closes_plate_to_vin(self) -> None:
        # Переход «госномер → VIN» закрывает именно Номерограм: у ГИБДД VIN нет.
        gibdd = (await GibddAdapter().fetch(rio_query())).payload
        nomerogram = (await NomerogramAdapter().fetch(rio_query())).payload
        assert isinstance(gibdd, RegistryPayload)
        assert isinstance(nomerogram, NomerogramPayload)
        assert gibdd.vin is None
        assert nomerogram.registry.vin == RIO_VIN
        assert nomerogram.photo_history, "история фото — вторая ценность источника"

    async def test_drom_gives_listing_and_reviews(self) -> None:
        payload = (await DromAdapter().fetch(rio_query())).payload
        assert isinstance(payload, DromPayload)
        assert payload.listing is not None
        assert payload.listing.price == 810_000
        assert payload.reviews.texts, "Дром подписан на B2 как источник отзывов"
        assert "рейка" in payload.reviews.texts[0]

    async def test_social_is_empty_for_rio_and_noisy_for_solaris(self) -> None:
        # По Rio борды молчат — на B3 честно «3 площадки», а не 4.
        assert (await SocialAdapter().fetch(rio_query())).payload == ()
        solaris = (await SocialAdapter().fetch(solaris_query())).payload
        assert isinstance(solaris, tuple)
        assert len(solaris) == 1
        # Худшее качество источника: ни VIN, ни госномера в посте нет.
        assert solaris[0].vin is None and solaris[0].plate is None

    async def test_autoru_has_no_listing_for_solaris(self) -> None:
        # Площадка ответила, объявления нет. Это DONE, а не отказ.
        result = await AutoruAdapter().fetch(solaris_query())
        assert result.status is SourceStatus.DONE
        assert result.payload is None

    @pytest.mark.parametrize(
        "subject",
        [
            SourceQuery(key=RIO_PLATE),
            SourceQuery(key="к999кк799"),
            SourceQuery(key="K999KK799"),
            SourceQuery(key=RIO_VIN),
            SourceQuery(key="проверка-1", vin=RIO_VIN),
            SourceQuery(key="проверка-2", plate="к 999 кк 799"),
        ],
    )
    async def test_subject_found_by_plate_or_vin_in_any_form(self, subject: SourceQuery) -> None:
        # Проверку запускают и с номера (B1), и с VIN (обходной путь CC5),
        # и номер прилетает то кириллицей, то латиницей, то с пробелами.
        payload = (await AvitoAdapter().fetch(subject)).payload
        assert isinstance(payload, ListingPayload)
        assert payload.listing_id == RIO_AVITO.listing_id

    def test_fixture_keys_are_deduplicated(self) -> None:
        keys = list(fixture_keys(SourceQuery(key=RIO_PLATE, plate=RIO_PLATE, vin=RIO_VIN)))
        assert len(keys) == len(set(keys))
        assert RIO_PLATE in keys and RIO_VIN in keys


class TestUnknownSubject:
    """CC5: «номер не находится в базах».

    Решение: неизвестный ключ даёт ``DONE`` с пустой нагрузкой, а не ``FAILED``.
    Различие не косметическое. ``FAILED`` означает «мы не знаем, что там», и по
    §8.2 разрешает оркестратору деградировать на кэш и предложить ретрай —
    для номера, которого просто нет в базах, и то и другое бессмысленно и
    вводит человека в заблуждение. Пустой ответ означает «источник знает, что
    ничего нет», и сборка вердикта отличает «все шесть ответили пусто» (CC5:
    предложить ввести VIN или фото) от «источники не ответили» (CC2: ретрай).
    """

    @pytest.mark.parametrize(("adapter_type", "_payload_type"), ADAPTERS_AND_PAYLOADS)
    async def test_unknown_plate_answers_done(
        self,
        adapter_type: type[FixtureSourceAdapter[Any]],
        _payload_type: type[object],
    ) -> None:
        result = await adapter_type().fetch(SourceQuery(key=UNKNOWN_PLATE, plate=UNKNOWN_PLATE))
        assert result.status is SourceStatus.DONE
        assert result.error is None

    @pytest.mark.parametrize(("adapter_type", "payload_type"), ADAPTERS_AND_PAYLOADS)
    async def test_unknown_plate_payload_is_empty_but_typed(
        self,
        adapter_type: type[FixtureSourceAdapter[Any]],
        payload_type: type[object],
    ) -> None:
        payload = (await adapter_type().fetch(SourceQuery(key=UNKNOWN_PLATE))).payload
        if payload is None:
            return  # у классифайдов «ничего не нашлось» выражено через None
        assert isinstance(payload, payload_type)
        assert payload in (RegistryPayload(), NomerogramPayload(), DromPayload(), ())

    async def test_cc5_is_representable_across_all_sources(self) -> None:
        # Весь экран B2 отработал, а сказать человеку нечего — это и есть CC5.
        query = SourceQuery(key=UNKNOWN_PLATE, plate=UNKNOWN_PLATE)
        results = [await source.fetch(query) for source in default_sources()]
        assert all(result.is_answer for result in results)
        assert not any(isinstance(result.payload, ListingPayload) for result in results)


class TestDegradation:
    """CC2: источник упал или не успел. Инвариант §5.1 — исключений наружу нет."""

    @pytest.mark.parametrize(("adapter_type", "_payload_type"), ADAPTERS_AND_PAYLOADS)
    async def test_failing_source_returns_failed(
        self,
        adapter_type: type[FixtureSourceAdapter[Any]],
        _payload_type: type[object],
    ) -> None:
        adapter = adapter_type(fail=True)
        result = await adapter.fetch(rio_query())  # не бросает — в этом и смысл
        assert result.status is SourceStatus.FAILED
        assert result.is_answer is False
        assert result.payload is None
        assert adapter.source_id in (result.error or "")

    async def test_failed_source_does_not_stop_the_others(self) -> None:
        # «82 из 100 по 5 из 6 источников — этого достаточно» (CC2).
        sources = default_sources()
        sources[4] = DromAdapter(fail=True)  # Дром не отвечает
        results = [await source.fetch(rio_query()) for source in sources]
        answered = [r for r in results if r.is_answer]
        assert len(answered) == 5
        assert [r.source_id for r in results if not r.is_answer] == ["drom"]

    async def test_slow_source_times_out(self) -> None:
        adapter = AvitoAdapter(delay_seconds=0.2, timeout_seconds=0.01)
        result = await adapter.fetch(rio_query())
        assert result.status is SourceStatus.FAILED
        assert result.error == "timeout"

    async def test_slow_source_within_budget_succeeds(self) -> None:
        adapter = AvitoAdapter(delay_seconds=0.01, timeout_seconds=1.0)
        result = await adapter.fetch(rio_query())
        assert result.status is SourceStatus.DONE

    async def test_timeout_wins_over_failure_for_a_slow_broken_source(self) -> None:
        # Площадка сначала тратит бюджет и только потом отдаёт капчу.
        adapter = DromAdapter(delay_seconds=0.2, fail=True, timeout_seconds=0.01)
        result = await adapter.fetch(rio_query())
        assert result.error == "timeout"

    @pytest.mark.parametrize("bad", [-1.0, -0.001])
    def test_negative_delay_is_rejected(self, bad: float) -> None:
        with pytest.raises(ValueError, match="задержка"):
            AvitoAdapter(delay_seconds=bad)

    def test_non_positive_timeout_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="бюджет"):
            AvitoAdapter(timeout_seconds=0.0)


class TestDedupIntegration:
    """Ключевой тест: фикстуры согласованы с каскадом дедупликации (§5.2)."""

    def test_three_rio_listings_glue_pairwise(self) -> None:
        items = [features(listing) for listing in RIO_LISTINGS]
        for index, left in enumerate(items):
            for right in items[index + 1 :]:
                match = similarity_score(left, right)
                assert match.confidence is MatchConfidence.EXACT
                assert match.score >= 0.97
                # Строка B3 «совпали VIN, фото и телефон продавца» берётся из данных.
                assert {"vin", "phone", "photo_phash"} <= set(match.reasons)

    def test_three_rio_listings_form_one_cluster(self) -> None:
        clusters = cluster_listings([features(listing) for listing in RIO_LISTINGS])
        assert len(clusters) == 1
        assert len(clusters[0]) == 3
        assert {item.source_id for item in clusters[0]} == {"Авито", "Авто.ру", "Дром"}

    def test_photos_alone_still_glue_the_rio_listings(self) -> None:
        # Площадка может скрыть и VIN, и телефон — склейка обязана выжить на фото.
        blind = [
            replace(features(listing), vin=None, phone_hash=None, plate=None)
            for listing in RIO_LISTINGS
        ]
        for index, left in enumerate(blind):
            for right in blind[index + 1 :]:
                match = similarity_score(left, right)
                assert match.match_reason == "photo_phash"
                assert match.confidence is MatchConfidence.STRONG

    def test_rio_photo_distances_are_inside_the_threshold(self) -> None:
        for listing in RIO_LISTINGS:
            distances = [
                hamming_distance(original, shown)
                for original, shown in zip(RIO_PHOTOS, listing.photo_phashes, strict=True)
            ]
            assert max(distances) <= 3, "пережатие площадкой — это единицы бит"
            assert max(distances) <= PHASH_MAX_DISTANCE

    def test_solaris_photos_are_maximally_far_from_rio(self) -> None:
        distances = [
            hamming_distance(rio, solaris)
            for rio, solaris in zip(RIO_PHOTOS, SOLARIS_PHOTOS, strict=True)
        ]
        assert min(distances) == 64

    def test_rio_and_solaris_never_glue(self) -> None:
        rio = features(RIO_AVITO)
        for listing in SOLARIS_LISTINGS:
            match = similarity_score(rio, features(listing))
            assert match.confidence is not MatchConfidence.EXACT
            assert match.score < 0.82

    def test_rio_and_solaris_are_vetoed_by_vin(self) -> None:
        match = similarity_score(features(RIO_AVITO), features(SOLARIS_LISTINGS[0]))
        assert match.match_reason == "vin_conflict"
        assert match.confidence is MatchConfidence.NO_MATCH

    def test_rio_and_solaris_stay_apart_without_identifiers(self) -> None:
        # Даже сняв VIN, госномер и телефон: фото, модель, год и пробег разные.
        rio = replace(features(RIO_AVITO), vin=None, plate=None, phone_hash=None)
        solaris = replace(features(SOLARIS_LISTINGS[0]), vin=None, plate=None, phone_hash=None)
        match = similarity_score(rio, solaris)
        assert match.confidence is MatchConfidence.NO_MATCH

    def test_six_listings_split_into_two_cars(self) -> None:
        everything = [features(listing) for listing in (*RIO_LISTINGS, *SOLARIS_LISTINGS)]
        clusters = cluster_listings(everything)
        assert len(clusters) == 2
        assert sorted(len(cluster) for cluster in clusters) == [3, 3]
        for cluster in clusters:
            vins = {item.vin for item in cluster if item.vin}
            assert len(vins) == 1
            assert vins <= {RIO_VIN, SOLARIS_VIN}


class TestPriceSpread:
    """Число из дизайна: разброс 25 000 ₽ на одном и том же автомобиле (B3)."""

    def test_three_platform_prices_match_the_mockup(self) -> None:
        prices = {listing.platform: listing.price for listing in RIO_LISTINGS}
        assert prices == {"Авито": 785_000, "Авто.ру": 799_000, "Дром": 810_000}

    def test_spread_is_exactly_25000(self) -> None:
        prices = [listing.price for listing in RIO_LISTINGS]
        assert max(prices) - min(prices) == RIO_PRICE_SPREAD_RUB == 25_000

    def test_canonical_price_is_the_freshest_avito_one(self) -> None:
        chosen = canonical_price([features(listing) for listing in RIO_LISTINGS], now=NOW)
        assert chosen.price == 785_000
        assert chosen.listing.source_id == "Авито"
        assert chosen.spread == RIO_PRICE_SPREAD_RUB

    def test_publication_dates_match_the_mockup(self) -> None:
        ages = {
            listing.platform: (SCENARIO_TODAY - listing.published_on).days
            for listing in RIO_LISTINGS
            if listing.published_on is not None
        }
        # B3: Авито «сегодня», Авто.ру «3 дня», Дром «8 дней».
        assert ages == {"Авито": 0, "Авто.ру": 3, "Дром": 8}
