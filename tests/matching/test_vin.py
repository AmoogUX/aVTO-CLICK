"""Тесты нормализации и валидации VIN (уровень 0 каскада, §5.2)."""

from __future__ import annotations

import pytest

from avtoklik.matching.vin import (
    VIN_LENGTH,
    has_valid_check_digit,
    is_valid_vin,
    normalize_vin,
    vin_check_digit,
)

# Реальные VIN с корректной контрольной цифрой (9-я позиция).
VALID_NA_VIN = "1HGCM82633A004352"
VALID_NA_VIN_WITH_X = "1M8GDM9AXKP042788"
# Реальный европейский VIN: контрольная цифра не соблюдена — и это норма.
EUROPEAN_VIN = "WBA3A5C51DF598900"


class TestNormalize:
    """Приведение к канону."""

    def test_uppercase(self) -> None:
        assert normalize_vin(VALID_NA_VIN.lower()) == VALID_NA_VIN

    def test_separators_are_dropped(self) -> None:
        assert normalize_vin(" 1HGCM826-33A 004352 ") == VALID_NA_VIN

    @pytest.mark.parametrize("raw", ["", "1HGCM82633A00435", "1HGCM82633A0043521", "мусор"])
    def test_wrong_length_is_rejected(self, raw: str) -> None:
        assert normalize_vin(raw) is None

    def test_length_constant(self) -> None:
        assert VIN_LENGTH == 17
        vin = normalize_vin(VALID_NA_VIN)
        assert vin is not None
        assert len(vin) == VIN_LENGTH

    def test_unknown_symbols_are_rejected(self) -> None:
        assert normalize_vin("1HGCM82633A00435*") is None


class TestForbiddenLetters:
    """I, O, Q в VIN не бывает — их чиним, а не отвергаем."""

    def test_letter_i_becomes_one(self) -> None:
        assert normalize_vin("IHGCM82633A004352") == "1HGCM82633A004352"

    def test_letter_o_becomes_zero(self) -> None:
        assert normalize_vin("1HGCM82633AO04352") == "1HGCM82633A004352"

    def test_letter_q_becomes_zero(self) -> None:
        assert normalize_vin("1HGCM82633AQ04352") == "1HGCM82633A004352"

    def test_no_forbidden_letters_in_result(self) -> None:
        vin = normalize_vin("IOQCM82633AOQ4352")
        assert vin is not None
        assert not (set(vin) & set("IOQ"))

    def test_cyrillic_layout(self) -> None:
        """Пользователь скопировал VIN, набранный в русской раскладке."""
        assert normalize_vin("1НGСМ82633А004352") == VALID_NA_VIN


class TestCheckDigit:
    """Контрольная цифра по ISO 3779."""

    def test_known_valid_vin(self) -> None:
        assert vin_check_digit(VALID_NA_VIN) == "3"
        assert has_valid_check_digit(VALID_NA_VIN)

    def test_remainder_ten_is_x(self) -> None:
        assert vin_check_digit(VALID_NA_VIN_WITH_X) == "X"
        assert has_valid_check_digit(VALID_NA_VIN_WITH_X)

    def test_broken_vin_fails(self) -> None:
        """Ломаем одну цифру — контрольная перестаёт сходиться."""
        broken = "1HGCM82633A004353"
        assert not has_valid_check_digit(broken)

    def test_raises_on_unnormalized_input(self) -> None:
        with pytest.raises(ValueError, match="нормализован"):
            vin_check_digit("1HGCM82633A0043")


class TestIsValidVin:
    """Мягкая проверка по умолчанию, строгая — по флагу."""

    def test_soft_mode_accepts_valid_vin(self) -> None:
        assert is_valid_vin(VALID_NA_VIN)

    def test_strict_mode_accepts_valid_vin(self) -> None:
        assert is_valid_vin(VALID_NA_VIN, strict_checksum=True)

    def test_strict_mode_rejects_broken_check_digit(self) -> None:
        assert is_valid_vin("1HGCM82633A004353", strict_checksum=True) is False

    def test_soft_mode_accepts_broken_check_digit(self) -> None:
        """Мягкий режим смотрит только на длину и алфавит."""
        assert is_valid_vin("1HGCM82633A004353") is True

    def test_european_vin_passes_soft_and_fails_strict(self) -> None:
        """Ключевое решение: европейские VIN контрольную цифру не соблюдают."""
        assert is_valid_vin(EUROPEAN_VIN) is True
        assert is_valid_vin(EUROPEAN_VIN, strict_checksum=True) is False

    @pytest.mark.parametrize("strict", [False, True])
    def test_garbage_is_rejected_in_both_modes(self, strict: bool) -> None:
        assert not is_valid_vin("не VIN", strict)
