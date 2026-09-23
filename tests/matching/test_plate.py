"""Тесты нормализации госномера (CC5, §4.6 дизайн-системы)."""

from __future__ import annotations

import pytest

from avtoklik.matching.plate import (
    PLATE_LETTERS,
    PlateKind,
    PlateParseError,
    explain_plate_error,
    is_valid_plate,
    normalize_plate,
    parse_plate,
)


class TestNormalizeBasic:
    """Приведение к канону: регистр, пробелы, разделители."""

    @pytest.mark.parametrize(
        "raw",
        [
            "К999КК799",
            "к999кк799",
            "К 999 КК 799",
            "  к999 кк-799  ",
            "к999кк/799",
        ],
    )
    def test_canonical_form(self, raw: str) -> None:
        assert normalize_plate(raw) == "К999КК799"

    def test_two_digit_region(self) -> None:
        assert normalize_plate("а001ак26") == "А001АК26"

    def test_three_digit_region(self) -> None:
        assert normalize_plate("к999кк799") == "К999КК799"

    @pytest.mark.parametrize("region", ["102", "716", "977"])
    def test_three_digit_region_prefixes(self, region: str) -> None:
        """Трёхзначный код начинается с 1, 7 или 9."""
        assert normalize_plate(f"А001АА{region}") == f"А001АА{region}"

    @pytest.mark.parametrize("region", ["202", "350", "812"])
    def test_three_digit_region_wrong_prefix(self, region: str) -> None:
        assert normalize_plate(f"А001АА{region}") is None

    def test_region_zero_zero_rejected(self) -> None:
        assert normalize_plate("А001АА00") is None


class TestLayoutRepair:
    """Латинская раскладка конвертируется молча, без ошибки (§4.6)."""

    def test_full_latin_input(self) -> None:
        assert normalize_plate("K999KK799") == "К999КК799"

    def test_lowercase_latin(self) -> None:
        assert normalize_plate("a001ak26") == "А001АК26"

    def test_mixed_layout(self) -> None:
        """Пользователь переключил раскладку посреди ввода."""
        assert normalize_plate("К999KK799") == "К999КК799"

    def test_every_latin_twin_has_cyrillic_pair(self) -> None:
        latin = "ABEKMHOPCTYX"
        for latin_letter, cyrillic_letter in zip(latin, "АВЕКМНОРСТУХ", strict=True):
            assert normalize_plate(f"{latin_letter}001{latin_letter}{latin_letter}26") == (
                f"{cyrillic_letter}001{cyrillic_letter}{cyrillic_letter}26"
            )
        assert len(set(latin)) == len(PLATE_LETTERS)


class TestZeroLetterOTypo:
    """«О» вместо «0» и «0» вместо «О» — частая опечатка из CC5."""

    def test_letter_o_in_digit_position(self) -> None:
        """Пользователь набрал букву О там, где должен быть ноль."""
        assert normalize_plate("АОО1АК26") == "А001АК26"

    def test_zero_in_letter_position(self) -> None:
        """И наоборот: ноль там, где должна быть буква О."""
        assert normalize_plate("0123ММ77") == "О123ММ77"

    def test_zero_in_both_letter_positions(self) -> None:
        assert normalize_plate("О12300 77") == "О123ОО77"

    def test_letter_o_in_region(self) -> None:
        assert normalize_plate("А001АА7О") == "А001АА70"

    def test_latin_o_is_repaired_too(self) -> None:
        """Латинская O → кириллическая О → ноль, если позиция цифровая."""
        assert normalize_plate("AOO1AK26") == "А001АК26"

    def test_clean_input_wins_over_repair(self) -> None:
        """Корректный ввод не должен «чиниться» в другой формат."""
        assert normalize_plate("О001ОО77") == "О001ОО77"


class TestInvalidInput:
    """Всё, что починить нельзя."""

    @pytest.mark.parametrize("raw", ["Ж001АК26", "Ц123ММ77", "А001ЖД26", "Д001ФФ99"])
    def test_letters_outside_alphabet(self, raw: str) -> None:
        """Ж, Ц, Д, Ф на российских знаках не встречаются."""
        assert normalize_plate(raw) is None
        assert not is_valid_plate(raw)

    @pytest.mark.parametrize(
        "raw",
        ["", "   ", "---", "не номер", "А001", "А001АК", "А001АК2", "А001АК2612", "12345678"],
    )
    def test_garbage(self, raw: str) -> None:
        assert normalize_plate(raw) is None

    def test_alphabet_has_exactly_twelve_letters(self) -> None:
        assert len(PLATE_LETTERS) == 12
        assert PLATE_LETTERS == "АВЕКМНОРСТУХ"

    @pytest.mark.parametrize("letter", "БГДЖЗИЙЛПФЦЧШЩЫЭЮЯ")
    def test_forbidden_cyrillic_letters(self, letter: str) -> None:
        assert normalize_plate(f"{letter}001АК26") is None


class TestOtherFormats:
    """Такси и прицепы — второстепенные форматы."""

    def test_taxi_plate(self) -> None:
        parsed = parse_plate("аа12377")
        assert parsed.text == "АА12377"
        assert parsed.kind is PlateKind.TAXI

    def test_trailer_plate(self) -> None:
        parsed = parse_plate("ав123450")
        assert parsed.text == "АВ123450"
        assert parsed.kind is PlateKind.TRAILER

    def test_passenger_wins_when_ambiguous_by_length(self) -> None:
        parsed = parse_plate("К999КК79")
        assert parsed.kind is PlateKind.PASSENGER
        assert parsed.region == "79"


class TestExplainError:
    """CC5: ошибка объясняет алфавит и правило региона."""

    def test_empty_input(self) -> None:
        message = explain_plate_error("")
        assert "Введите госномер" in message

    def test_bad_letters_are_listed(self) -> None:
        message = explain_plate_error("Ж001ЦК26")
        assert "Ж" in message
        assert "Ц" in message
        assert "А В Е К М Н О Р С Т У Х" in message

    def test_region_message_mentions_digits(self) -> None:
        message = explain_plate_error("А001АА212")
        assert "2 или 3 цифры" in message
        assert "1, 7 или 9" in message

    def test_structure_message_mentions_typo_hint(self) -> None:
        message = explain_plate_error("А0011АК26")
        assert "Частая опечатка" in message

    def test_valid_plate_reports_success(self) -> None:
        assert explain_plate_error("к999кк799") == "Номер распознан."


class TestParsePlate:
    """Строгий вариант с исключением."""

    def test_returns_parsed(self) -> None:
        parsed = parse_plate(" к 999 кк 799 ")
        assert parsed.text == "К999КК799"
        assert parsed.kind is PlateKind.PASSENGER
        assert parsed.region == "799"

    def test_raises_with_human_message(self) -> None:
        with pytest.raises(PlateParseError) as exc_info:
            parse_plate("Ж001ЦК26")
        assert "А В Е К М Н О Р С Т У Х" in str(exc_info.value)

    def test_is_valid_plate(self) -> None:
        assert is_valid_plate("K999KK799")
        assert not is_valid_plate("Ж001ЦК26")
