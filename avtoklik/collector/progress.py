"""Контракт стриминга прогресса проверки (ТЗ, раздел 7.3).

Транспорт — SSE, а не WebSocket: поток строго односторонний, живёт поверх обычного
HTTP/2, проходит через корпоративные прокси и умеет реконнект по `Last-Event-ID`.

Два решения из контракта, которые легко нарушить и трудно потом починить:

* **в каждом кадре — полный список источников, а не дельта.** Кадр больше, зато
  клиент не склеивает состояние, а реконнект не требует синхронизации. На шести
  элементах экономить нечего;
* **`title` источника и `progress` приходят с сервера.** Клиент только отрисовывает:
  иначе три клиента посчитают «64 %» по-разному, а новый источник потребует релиза
  приложения.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from avtoklik.collector.base import SourceStatus
from avtoklik.collector.orchestrator import CheckRun, SourceState

__all__ = [
    "HEARTBEAT_INTERVAL_SECONDS",
    "format_sse",
    "heartbeat_comment",
    "render_progress_event",
]

#: Реже нельзя: прокси рвут «молчащее» соединение.
HEARTBEAT_INTERVAL_SECONDS = 15.0

#: Внутренний статус → значение `state` в контракте, отображаемое один-в-один
#: на подписи экрана B2. Отдельного `late` в протоколе нет: для клиента
#: «опоздавший» источник по-прежнему «ИЩЕМ…», просто вердикт уже отдан.
_WIRE_STATE: dict[SourceStatus, str] = {
    SourceStatus.QUEUED: "queued",
    SourceStatus.RUNNING: "running",
    SourceStatus.DONE: "ok",
    SourceStatus.CACHED: "from_cache",
    SourceStatus.FAILED: "error",
}


def _iso(timestamp: float) -> str:
    """Метка времени в ISO-8601 UTC — в таком виде её ждёт клиент."""
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def _render_source(state: SourceState) -> dict[str, Any]:
    """Одна строка экрана B2 в кадре прогресса."""
    wire = _WIRE_STATE[state.status]
    if state.status is SourceStatus.FAILED and state.error == "timeout":
        # Контракт различает `timeout` и `error`: подпись одна, а в метриках и
        # в тексте плашки CC2 это разные причины.
        wire = "timeout"

    frame: dict[str, Any] = {
        "code": state.source_id,
        "title": state.title,  # приходит с сервера (§7.3)
        "state": wire,
        "order": state.order,
        "required": state.is_required,
        "late": state.is_late,
    }
    if state.cache_age_seconds is not None:
        frame["cache_age_s"] = round(state.cache_age_seconds, 3)
    if state.fetched_at is not None:
        frame["cached_at" if state.status is SourceStatus.CACHED else "fetched_at"] = _iso(
            state.fetched_at
        )
    if state.error is not None:
        frame["reason"] = state.error
    return frame


def render_progress_event(run: CheckRun) -> dict[str, Any]:
    """Кадр `progress`: состояние проверки целиком.

    Полный список источников в каждом кадре — требование контракта, а не
    расточительность: при реконнекте клиент просто заменяет своё состояние этим.
    """
    return {
        "status": run.status.value,
        "progress": round(run.progress, 4),  # считает сервер
        "elapsed_ms": run.elapsed_ms(),
        "verdict_ready": run.verdict_ready,
        "sources": [_render_source(state) for state in run.sources],
    }


def format_sse(event: str, data: Mapping[str, Any], event_id: int | None = None) -> str:
    """Собрать кадр SSE: `event:` / `id:` / `data:` и пустая строка-разделитель.

    `id` нужен для `Last-Event-ID`: пользователь нырнул в лифт на пять секунд —
    браузер переподключится и доберёт пропущенное сам.
    """
    lines = [f"event: {event}"]
    if event_id is not None:
        lines.append(f"id: {event_id}")
    # `ensure_ascii=False` — данные русские, экранирование кириллицы раздувает кадр
    # втрое и мешает читать поток курлом. Переводы строк в JSON исключены по формату.
    lines.append(f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"


def heartbeat_comment() -> str:
    """Комментарий-пульс: клиент его игнорирует, прокси видит живое соединение."""
    return ": keepalive\n\n"
