"""Страницы веб-клиента: витрина, карточка, ошибка.

Проверяется не вёрстка (её проверяют глазами), а инварианты, которые легко
сломать правкой шаблона и невозможно заметить на ревью: порядок блоков
карточки, код ответа на снятое объявление, наличие мета-тега вьюпорта и
отсутствие обращений к третьим сторонам.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from avtoklik.api.app import create_app
from avtoklik.service.showcase import list_showcase
from avtoklik.web.routes import STATIC_DIR, plural


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


class TestShowcase:
    def test_showcase_opens(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.status_code == 200
        assert "Автомобили с пробегом" in response.text

    def test_every_car_is_in_the_feed_and_links_to_its_card(self, client: TestClient) -> None:
        body = client.get("/").text
        for car in list_showcase():
            assert f"/car/{car.listing_id}" in body

    def test_feed_is_a_list_for_a_screen_reader(self, client: TestClient) -> None:
        """Лента объявлений — список, а не набор div'ов: иначе её не пролистать."""
        body = client.get("/").text
        assert '<ul class="grid">' in body


class TestCard:
    def test_card_opens(self, client: TestClient) -> None:
        response = client.get("/car/avito-3141592653")
        assert response.status_code == 200
        assert "Kia Rio III" in response.text

    def test_free_repair_block_comes_before_the_paid_check(self, client: TestClient) -> None:
        """Требование 5.5а и прямая просьба заказчика: польза выше пейволла.

        Порядок держится структурой документа, а не CSS, поэтому проверяется
        по позиции в HTML: перестановка колонок на широком экране не должна
        уметь поднять платный блок выше бесплатного.
        """
        body = client.get("/car/avito-3141592653").text
        assert body.index('id="repair-title"') < body.index('id="check-title"')

    def test_card_shows_the_estimate_as_a_range(self, client: TestClient) -> None:
        """Одно «точное» число выдало бы выход вероятностной модели за факт."""
        body = client.get("/car/avito-3141592653").text
        amount = re.search(r'<p class="amount">(.+?)</p>', body)
        assert amount is not None
        assert amount.group(1).count("₽") == 2

    def test_uncalibrated_prevalence_is_never_shown_as_a_percentage(
        self, client: TestClient
    ) -> None:
        """5.6.4: доля упоминаний в отзывах смещена к жалобам и не есть частота."""
        body = client.get("/car/avito-3141592653").text
        block = body[body.index('id="repair-title"') : body.index('id="check-title"')]
        assert "Встречаемость болячек ещё не откалибрована" in block
        assert "%" not in block

    def test_model_without_data_says_so_and_still_offers_the_check(
        self, client: TestClient
    ) -> None:
        body = client.get("/car/autoru-2240781").text
        assert "данных пока мало" in body
        assert 'id="check-title"' in body

    def test_risky_car_states_the_risk_in_words(self, client: TestClient) -> None:
        """Рамка и полоса — цвет; §3.4 требует текст рядом с ними."""
        body = client.get("/car/avito-2718281828").text
        assert "похоже на перепродажу" in body
        assert "перекуп" not in body

    def test_removed_listing_answers_404(self, client: TestClient) -> None:
        """Иначе поисковик проиндексирует снятое объявление как живое."""
        response = client.get("/car/no-such-listing")
        assert response.status_code == 404
        assert "Вернуться на витрину" in response.text


class TestAdaptiveShell:
    """Адаптив — это контракт страницы, а не украшение."""

    @pytest.mark.parametrize("path", ["/", "/car/avito-3141592653"])
    def test_viewport_meta_is_present(self, client: TestClient, path: str) -> None:
        """Без него мобильный браузер рисует страницу в окне 980 px.

        Тогда ни один медиазапрос не срабатывает, и весь адаптив мёртв.
        """
        body = client.get(path).text
        assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in body

    def test_stylesheet_declares_all_three_breakpoints(self) -> None:
        css = (STATIC_DIR / "css" / "app.css").read_text(encoding="utf-8")
        for width in (600, 960, 1400):
            assert f"@media (min-width: {width}px)" in css

    def test_grid_starts_from_one_column(self) -> None:
        """Mobile first: телефон получает вёрстку без единого медиазапроса."""
        css = (STATIC_DIR / "css" / "app.css").read_text(encoding="utf-8")
        base = css[: css.index("@media (min-width: 600px)")]
        assert "grid-template-columns: 1fr;" in base

    def test_reduced_motion_is_respected(self) -> None:
        css = (STATIC_DIR / "css" / "app.css").read_text(encoding="utf-8")
        assert "@media (prefers-reduced-motion: reduce)" in css

    @pytest.mark.parametrize("path", ["/", "/car/avito-3141592653"])
    def test_pages_do_not_load_anything_from_third_parties(
        self, client: TestClient, path: str
    ) -> None:
        """Внешний шрифт или скрипт — это обращение браузера пользователя
        к третьей стороне на каждой странице, включая её логи.

        Ссылки на сами объявления не считаются: по ним человек переходит сам.
        """
        body = client.get(path).text
        head = body[: body.index("</head>")]
        external = [
            url
            for url in re.findall(r'(?:href|src)="([^"]+)"', head)
            if url.startswith(("http://", "https://", "//"))
            and not url.startswith("http://testserver/")
        ]
        assert external == []

    def test_stylesheets_are_served(self, client: TestClient) -> None:
        for name in ("tokens.css", "app.css"):
            assert client.get(f"/static/css/{name}").status_code == 200


class TestPlural:
    @pytest.mark.parametrize(
        ("count", "expected"),
        [
            (1, "объявление"),
            (2, "объявления"),
            (5, "объявлений"),
            (11, "объявлений"),
            (21, "объявление"),
            (104, "объявления"),
            (112, "объявлений"),
        ],
    )
    def test_russian_plural(self, count: int, expected: str) -> None:
        assert plural(count, "объявление", "объявления", "объявлений") == expected
