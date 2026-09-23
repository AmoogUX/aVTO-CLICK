"""Тесты каскада дедупликации (§5.2 технической документации, сценарий B3)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from avtoklik.matching.dedup import (
    MAX_CLUSTER_SIZE,
    PHASH_MAX_DISTANCE,
    ListingFeatures,
    MatchConfidence,
    attr_similarity,
    canonical_price,
    cluster_listings,
    describe_reasons,
    hamming_distance,
    similarity_score,
)

VIN_A = "1HGCM82633A004352"
VIN_B = "1M8GDM9AXKP042788"

PHOTO_1 = 0xF0F0F0F0F0F0F0F0
PHOTO_2 = 0x0F0F0F0F0F0F0F0F
PHOTO_3 = 0xAAAAAAAAAAAAAAAA

# Те же кадры после пережатия площадкой: несколько бит уплыло, но не больше 6.
PHOTO_1_RECOMPRESSED = PHOTO_1 ^ 0b1011
PHOTO_2_RECOMPRESSED = PHOTO_2 ^ 0b110001

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


BASE_LISTING = ListingFeatures(
    model_id="hyundai-solaris-2",
    year=2018,
    mileage_km=92_000,
    region_id="77",
    price=785_000,
)


def make_listing(**overrides: Any) -> ListingFeatures:
    """Объявление на базе эталонного: Solaris 2018 года, 92 000 км, Москва."""
    return replace(BASE_LISTING, **overrides)


class TestHammingDistance:
    """Сравнение перцептивных хэшей."""

    def test_identical_hashes(self) -> None:
        assert hamming_distance(PHOTO_1, PHOTO_1) == 0

    def test_counts_differing_bits(self) -> None:
        assert hamming_distance(0b0000, 0b1011) == 3

    def test_symmetric(self) -> None:
        assert hamming_distance(PHOTO_1, PHOTO_3) == hamming_distance(PHOTO_3, PHOTO_1)

    def test_fully_opposite_64bit_hashes(self) -> None:
        assert hamming_distance(PHOTO_1, PHOTO_2) == 64

    def test_recompressed_photo_within_threshold(self) -> None:
        assert hamming_distance(PHOTO_1, PHOTO_1_RECOMPRESSED) <= PHASH_MAX_DISTANCE
        assert hamming_distance(PHOTO_2, PHOTO_2_RECOMPRESSED) <= PHASH_MAX_DISTANCE

    def test_negative_hash_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="отрицательным"):
            hamming_distance(-1, 0)


class TestVinLevel:
    """Уровень 0: VIN."""

    def test_same_vin_is_exact(self) -> None:
        a = make_listing(vin=VIN_A)
        b = make_listing(vin=VIN_A.lower(), price=810_000, mileage_km=94_000)
        result = similarity_score(a, b)
        assert result.confidence is MatchConfidence.EXACT
        assert result.score == pytest.approx(0.99)
        assert result.match_reason == "vin"
        assert "vin" in result.reasons

    def test_different_vins_never_match(self) -> None:
        """Одинаковые фото и телефон при разных VIN — это два авто у дилера."""
        a = make_listing(
            vin=VIN_A,
            phone_hash="hmac-1",
            photo_phashes=[PHOTO_1, PHOTO_2],
        )
        b = make_listing(
            vin=VIN_B,
            phone_hash="hmac-1",
            photo_phashes=[PHOTO_1, PHOTO_2],
        )
        result = similarity_score(a, b)
        assert result.confidence is MatchConfidence.NO_MATCH
        assert result.score == 0.0
        assert result.reasons == ["vin_conflict"]

    def test_vin_on_one_side_only_falls_through(self) -> None:
        """Площадка скрыла VIN — каскад идёт дальше, а не отвергает пару."""
        a = make_listing(vin=VIN_A, phone_hash="hmac-1")
        b = make_listing(phone_hash="hmac-1")
        result = similarity_score(a, b)
        assert result.match_reason == "phone"


class TestPlateLevel:
    """Уровень 1: госномер."""

    def test_same_plate_is_exact(self) -> None:
        a = make_listing(plate="к999кк799")
        b = make_listing(plate="K999KK799", price=810_000)
        result = similarity_score(a, b)
        assert result.confidence is MatchConfidence.EXACT
        assert result.score == pytest.approx(0.97)
        assert result.match_reason == "plate"

    def test_different_plates_without_vin_are_weak(self) -> None:
        """Спека: понижаем уверенность до 0.5 и ждём ручного подтверждения."""
        a = make_listing(plate="А001АК26", phone_hash="hmac-1")
        b = make_listing(plate="В002ВМ26", phone_hash="hmac-1")
        result = similarity_score(a, b)
        assert result.confidence is MatchConfidence.WEAK
        assert result.score == pytest.approx(0.5)
        assert result.match_reason == "plate_conflict"

    def test_vin_match_outranks_plate_conflict(self) -> None:
        a = make_listing(vin=VIN_A, plate="А001АК26")
        b = make_listing(vin=VIN_A, plate="В002ВМ26")
        assert similarity_score(a, b).confidence is MatchConfidence.EXACT


class TestPhoneAndPhotoLevels:
    """Уровни 2 и 3: телефон и перцептивный хэш фото."""

    def test_phone_and_photos_without_vin_are_strong(self) -> None:
        a = make_listing(phone_hash="hmac-1", photo_phashes=[PHOTO_1, PHOTO_2, PHOTO_3])
        b = make_listing(
            phone_hash="hmac-1",
            photo_phashes=[PHOTO_1_RECOMPRESSED, PHOTO_2_RECOMPRESSED],
            price=799_000,
            mileage_km=92_400,
        )
        result = similarity_score(a, b)
        assert result.confidence is MatchConfidence.STRONG
        assert result.score == pytest.approx(0.90)
        assert result.match_reason == "phone"
        assert "phone" in result.reasons
        assert "photo_phash" in result.reasons

    def test_phone_alone_needs_model_and_year(self) -> None:
        """Перекуп с десятком машин: один телефон — ещё не одно авто."""
        a = make_listing(phone_hash="hmac-1", model_id="kia-rio-4")
        b = make_listing(phone_hash="hmac-1", model_id="hyundai-solaris-2")
        result = similarity_score(a, b)
        assert "phone" not in result.reasons

    def test_photos_alone_are_strong(self) -> None:
        a = make_listing(photo_phashes=[PHOTO_1, PHOTO_2])
        b = make_listing(
            photo_phashes=[PHOTO_1_RECOMPRESSED, PHOTO_2_RECOMPRESSED],
            model_id="kia-rio-4",
            year=2015,
            price=500_000,
        )
        result = similarity_score(a, b)
        assert result.confidence is MatchConfidence.STRONG
        assert result.match_reason == "photo_phash"

    def test_single_matching_photo_is_not_enough(self) -> None:
        a = make_listing(photo_phashes=[PHOTO_1, PHOTO_3], model_id="kia-rio-4", price=400_000)
        b = make_listing(photo_phashes=[PHOTO_1_RECOMPRESSED, PHOTO_2])
        result = similarity_score(a, b)
        assert "photo_phash" not in result.reasons

    def test_one_photo_cannot_close_two_slots(self) -> None:
        """Два почти одинаковых ракурса не должны дать «два совпадения»."""
        a = make_listing(
            photo_phashes=[PHOTO_1, PHOTO_1 ^ 0b1], model_id="kia-rio-4", price=400_000
        )
        b = make_listing(photo_phashes=[PHOTO_1_RECOMPRESSED])
        result = similarity_score(a, b)
        assert "photo_phash" not in result.reasons


class TestAttrLevel:
    """Уровень 4: нечёткое совпадение атрибутов."""

    def test_similar_attributes_are_weak(self) -> None:
        a = make_listing()
        b = make_listing(mileage_km=92_400, price=799_000)
        result = similarity_score(a, b)
        assert result.confidence is MatchConfidence.WEAK
        assert result.match_reason == "attrs"
        assert result.score >= 0.82

    def test_different_model_is_no_match(self) -> None:
        a = make_listing()
        b = make_listing(model_id="kia-rio-4")
        result = similarity_score(a, b)
        assert result.confidence is MatchConfidence.NO_MATCH
        assert result.reasons == []

    def test_mileage_gap_lowers_score(self) -> None:
        close = attr_similarity(make_listing(), make_listing(mileage_km=92_400))
        far = attr_similarity(make_listing(), make_listing(mileage_km=140_000))
        assert close > far

    def test_price_divergence_penalty(self) -> None:
        """Цена, отличающаяся больше чем на 15%, — скорее всего, разные машины."""
        same_price = attr_similarity(make_listing(), make_listing())
        other_price = attr_similarity(make_listing(), make_listing(price=1_500_000))
        assert other_price < same_price
        assert other_price < 0.82

    def test_score_is_bounded(self) -> None:
        assert 0.0 <= attr_similarity(make_listing(), make_listing()) <= 1.0


class TestExplainability:
    """Основание склейки показывается пользователю (B3)."""

    def test_b3_wording(self) -> None:
        a = make_listing(vin=VIN_A, phone_hash="hmac-1", photo_phashes=[PHOTO_1, PHOTO_2])
        b = make_listing(
            vin=VIN_A,
            phone_hash="hmac-1",
            photo_phashes=[PHOTO_1_RECOMPRESSED, PHOTO_2_RECOMPRESSED],
            price=799_000,
        )
        text = similarity_score(a, b).explain()
        assert "VIN" in text
        assert "фото" in text
        assert "телефон продавца" in text

    def test_describe_empty(self) -> None:
        assert describe_reasons([]) == "совпадений нет"

    def test_describe_single(self) -> None:
        assert describe_reasons(["vin"]) == "совпал VIN"

    def test_same_source_duplicate_is_flagged(self) -> None:
        """Продавец пересоздал объявление на той же площадке — отдельный сигнал."""
        a = make_listing(vin=VIN_A, source_id="avito")
        b = make_listing(vin=VIN_A, source_id="avito", price=770_000)
        assert "same_source_duplicate" in similarity_score(a, b).reasons


class TestClustering:
    """Сценарий B3: одно авто на трёх площадках."""

    @staticmethod
    def b3_listings() -> list[ListingFeatures]:
        return [
            ListingFeatures(
                vin=VIN_A,
                phone_hash="hmac-1",
                photo_phashes=[PHOTO_1, PHOTO_2],
                model_id="hyundai-solaris-2",
                year=2018,
                mileage_km=92_000,
                region_id="77",
                price=785_000,
                listing_id="a-1",
                source_id="avito",
                last_seen_at=NOW,
                last_price_change_at=NOW,
            ),
            ListingFeatures(
                vin=VIN_A.lower(),
                phone_hash="hmac-1",
                photo_phashes=[PHOTO_1_RECOMPRESSED, PHOTO_2_RECOMPRESSED],
                model_id="hyundai-solaris-2",
                year=2018,
                mileage_km=92_400,
                region_id="77",
                price=799_000,
                listing_id="r-1",
                source_id="autoru",
                last_seen_at=NOW - timedelta(hours=6),
                last_price_change_at=NOW - timedelta(days=3),
            ),
            ListingFeatures(
                phone_hash="hmac-1",
                photo_phashes=[PHOTO_1, PHOTO_2],
                model_id="hyundai-solaris-2",
                year=2018,
                mileage_km=92_000,
                region_id="77",
                price=810_000,
                listing_id="d-1",
                source_id="drom",
                last_seen_at=NOW - timedelta(hours=12),
                last_price_change_at=NOW - timedelta(days=8),
            ),
        ]

    def test_three_platforms_collapse_into_one_cluster(self) -> None:
        clusters = cluster_listings(self.b3_listings())
        assert len(clusters) == 1
        assert len(clusters[0]) == 3
        assert [item.source_id for item in clusters[0]] == ["avito", "autoru", "drom"]

    def test_different_car_stays_separate(self) -> None:
        listings = [*self.b3_listings(), make_listing(vin=VIN_B, model_id="kia-rio-4")]
        clusters = cluster_listings(listings)
        assert len(clusters) == 2
        assert len(clusters[1]) == 1

    def test_vin_conflict_blocks_join_even_with_matching_photos(self) -> None:
        """Объявление не «протащит» себя в кластер через похожего соседа."""
        twin = ListingFeatures(
            vin=VIN_B,
            phone_hash="hmac-1",
            photo_phashes=[PHOTO_1, PHOTO_2],
            model_id="hyundai-solaris-2",
            year=2018,
            mileage_km=92_000,
            region_id="77",
            price=789_000,
            source_id="avito",
        )
        clusters = cluster_listings([*self.b3_listings(), twin])
        assert len(clusters) == 2
        assert clusters[1] == [twin]

    def test_cluster_size_is_capped(self) -> None:
        listings = [
            ListingFeatures(
                vin=VIN_A,
                model_id="hyundai-solaris-2",
                year=2018,
                mileage_km=92_000,
                price=785_000,
                listing_id=f"x-{index}",
            )
            for index in range(MAX_CLUSTER_SIZE + 3)
        ]
        clusters = cluster_listings(listings)
        assert len(clusters[0]) == MAX_CLUSTER_SIZE
        assert sum(len(cluster) for cluster in clusters) == len(listings)

    def test_weak_attrs_do_not_grow_cluster_beyond_three(self) -> None:
        """Кластер больше трёх обязан держаться на признаке уровня 0–2."""
        listings = [
            make_listing(mileage_km=92_000 + index * 100, listing_id=f"w-{index}")
            for index in range(5)
        ]
        clusters = cluster_listings(listings)
        assert len(clusters[0]) == 3

    def test_empty_input(self) -> None:
        assert cluster_listings([]) == []


class TestCanonicalPrice:
    """Каноническая цена: самое свежее, при равенстве — самое дешёвое."""

    def test_b3_scenario(self) -> None:
        """Авито «сегодня» 785 000 побеждает Авто.ру 799 000 и Дром 810 000."""
        chosen = canonical_price(TestClustering.b3_listings(), now=NOW)
        assert chosen.price == 785_000
        assert chosen.listing.source_id == "avito"
        assert chosen.price_min == 785_000
        assert chosen.price_max == 810_000
        assert chosen.spread == 25_000

    def test_all_stale_falls_back_to_minimum(self) -> None:
        stale = [
            ListingFeatures(price=810_000, last_seen_at=NOW - timedelta(days=30)),
            ListingFeatures(price=785_000, last_seen_at=NOW - timedelta(days=40)),
        ]
        assert canonical_price(stale, now=NOW).price == 785_000

    def test_inactive_listings_are_ignored(self) -> None:
        listings = [
            ListingFeatures(price=700_000, status="sold", last_seen_at=NOW),
            ListingFeatures(price=790_000, status="active", last_seen_at=NOW),
        ]
        assert canonical_price(listings, now=NOW).price == 790_000

    def test_fresher_price_change_wins_over_lower_price(self) -> None:
        listings = [
            ListingFeatures(
                price=760_000,
                last_seen_at=NOW - timedelta(days=2, hours=12),
                last_price_change_at=NOW - timedelta(days=2, hours=12),
                source_id="drom",
            ),
            ListingFeatures(
                price=790_000,
                last_seen_at=NOW,
                last_price_change_at=NOW,
                source_id="avito",
            ),
        ]
        chosen = canonical_price(listings, now=NOW)
        assert chosen.listing.source_id == "avito"
        assert chosen.price == 790_000

    def test_empty_cluster_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="пустым"):
            canonical_price([])
