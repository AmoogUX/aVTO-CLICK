"""Нормализация и валидация российского государственного регистрационного знака.

Модуль превращает то, что пользователь набрал в компоненте ``PlateInput``
(экраны B1, E1, CC5 дизайн-системы), в канонический ключ, по которому ищутся
объявления и склеиваются кластеры (уровень 1 каскада дедупликации, §5.2
технической документации).

**Почему здесь так много «починки» ввода.** Продуктовое требование CC5 (§1.6 и
§4.6 дизайн-системы): типичные опечатки исправляются молча, а не выдаются
пользователю как ошибка валидации. Исправляем два класса ошибок:

* **латинская раскладка** — 12 букв российского знака подобраны так, что каждая
  визуально совпадает с латинской (``A→А``, ``B→В``, … ``X→Х``). Пользователь с
  английской раскладкой физически не видит разницы, значит ругаться не на что;
* **«О» вместо «0» и «0» вместо «О»** — знак набирается моноширинным шрифтом
  (``JetBrains Mono``), где эти символы почти неразличимы. Позиция в маске
  однозначно говорит, что имелось в виду, поэтому чинить безопасно.

Ошибкой остаётся только то, что починить нельзя: буквы вне алфавита (Ж, Ц, Д…),
неверная структура и недопустимый код региона.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "PLATE_LETTERS",
    "ParsedPlate",
    "PlateKind",
    "PlateParseError",
    "explain_plate_error",
    "is_valid_plate",
    "normalize_plate",
    "parse_plate",
]

PLATE_LETTERS = "АВЕКМНОРСТУХ"
"""Алфавит российского знака — строго 12 кириллических букв.

Они и только они внесены в ГОСТ именно потому, что совпадают по начертанию с
латиницей; подсказка CC5 перечисляет их пользователю дословно.
"""

_DIGITS = "0123456789"

_LATIN_TO_CYRILLIC = {
    "A": "А",
    "B": "В",
    "E": "Е",
    "K": "К",
    "M": "М",
    "H": "Н",
    "O": "О",
    "P": "Р",
    "C": "С",
    "T": "Т",
    "Y": "У",
    "X": "Х",
}
"""Визуальные близнецы: латинская буква → кириллическая буква знака."""

_THREE_DIGIT_REGION_PREFIXES = frozenset("179")
"""Трёхзначный код региона начинается с 1, 7 или 9 (см. §4.6 дизайн-системы).

