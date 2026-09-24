"""Общие фикстуры.

Черновики объявлений и опубликованные объявления живут в памяти процесса —
это временная замена таблицам. Общее хранилище означает, что тест, который
что-то опубликовал, изменит витрину для всех следующих. Сброс перед каждым
тестом делает порядок их запуска неважным.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from avtoklik.service.sell import reset_drafts
from avtoklik.service.showcase import reset_published


@pytest.fixture(autouse=True)
def _clean_stores() -> Iterator[None]:
    reset_drafts()
    reset_published()
    yield
    reset_drafts()
    reset_published()
