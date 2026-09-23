"""Сервис сбора: контракт источника, оркестрация опроса и извлечение фактов.

Каркас, а не боевой сбор. Адаптеров реальных площадок здесь нет и не должно быть,
пока не закрыт правовой вопрос доступа (§8.4): конкретный адаптер пишется под
готовый контракт `SourceAdapter`, когда условия доступа определят.
"""

from __future__ import annotations

from avtoklik.collector.base import (
    DEFAULT_SOURCE_TIMEOUT_SECONDS,
    SourceAdapter,
    SourceQuery,
    SourceResult,
    SourceStatus,
)
from avtoklik.collector.extraction import (
    ExtractedFacts,
    extract_censoring_markers,
    extract_cost,
    extract_facts,
    extract_mileage,
)
from avtoklik.collector.orchestrator import (
    HARD_DEADLINE_SECONDS,
    SOFT_DEADLINE_SECONDS,
    CacheEntry,
    CacheProtocol,
    CheckRun,
    CheckStatus,
    InMemoryCache,
    SourceState,
    run_check,
)
from avtoklik.collector.progress import (
    HEARTBEAT_INTERVAL_SECONDS,
    format_sse,
    heartbeat_comment,
    render_progress_event,
)

__all__ = [
    "DEFAULT_SOURCE_TIMEOUT_SECONDS",
    "HARD_DEADLINE_SECONDS",
    "HEARTBEAT_INTERVAL_SECONDS",
    "SOFT_DEADLINE_SECONDS",
    "CacheEntry",
    "CacheProtocol",
    "CheckRun",
    "CheckStatus",
    "ExtractedFacts",
    "InMemoryCache",
    "SourceAdapter",
    "SourceQuery",
    "SourceResult",
    "SourceState",
    "SourceStatus",
    "extract_censoring_markers",
    "extract_cost",
    "extract_facts",
    "extract_mileage",
    "format_sse",
    "heartbeat_comment",
    "render_progress_event",
    "run_check",
]