Допущение зафиксировано в дизайн-системе как требующее подтверждения бэкендом,
поэтому вынесено в отдельную константу, а не зашито в регулярку.
"""

# Всё, что не буква и не цифра (пробелы, дефисы, точки, скобки из вставки
# из буфера — «к 999 кк / 799»), для нормализации значения не имеет.
_NOISE_RE = re.compile(r"[^0-9A-Za-zА-Яа-яЁё]+")


class PlateKind(Enum):
    """Тип регистрационного знака."""

    PASSENGER = "passenger"
    """Легковой: буква, три цифры, две буквы, регион. Основной для продукта."""

    TAXI = "taxi"
    """Такси и общественный транспорт: две буквы, три цифры, регион."""

    TRAILER = "trailer"
    """Прицеп: две буквы, четыре цифры, регион."""


class PlateParseError(ValueError):
    """Госномер не удалось разобрать.

    Сообщение исключения — уже человеческое, пригодное для показа в CC5:
    ошибка объясняет, что случилось и что делать (§1.6 дизайн-системы).
    """


@dataclass(frozen=True, slots=True)
class ParsedPlate:
    """Разобранный госномер."""

    text: str
    """Канон: верхний регистр, кириллица, без пробелов, например ``К999КК799``."""

    kind: PlateKind
    """Тип знака."""

    region: str
    """Код региона, 2 или 3 цифры."""


@dataclass(frozen=True, slots=True)
class _PlateFormat:
    """Маска знака без региона: ``L`` — буква, ``D`` — цифра."""

    kind: PlateKind
    mask: str


# Порядок важен: при неоднозначности (одинаковая длина после починки опечаток)
# выигрывает более частый формат, а легковой знак — основной для продукта.
_FORMATS = (
    _PlateFormat(PlateKind.PASSENGER, "LDDDLL"),
    _PlateFormat(PlateKind.TAXI, "LLDDD"),
    _PlateFormat(PlateKind.TRAILER, "LLDDDD"),
)


def _compact(raw: str) -> str:
    """Убирает разделители, поднимает регистр и переводит латиницу в кириллицу."""
    cleaned = _NOISE_RE.sub("", raw).upper()
    return "".join(_LATIN_TO_CYRILLIC.get(ch, ch) for ch in cleaned)


def _is_valid_region(region: str) -> bool:
    """Проверяет код региона: 2 или 3 цифры, трёхзначный начинается с 1, 7 или 9."""
    if not region.isdigit():
        return False
    if len(region) == 2:
        # «00» не выдавался никогда, а вот 01…99 — реальные коды.
        return region != "00"
    if len(region) == 3:
        return region[0] in _THREE_DIGIT_REGION_PREFIXES
    return False


def _fit(
    compact: str, fmt: _PlateFormat, *, repair: bool, check_region: bool
) -> ParsedPlate | None:
    """Пробует уложить строку в маску формата.

    Args:
        compact: строка уже без разделителей и в кириллице.
        fmt: проверяемый формат.
        repair: чинить ли опечатки «О»↔«0» по позиции в маске.
        check_region: проверять ли код региона (выключается при диагностике,
            чтобы отличить «сломана структура» от «сломан регион»).

    Returns:
        Разобранный знак либо ``None``, если строка формату не соответствует.
    """
    body_len = len(fmt.mask)
    region = compact[body_len:]
    if not 2 <= len(region) <= 3:
        return None

    body_chars: list[str] = []
    for ch, slot in zip(compact[:body_len], fmt.mask, strict=True):
        if slot == "L":
            # «0» в позиции буквы — всегда опечатка вместо «О».
            fixed = "О" if repair and ch == "0" else ch
            if fixed not in PLATE_LETTERS:
                return None
        else:
            # ...и симметрично: «О» в цифровой позиции — всегда ноль.
            fixed = "0" if repair and ch == "О" else ch
            if fixed not in _DIGITS:
                return None
        body_chars.append(fixed)

    region_chars: list[str] = []
    for ch in region:
        fixed = "0" if repair and ch == "О" else ch
        if fixed not in _DIGITS:
            return None
        region_chars.append(fixed)
    fixed_region = "".join(region_chars)

    if check_region and not _is_valid_region(fixed_region):
        return None
    return ParsedPlate("".join(body_chars) + fixed_region, fmt.kind, fixed_region)


def _try_formats(compact: str, *, repair: bool, check_region: bool) -> ParsedPlate | None:
    """Прогоняет строку по всем форматам в порядке приоритета."""
    for fmt in _FORMATS:
        parsed = _fit(compact, fmt, repair=repair, check_region=check_region)
        if parsed is not None:
            return parsed
    return None


def _parse_compact(compact: str) -> ParsedPlate | None:
    """Разбирает уже очищенную строку: сначала «как есть», потом с починкой.

    Два прохода нужны, чтобы починка «О»↔«0» не перетащила корректно набранный
    номер в чужой формат: если строка укладывается в маску без правок — она и
    победит.
    """
    exact = _try_formats(compact, repair=False, check_region=True)
    if exact is not None:
        return exact
    return _try_formats(compact, repair=True, check_region=True)


def normalize_plate(raw: str) -> str | None:
    """Приводит госномер к канону.

    Канон — верхний регистр, кириллица, без пробелов и разделителей, регион
    слитно: ``«к 999 кк 799»``, ``«K999KK799»`` и ``«К999КК799»`` дают одну и ту
    же строку ``К999КК799``. Именно она используется как ключ уровня 1 каскада
    дедупликации.

    Args:
        raw: то, что ввёл или вставил пользователь.

    Returns:
        Канонический номер либо ``None``, если разобрать не удалось
        (объяснение для пользователя даёт :func:`explain_plate_error`).
    """
    parsed = _parse_compact(_compact(raw))
    return None if parsed is None else parsed.text


def parse_plate(raw: str) -> ParsedPlate:
    """Разбирает госномер, бросая исключение вместо ``None``.

    Args:
        raw: то, что ввёл или вставил пользователь.

    Returns:
        Разобранный знак с типом и кодом региона.

    Raises:
        PlateParseError: с готовым человеческим текстом для CC5.
    """
    parsed = _parse_compact(_compact(raw))
    if parsed is None:
        raise PlateParseError(explain_plate_error(raw))
    return parsed


def is_valid_plate(raw: str) -> bool:
    """Отвечает, распознаётся ли строка как российский госномер."""
    return normalize_plate(raw) is not None


_HINT_ALPHABET = "Буквы только А В Е К М Н О Р С Т У Х · регион 2–3 цифры."
_HINT_TYPO = "Частая опечатка: О вместо 0."


def explain_plate_error(raw: str) -> str:
    """Объясняет человеку, что не так с номером — текст для состояния ``error`` (CC5).

    Формат сообщения следует правилу §1.6 дизайн-системы «что случилось · почему ·
    что сделать»: ошибка не извиняется и не пугает, а перечисляет алфавит и
    правило региона, потому что именно их пользователь и нарушает.

    Args:
        raw: исходный ввод.

    Returns:
        Готовую строку для показа под плашкой. Если номер корректен, возвращает
        подтверждение — вызывающему коду не нужно отдельно проверять валидность.
    """
    compact = _compact(raw)
    if not compact:
        return "Введите госномер: буква, три цифры, две буквы и код региона — например, А001АК26."

    if _parse_compact(compact) is not None:
        return "Номер распознан."

    # Буквы вне алфавита — единственная ошибка, которую нельзя починить молча.
    bad_letters = sorted({ch for ch in compact if ch not in PLATE_LETTERS and ch not in _DIGITS})
    if bad_letters:
        listed = ", ".join(bad_letters)
        return (
            f"Такой номер не находится в базах: букв {listed} на российских знаках не бывает. "
            f"{_HINT_ALPHABET} {_HINT_TYPO}"
        )

    # Структура сошлась, но регион не подходит — говорим именно про регион.
    if _try_formats(compact, repair=True, check_region=False) is not None:
        return (
            "Такой номер не находится в базах: код региона — 2 или 3 цифры, "
            "трёхзначный начинается с 1, 7 или 9 (77, 178, 750). "
            f"{_HINT_ALPHABET}"
        )

    return (
        "Такой номер не находится в базах: не сходится структура — нужны буква, "
        "три цифры, две буквы и регион из 2–3 цифр (например, А001АК26). "
        f"{_HINT_TYPO}"
    )
