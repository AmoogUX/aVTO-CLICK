"""Учебный корпус отзывов для тестов извлечения (ТЗ, раздел 5.6.2).

Тексты сочинены вручную и лежат в репозитории специально: боевой сбор с площадок
и форумов не реализован, пока не закрыт правовой вопрос, а контракт извлечения
проверять надо уже сейчас. Когда появится настоящий корпус, он будет храниться
в S3 вместе с `source_url`, `fetched_at` и хэшем — чтобы переразметить его новой
моделью без повторного обхода.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["FIXTURES_DIR", "load_review", "load_reviews"]

FIXTURES_DIR = Path(__file__).parent


def load_reviews() -> dict[str, str]:
    """Прочитать весь корпус: имя файла без расширения → текст отзыва."""
    return {
        path.stem: path.read_text(encoding="utf-8") for path in sorted(FIXTURES_DIR.glob("*.txt"))
    }


def load_review(name: str) -> str:
    """Прочитать один отзыв по имени файла без расширения."""
    path = FIXTURES_DIR / f"{name}.txt"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")
