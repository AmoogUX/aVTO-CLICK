"""Флоу продажи: автозаполнение, цена, автопроверки, выход в витрину.

Главный инвариант этого модуля — не цена и не статусы, а граница данных:
по чужому госномеру нельзя получить историю чужой машины. Он проверяется
первым и отдельно, потому что нарушить его можно одной строкой в шаблоне.
"""

from __future__ import annotations

from datetime import date

import pytest

from avtoklik.service.sell import (
    DraftStatus,
    autofill,
    create_draft,
    get_draft,
    publish_draft,
    run_auto_checks,
    set_price,
    suggest_price,
)
from avtoklik.service.showcase import OWN_PLATFORM, get_car, list_showcase
from avtoklik.service.sources.fixtures import OCTAVIA_PLATE, RIO_PLATE

TODAY = date(2026, 9, 24)


class TestOwnershipBoundary:
    """Автозаполнение не отдаёт историю конкретной машины без подтверждения."""

    def test_model_facts_are_public(self) -> None:
        """Марка, год и двигатель — характеристики выпуска, а не чья-то тайна."""
        found = autofill(RIO_PLATE)
        assert found.found
        names = {item.name for item in found.filled}
        assert {"brand", "model", "year", "engine"} <= names

    def test_history_is_withheld_without_proof(self) -> None:
        found = autofill(RIO_PLATE)
        names = {item.name for item in found.filled}
        assert "owners" not in names
        assert "mileage" not in names

    def test_withheld_names_the_fields_but_never_their_values(self) -> None:
        """Продавец должен понимать, что получит, не получая этого заранее."""
        found = autofill(RIO_PLATE)
        assert found.withheld
        joined = " ".join(found.withheld)
        assert "2" not in joined  # число владельцев в фикстуре — два

    def test_proof_unlocks_the_history(self) -> None:
        found = autofill(RIO_PLATE, ownership_confirmed=True)
        names = {item.name for item in found.filled}
        assert "owners" in names
        assert found.withheld == ()

    def test_unknown_plate_finds_nothing(self) -> None:
        assert autofill("А000АА777").found is False

    def test_garbage_input_is_not_an_error(self) -> None:
        """CC5: непонятный ввод — это объяснение и обходной путь, а не исключение."""
        assert autofill("ЫЫЫ").found is False


class TestDraft:
    def test_draft_survives_a_reload(self) -> None:
        """Продавец уходит за пробегом в ПТС и возвращается по ссылке."""
        draft, _ = create_draft(OCTAVIA_PLATE, mileage_km=134_000, today=TODAY)
        restored = get_draft(draft.draft_id)
        assert restored is not None
        assert restored.mileage_km == 134_000
        assert restored.filled  # автозаполнение сохранено вместе с черновиком

    def test_draft_is_created_even_when_nothing_was_found(self) -> None:
        """Иначе продукт отказывает владельцу редкой машины из-за своего незнания."""
        draft, found = create_draft("А000АА777", mileage_km=200_000, today=TODAY)
        assert found.found is False
        assert get_draft(draft.draft_id) is not None

    def test_own_price_is_allowed(self) -> None:
        """Оценка — наш совет, цена — решение продавца."""
        draft, _ = create_draft(OCTAVIA_PLATE, mileage_km=134_000, today=TODAY)
        updated = set_price(draft.draft_id, 1_500_000)
        assert updated is not None
        assert updated.price_rub == 1_500_000

    def test_non_positive_price_is_refused(self) -> None:
        draft, _ = create_draft(OCTAVIA_PLATE, mileage_km=134_000, today=TODAY)
        with pytest.raises(ValueError):
            set_price(draft.draft_id, 0)


class TestSuggestedPrice:
    def test_reference_car_reproduces_the_mockup(self) -> None:
        draft, _ = create_draft(OCTAVIA_PLATE, mileage_km=134_000, today=TODAY)
        anchors = suggest_price(draft)
        assert anchors is not None
        assert anchors.recommended.price_rub == 1_292_000
        assert (anchors.fast.days, anchors.slow.days) == (6, 25)

    def test_thin_segment_offers_no_price(self) -> None:
        draft, _ = create_draft(RIO_PLATE, mileage_km=92_000, today=TODAY)
        assert suggest_price(draft) is None


class TestAutoChecks:
    def test_phone_in_the_description_is_caught(self) -> None:
        draft, _ = create_draft(
            OCTAVIA_PLATE,
            mileage_km=134_000,
            description="Звоните 8 999 123-45-67",
            today=TODAY,
        )
        set_price(draft.draft_id, 1_292_000)
        published = publish_draft(draft.draft_id)
        assert published is not None
        assert published.status is DraftStatus.REJECTED
        assert any(issue.field == "description" for issue in published.issues)

    def test_every_issue_says_what_to_do(self) -> None:
        """§1.6: слот «что сделать» не может быть пустым."""
        draft, _ = create_draft(OCTAVIA_PLATE, description="http://example.com", today=TODAY)
        for issue in run_auto_checks(draft):
            assert issue.problem and issue.fix

    def test_rejection_keeps_the_draft_editable(self) -> None:
        """CC6: отказ — это список правок, а не тупик."""
        draft, _ = create_draft(OCTAVIA_PLATE, today=TODAY)
        publish_draft(draft.draft_id)
        restored = get_draft(draft.draft_id)
        assert restored is not None
        assert restored.status is DraftStatus.REJECTED
        assert restored.issues

    @pytest.mark.parametrize("mileage", [5, 3_000_000])
    def test_implausible_mileage_is_questioned(self, mileage: int) -> None:
        draft, _ = create_draft(OCTAVIA_PLATE, mileage_km=mileage, today=TODAY)
        set_price(draft.draft_id, 1_292_000)
        published = publish_draft(draft.draft_id)
        assert published is not None
        assert published.status is DraftStatus.REJECTED


class TestPublication:
    def _publish(self) -> str:
        draft, _ = create_draft(OCTAVIA_PLATE, mileage_km=134_000, region_id=78, today=TODAY)
        set_price(draft.draft_id, 1_292_000)
        published = publish_draft(draft.draft_id)
        assert published is not None
        assert published.status is DraftStatus.PUBLISHED
        return f"avtoklik-{draft.draft_id}"

    def test_published_listing_enters_the_showcase(self) -> None:
        """Ради этого флоу и переносили в релиз 1: витрина наполняется сама."""
        listing_id = self._publish()
        assert any(car.listing_id == listing_id for car in list_showcase())

    def test_own_listing_opens_as_an_ordinary_card(self) -> None:
        """Своё объявление ничем не отличается от чужого, кроме площадки."""
        car = get_car(self._publish())
        assert car is not None
        assert car.listing.platform == OWN_PLATFORM

    def test_own_listings_come_first(self) -> None:
        """Продавец обязан увидеть своё объявление сразу после публикации."""
        listing_id = self._publish()
        assert list_showcase()[0].listing_id == listing_id

    def test_estimate_is_kept_beside_the_price(self) -> None:
        """Иначе бейдж «в рынке» превращается в утверждение, которое нечем поверить."""
        car = get_car(self._publish())
        assert car is not None
        assert car.market_price_rub == 1_292_000

    def test_publishing_an_unknown_draft_returns_none(self) -> None:
        assert publish_draft("no-such-draft") is None
