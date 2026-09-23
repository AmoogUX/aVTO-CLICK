"""Извлечение фактов из текста отзыва (ТЗ, раздел 5.6.2, шаг 3).

Жёсткая граница, которую нельзя размывать: **числа берутся регулярками из
исходного текста**. LLM здесь допустима только для классификации узла и симптома —
она размечает существующий текст, а не придумывает значения. Галлюцинированная
цена ремонта — такая же дезинформация, как галлюцинированная норма толщиномера.

Отдельно (требование 5.6.4) извлекается признак **цензурированного наблюдения**:
«пробег 150 тысяч, рейка не стучала» — это правоцензурированное наблюдение, и для
модели дожития оно так же ценно, как отчёт об отказе. Если учитывать только отказы,
модель решит, что ломается всё и у всех.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "ExtractedFacts",
    "extract_censoring_markers",
    "extract_cost",
    "extract_facts",
    "extract_mileage",
]

# --- строительные блоки регулярок -------------------------------------------------

#: Пробел, в том числе неразрывный: «92 000 км» владельцы пишут и тем, и другим.
_SP = r"[ \t ]"
#: Число с необязательной разрядной группировкой: `92000`, `92 000`, `1 250 000`.
_NUM = rf"\d{{1,3}}(?:{_SP}\d{{3}})+|\d+"
#: Денежная единица во всех встречающихся написаниях. `р` без точки принимается
#: только если за ним не идёт буква, иначе «2 раза» превратится в «2 рубля».
_CUR = r"(?:руб\.?|рубл\w*|₽|р\.|р(?![а-яёa-z]))"
#: Множитель «тысяч». Для денег и пробега различается: см. комментарии ниже.
_K_WORD = r"(?:тыс\.?|тысяч[аи]?)"

#: Пробег «в тысячах»: «92 тыс», «92 тыс. км», «92 т.км», «92т км», «150 тысяч».
#: Голое `т` требует «км» после себя — иначе «15 тр» (пятнадцать тысяч рублей)
#: было бы прочитано как пробег. Хвостовой lookahead страхует от «92 тыс. руб»,
#: причём точку он проверяет сам: без этого движок отступил бы на «тыс» без точки
#: и обошёл бы запрет.
_MILEAGE_K_RE = re.compile(
    rf"(?<![\d.,])(?P<num>{_NUM}){_SP}*"
    rf"(?:{_K_WORD}{_SP}*(?:км\.?)?|т\.?{_SP}*км\.?)"
    rf"(?!\.?{_SP}*(?:{_CUR}|тр\b))",
    re.IGNORECASE,
)

#: Пробег «как есть»: «92000 км», «92 000 км». Требуется либо разрядная
#: группировка, либо 4+ цифр: «92 км» — это не пробег, а шум.
_MILEAGE_PLAIN_RE = re.compile(
    rf"(?<![\d.,])(?P<num>\d{{1,3}}(?:{_SP}\d{{3}})+|\d{{4,7}}){_SP}*км\.?",
    re.IGNORECASE,
)

#: Хвост суммы: необязательные «тыс/т» + обязательная валюта.
#: Покрывает «15 тр», «15т.р.», «15 000 руб», «15 000 ₽», «12 т₽».
_MONEY_TAIL = rf"(?:(?P<k>{_K_WORD}|т\.?){_SP}*)?{_CUR}"

#: Диапазон: «12–18 т₽», «15 000 - 20 000 руб». Множитель и валюта общие для
#: обеих границ, поэтому диапазон разбирается раньше одиночных сумм.
_COST_RANGE_RE = re.compile(
    rf"(?<![\d.,])(?P<lo>{_NUM}){_SP}*[–—−-]{_SP}*(?P<hi>{_NUM}){_SP}*{_MONEY_TAIL}",
    re.IGNORECASE,
)
_COST_SINGLE_RE = re.compile(rf"(?<![\d.,])(?P<num>{_NUM}){_SP}*{_MONEY_TAIL}", re.IGNORECASE)

#: Явные свидетельства ОТСУТСТВИЯ поломки (5.6.4). Список управляемый: он растёт
#: по мере разбора корпуса, но никогда не превращается в «угадайку по тональности» —
#: цензурированное наблюдение должно быть заявлено владельцем явно.
_CENSORING_PATTERNS: tuple[str, ...] = (
    r"(?:ничего\s+)?не\s+лома(?:л|ло|лось|лась|лись)\w*",
    r"не\s+ломал\w*\s+ничего",
    r"проблем\s*(?:с\s+\w+\s*)?(?:никаких\s+)?не\s+было",
    r"без\s+проблем",
    r"пол[её]т\s+нормальн\w*",
    r"ни\s+разу\s+не\s+подвел\w*",
    r"ничего\s+не\s+мен[ья]л\w*",
    r"не\s+стуча(?:л|ла|ло|ли)\w*",
    r"нареканий\s+нет",
    r"без\s+нареканий",
    r"вс[её]\s+родное",
)
_CENSORING_RE = re.compile("|".join(_CENSORING_PATTERNS), re.IGNORECASE)

#: Границы правдоподобия. Верх — чтобы опечатка «920000 тыс. км» не попала в модель.
_MAX_MILEAGE_KM = 2_000_000
_MAX_COST_RUB = 5_000_000


def _to_int(raw: str) -> int:
    """Убрать разрядные пробелы: «92 000» → 92000."""
    return int(re.sub(r"[\s ]", "", raw))


def _scale_thousands(value: int) -> int:
    """Домножить на тысячу.

    Значение ≥ 1000 не домножается: «92 000 тыс. км» — это описка автора, а не
    92 миллиона. Лучше вернуть заниженное правдоподобное число, чем выброс,
    который перекосит оценку дожития.
    """
    return value * 1000 if value < 1000 else value


def _spans_overlap(span: tuple[int, int], taken: list[tuple[int, int]]) -> bool:
    """Пересекается ли совпадение с уже разобранным участком текста."""
    return any(span[0] < end and start < span[1] for start, end in taken)


def extract_mileage(text: str) -> list[int]:
    """Вытащить пробеги в километрах, в порядке появления в тексте.

    Понимает «92 тыс», «92000 км», «92 т.км», «92 тыс. км», «92т км», «92 000 км».
    Не путает с пробегом год («2017» без единицы измерения — не пробег) и деньги
    («92 000 руб» — не пробег).
    """
    found: list[tuple[int, int]] = []
    taken: list[tuple[int, int]] = []

    for match in _MILEAGE_K_RE.finditer(text):
        value = _scale_thousands(_to_int(match.group("num")))
        if 0 < value <= _MAX_MILEAGE_KM:
            found.append((match.start(), value))
            taken.append(match.span())

    for match in _MILEAGE_PLAIN_RE.finditer(text):
        if _spans_overlap(match.span(), taken):
            continue
        value = _to_int(match.group("num"))
        if 0 < value <= _MAX_MILEAGE_KM:
            found.append((match.start(), value))

    found.sort(key=lambda item: item[0])
    return [value for _, value in found]


def extract_cost(text: str) -> list[int]:
    """Вытащить суммы в рублях, в порядке появления в тексте.

    Понимает «15 тр», «15 000 руб», «15т.р.», «15 000 ₽», «12 т₽».
    Диапазон «12–18 т₽» даёт обе границы: вилка цены — это два числа, и сводить
    её к середине нельзя, на экране C2 показывается именно диапазон.
    """
    found: list[tuple[int, int]] = []
    taken: list[tuple[int, int]] = []

    for match in _COST_RANGE_RE.finditer(text):
        multiply = match.group("k") is not None
        for name in ("lo", "hi"):
            value = _to_int(match.group(name))
            if multiply:
                value = _scale_thousands(value)
            if 0 < value <= _MAX_COST_RUB:
                found.append((match.start(name), value))
        taken.append(match.span())

    for match in _COST_SINGLE_RE.finditer(text):
        if _spans_overlap(match.span(), taken):
            continue
        value = _to_int(match.group("num"))
        if match.group("k") is not None:
            value = _scale_thousands(value)
        if 0 < value <= _MAX_COST_RUB:
            found.append((match.start("num"), value))

    found.sort(key=lambda item: item[0])
    return [value for _, value in found]


def extract_censoring_markers(text: str) -> list[str]:
    """Найти явные свидетельства отсутствия поломки, как они написаны в тексте."""
    # Пробелы нормализуются: фраза может быть разорвана переносом строки,
    # а дальше она попадает в разметку и в глаза редактору на приёмке.
    return [re.sub(r"\s+", " ", match.group(0)) for match in _CENSORING_RE.finditer(text)]


@dataclass(frozen=True, slots=True)
class ExtractedFacts:
    """Факты одного документа корпуса.

    Кортеж `(узел, симптом, пробег, стоимость, исход)` из 5.6.2 здесь закрыт только
    в части чисел и исхода. Узел и симптом — задача классификатора по управляемому
    справочнику, отдельный шаг конвейера.
    """

    mileages_km: tuple[int, ...] = ()
    costs_rub: tuple[int, ...] = ()
    censoring_markers: tuple[str, ...] = ()

    @property
    def is_censored_observation(self) -> bool:
        """Правоцензурированное наблюдение: отказа не было при известном пробеге.

        Без пробега свидетельство бесполезно: «ничего не ломалось» без ответа
        «до какого пробега» ничего не говорит функции дожития (5.6.4).
        """
        return bool(self.censoring_markers) and bool(self.mileages_km)

    @property
    def max_mileage_km(self) -> int | None:
        """Наибольший упомянутый пробег — точка цензурирования наблюдения."""
        return max(self.mileages_km) if self.mileages_km else None


def extract_facts(text: str) -> ExtractedFacts:
    """Сводное извлечение: пробеги, стоимости и признак цензурированного наблюдения."""
    return ExtractedFacts(
        mileages_km=tuple(extract_mileage(text)),
        costs_rub=tuple(extract_cost(text)),
        censoring_markers=tuple(extract_censoring_markers(text)),
    )
