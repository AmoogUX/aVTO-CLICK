"""Извлечение фактов из текста отзыва (5.6.2, шаг 3; цензурирование — 5.6.4)."""

from __future__ import annotations

import pytest

from avtoklik.collector.extraction import (
    extract_censoring_markers,
    extract_cost,
    extract_facts,
    extract_mileage,
)
from avtoklik.collector.fixtures import load_review, load_reviews


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("пробег 92 тыс", 92000),
        ("пробег 92000 км", 92000),
        ("пробег 92 т.км", 92000),
        ("пробег 92 тыс. км", 92000),
        ("пробег 92т км", 92000),
        ("пробег 92 000 км", 92000),
        ("пробег 150 тысяч", 150000),
        ("пробег 150 тысяч км", 150000),
        ("пробег 92 000 км", 92000),  # неразрывный пробел
        ("пробег 92000км", 92000),
    ],
)
def test_formaty_probega(text: str, expected: int) -> None:
    """Все написания из спеки нормализуются в километры."""
    assert extract_mileage(text) == [expected]


@pytest.mark.parametrize(
    "text",
    [
        "Машина 2017 года выпуска",
        "Купил в 2017 году, всё нравится",
        "Отдал за ремонт 92 000 руб",
        "Ценник у официалов 90 тыс. руб",
        "Замена обошлась в 15 тр",
        "Стоит 45 тыс. рублей",
    ],
)
def test_lozhnye_srabatyvaniya_probega(text: str) -> None:
    """Год и деньги — не пробег. Цена ошибки здесь — перекошенная модель дожития."""
    assert extract_mileage(text) == []


def test_neskolko_probegov_v_poryadke_teksta() -> None:
    """Порядок сохраняется: он нужен для привязки пробега к эпизоду поломки."""
    text = "Взял на 74 000 км, рейка застучала на 92 тыс. км, сейчас 118 т.км."
    assert extract_mileage(text) == [74000, 92000, 118000]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ремонт 15 тр", 15000),
        ("ремонт 15 000 руб", 15000),
        ("ремонт 15т.р.", 15000),
        ("ремонт 15 000 ₽", 15000),
        ("ремонт 15 тыс. руб", 15000),
        ("ремонт 15 т₽", 15000),
        ("ремонт 15000 рублей", 15000),
        ("ремонт 15 000 р.", 15000),
    ],
)
def test_formaty_stoimosti(text: str, expected: int) -> None:
    """Все написания суммы нормализуются в рубли."""
    assert extract_cost(text) == [expected]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("замена 12–18 т₽", [12000, 18000]),
        ("замена 12-18 тр", [12000, 18000]),
        ("замена 12 — 18 тыс. руб", [12000, 18000]),
        ("замена 15 000 - 20 000 руб", [15000, 20000]),
    ],
)
def test_diapazon_stoimosti_dayot_dve_granitsy(text: str, expected: list[int]) -> None:
    """Вилка цены — это два числа. Схлопывать её в середину нельзя: на C2 показывается диапазон."""
    assert extract_cost(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Пробег 92 тыс. км",
        "Ездил 2 раза в сервис",
        "Машина 2017 года",
    ],
)
def test_lozhnye_srabatyvaniya_stoimosti(text: str) -> None:
    """Пробег, год и слова на «р» деньгами не становятся."""
    assert extract_cost(text) == []


def test_probeg_i_stoimost_v_odnom_predlozhenii() -> None:
    """Типичный случай корпуса: эпизод «пробег + цена ремонта» в одной фразе."""
    facts = extract_facts("На 92 тыс. км застучала рейка, ремонт втулок вышел 15 тр.")

    assert facts.mileages_km == (92000,)
    assert facts.costs_rub == (15000,)
    assert facts.is_censored_observation is False


@pytest.mark.parametrize(
    "text",
    [
        "180 тыс км — ничего не ломалось",
        "150 т.км, проблем не было",
        "Пробег 200 000 км, полёт нормальный",
        "За 150 тысяч км ни разу не подвела",
        "120 тыс. км — нареканий нет",
        "Рейка не стучала до 150 тыс. км",
    ],
)
def test_tsenzurirovannoe_nablyudenie_raspoznayotsya(text: str) -> None:
    """«Пробег 150 тысяч, рейка не стучала» — наблюдение, а не пустой отзыв (5.6.4)."""
    facts = extract_facts(text)

    assert facts.censoring_markers
    assert facts.is_censored_observation is True
    assert facts.max_mileage_km is not None


def test_bez_probega_tsenzurirovaniya_net() -> None:
    """Без пробега свидетельство «ничего не ломалось» функции дожития ничего не даёт."""
    facts = extract_facts(
        "Ничего не ломалось, но пробег скручен и сколько на самом деле — не знаю."
    )

    assert facts.censoring_markers
    assert facts.mileages_km == ()
    assert facts.is_censored_observation is False


def test_markery_normalizuyutsya_po_probelam() -> None:
    """Фраза, разорванная переносом строки, приходит редактору в читаемом виде."""
    assert extract_censoring_markers("180 тыс км — ничего не\nломалось") == ["ничего не ломалось"]


def test_korpus_fikstur_razbiraetsya() -> None:
    """Прогон по учебному корпусу: числа берутся из текста, выдуманных нет."""
    reviews = load_reviews()
    assert len(reviews) >= 5

    for name, text in reviews.items():
        facts = extract_facts(text)
        assert all(0 < value <= 2_000_000 for value in facts.mileages_km), name
        assert all(0 < value <= 5_000_000 for value in facts.costs_rub), name
        # Каждое извлечённое число обязано встречаться в исходном тексте —
        # хотя бы своей значащей частью. Это и есть граница «не придумываем числа».
        for value in facts.mileages_km:
            assert str(value)[:2] in text.replace(" ", " "), (name, value)


def test_fikstura_s_reykoy() -> None:
    """Опорный пример: пробеги, обе цены и цензурирующая фраза одновременно."""
    facts = extract_facts(load_review("01_rio3_rulevaya_reyka"))

    assert facts.mileages_km == (74000, 118000, 92000)
    assert facts.costs_rub == (48000, 15000)
    assert facts.censoring_markers == ("полёт нормальный",)


def test_fikstura_s_diapazonom_tseny() -> None:
    """Диапазон «12–18 т₽» из текста отзыва даёт обе границы."""
    facts = extract_facts(load_review("02_solaris_katalizator"))

    assert facts.mileages_km == (118000, 105000)
    assert facts.costs_rub[:3] == (12000, 18000, 14000)


def test_fikstura_bez_polomok() -> None:
    """Отзыв «ничего не ломалось на 180 тыс» — полноценное правоцензурированное наблюдение."""
    facts = extract_facts(load_review("06_camry_bez_polomok"))

    assert facts.mileages_km == (180000,)
    assert facts.is_censored_observation is True
    assert facts.max_mileage_km == 180000
