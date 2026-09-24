"""Экраны продажи E1 → E3 → E4.

Проверяется сквозной путь и то, что граница данных из :mod:`avtoklik.service.sell`
не протекает в разметку: значения, которые модуль придержал, не должны
появиться на странице ни в каком виде.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from avtoklik.api.app import create_app
from avtoklik.service.sources.fixtures import OCTAVIA_PLATE, RIO_PLATE

NBSP = " "


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _start(client: TestClient, **data: str) -> str:
    """Создать черновик и вернуть его идентификатор."""
    response = client.post("/sell", data=data, follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"].rstrip("/").split("/")[-2]


class TestFirstStep:
    def test_form_opens(self, client: TestClient) -> None:
        response = client.get("/sell")
        assert response.status_code == 200
        assert "Продать автомобиль" in response.text

    def test_unknown_plate_explains_and_offers_a_way_round(self, client: TestClient) -> None:
        """CC5: что случилось · почему · что делать. Обходной путь обязателен."""
        response = client.post("/sell", data={"subject": "ЫЫЫ"}, follow_redirects=False)
        assert response.status_code == 422
        assert "не находится в базах" in response.text
        assert "VIN" in response.text

    def test_mileage_typed_with_spaces_is_understood(self, client: TestClient) -> None:
        """«134 000» — это то, как человек пишет число, а не ошибка ввода."""
        draft_id = _start(client, subject=OCTAVIA_PLATE, mileage_km="134 000")
        body = client.get(f"/sell/{draft_id}/price").text
        assert f"134{NBSP}000{NBSP}км" in body


class TestPriceStep:
    def test_autofilled_fields_are_shown_with_their_source(self, client: TestClient) -> None:
        draft_id = _start(client, subject=OCTAVIA_PLATE, mileage_km="134000")
        body = client.get(f"/sell/{draft_id}/price").text
        assert "Заполнили сами" in body
        assert "1.4 TSI DSG" in body
        assert "ГИБДД" in body

    def test_withheld_values_never_reach_the_page(self, client: TestClient) -> None:
        """Граница данных проверяется на разметке, а не только на модуле."""
        draft_id = _start(client, subject=RIO_PLATE, mileage_km="92000")
        body = client.get(f"/sell/{draft_id}/price").text
        assert "Покажем после подтверждения СТС" in body
        assert "Владельцев по ПТС" not in body

    def test_confirmed_ownership_unlocks_the_history(self, client: TestClient) -> None:
        draft_id = _start(client, subject=RIO_PLATE, mileage_km="92000", ownership="1")
        body = client.get(f"/sell/{draft_id}/price").text
        assert "Владельцев по ПТС" in body
        assert "Покажем после подтверждения СТС" not in body

    def test_scale_reproduces_the_mockup(self, client: TestClient) -> None:
        draft_id = _start(client, subject=OCTAVIA_PLATE, mileage_km="134000")
        body = client.get(f"/sell/{draft_id}/price").text.replace(NBSP, " ")
        for price, days in (("1 249 000", "≈ 6 дней"), ("1 340 000", "≈ 25 дней")):
            assert price in body
            assert days in body

    def test_days_are_never_shown_with_a_fraction(self, client: TestClient) -> None:
        """5.3.3: «12,4 дня» — ложная точность."""
        draft_id = _start(client, subject=OCTAVIA_PLATE, mileage_km="134000")
        body = client.get(f"/sell/{draft_id}/price").text
        assert re.search(r"≈ \d+[.,]\d", body) is None

    def test_thin_segment_asks_the_seller_for_a_price(self, client: TestClient) -> None:
        draft_id = _start(client, subject=RIO_PLATE, mileage_km="92000")
        body = client.get(f"/sell/{draft_id}/price").text
        assert "слишком мало похожих машин" in body
        assert "Назначьте свою" in body

    def test_unknown_draft_answers_404(self, client: TestClient) -> None:
        assert client.get("/sell/no-such-draft/price").status_code == 404


class TestPublication:
    def test_published_car_appears_in_the_showcase(self, client: TestClient) -> None:
        draft_id = _start(client, subject=OCTAVIA_PLATE, mileage_km="134000", region_id="78")
        client.post(f"/sell/{draft_id}/price", data={"price_rub": "1292000"})
        done = client.get(f"/sell/{draft_id}/done")
        assert "Опубликовано" in done.text
        assert f"avtoklik-{draft_id}" in client.get("/").text

    def test_published_car_opens_as_an_ordinary_card(self, client: TestClient) -> None:
        draft_id = _start(client, subject=OCTAVIA_PLATE, mileage_km="134000")
        client.post(f"/sell/{draft_id}/price", data={"price_rub": "1292000"})
        card = client.get(f"/car/avtoklik-{draft_id}")
        assert card.status_code == 200
        assert "Škoda Octavia A7" in card.text
        # Блок проверки по базам на своём объявлении такой же, как на чужом.
        assert 'id="check-title"' in card.text

    def test_rejection_lists_the_fixes(self, client: TestClient) -> None:
        draft_id = _start(
            client,
            subject=OCTAVIA_PLATE,
            mileage_km="134000",
            description="звоните 8 999 123-45-67",
        )
        client.post(f"/sell/{draft_id}/price", data={"price_rub": "1292000"})
        body = client.get(f"/sell/{draft_id}/done").text
        assert "Нужно поправить" in body
        assert "Уберите номер" in body
        assert "Вернуться к объявлению" in body

    def test_moderation_is_not_claimed_to_exist(self, client: TestClient) -> None:
        """Человеческая модерация не подключена, и обещать её нельзя."""
        draft_id = _start(client, subject=OCTAVIA_PLATE, mileage_km="134000")
        client.post(f"/sell/{draft_id}/price", data={"price_rub": "1292000"})
        body = client.get(f"/sell/{draft_id}/done").text
        assert "модерация пока не подключена" in body.lower()
