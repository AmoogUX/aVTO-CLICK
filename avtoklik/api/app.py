"""Сборка FastAPI-приложения «АвтоКлик» (ТЗ, раздел 7).

Приложение собирается фабрикой, а не создаётся на уровне модуля: тестам нужно
несколько независимых экземпляров с разными заглушками, а продакшену — одна
точка, куда передаются настоящие реализации. Модуль-синглтон не даёт ни того,
ни другого.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from avtoklik.api.deps import (
    CheckOrchestrator,
    IdempotencyStore,
    InMemoryIdempotencyStore,
    VehicleRepository,
)
from avtoklik.api.routes_check import router as checks_router
from avtoklik.api.schemas import PLATE_HINT, VIN_HINT, ErrorResponse

__all__ = ["API_PREFIX", "create_app"]

# Версия в пути, а не в заголовке (ТЗ, 7): ломающее изменение контракта живёт
# рядом со старым, и клиенты переезжают по одному.
API_PREFIX = "/api/v1"


def create_app(
    *,
    orchestrator: CheckOrchestrator | None = None,
    vehicle_repository: VehicleRepository | None = None,
    idempotency_store: IdempotencyStore | None = None,
    with_web: bool = True,
) -> FastAPI:
    """Создать приложение.

    Все зависимости — необязательные аргументы: так интеграция с оркестратором
    из :mod:`avtoklik.collector` сводится к одному вызову, а тесты подменяют
    их либо здесь, либо через ``app.dependency_overrides``.

    `with_web` подключает серверный веб-клиент (витрина и карточка). Он стоит
    на корне `/`, тогда как API живёт под `/api/v1`, поэтому одно приложение
    обслуживает и людей, и клиентов. Выключается там, где нужен голый API:
    например, если веб будет разворачиваться отдельным сервисом.
    """
    app = FastAPI(
        title="АвтоКлик API",
        version="1.0.0",
        description="Проверка, оценка и продажа автомобилей с пробегом",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
    )

    app.state.orchestrator = orchestrator
    app.state.vehicle_repository = vehicle_repository
    # Хранилище идемпотентности по умолчанию — в памяти: без него любой запуск
    # приложения «из коробки» молча терял бы гарантию повторного запроса.
    app.state.idempotency_store = idempotency_store or InMemoryIdempotencyStore()

    app.include_router(checks_router, prefix=API_PREFIX)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)

    if with_web:
        # Импорт внутри функции разрывает цикл: веб-клиент строит свои
        # представления на схемах API, а API подключает веб-клиент. Тащить
        # ради этого схемы в третий модуль смысла нет — зависимость
        # односторонняя по сути и кольцевая только по времени импорта.
        from avtoklik.web.routes import mount_web

        mount_web(app)

    @app.get("/health", tags=["service"], summary="Проверка живости")
    async def health() -> dict[str, str]:
        """Лайвнес-проба: отвечает, пока процесс способен обслуживать запросы.

        Намеренно не ходит в БД и не опрашивает источники: проба, падающая
        из-за недоступного Дрома, снимет из балансировщика здоровые поды —
        ровно наоборот тому, что нужно по CC2.
        """
        return {"status": "ok", "version": app.version}

    return app


_FIELD_HINTS = {"plate": ("invalid_plate", PLATE_HINT), "vin": ("invalid_vin", VIN_HINT)}


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Привести ошибку валидации к формату ошибок API (ТЗ, 7.1).

    По умолчанию FastAPI отдаёт массив `detail` с внутренними путями к полям —
    клиенту от него пользы нет. Отдаём машинный `code` (по нему UI выбирает
    подсказку) и человеческий текст: CC5 требует не «validation error», а
    «Допустимы А В Е К М Н О Р С Т У Х».
    """
    issues: list[dict[str, str]] = []
    code = "validation_failed"
    hint: str | None = None
    errors: Sequence[Any] = exc.errors() if isinstance(exc, RequestValidationError) else []
    for error in errors:
        location = [str(part) for part in error.get("loc", ()) if part not in ("body", "query")]
        field = ".".join(location) or "body"
        issues.append({"field": field, "message": str(error.get("msg", ""))})
        for name, (field_code, field_hint) in _FIELD_HINTS.items():
            if name in location and code == "validation_failed":
                code, hint = field_code, field_hint

    payload = ErrorResponse(
        code=code,
        detail=issues[0]["message"] if issues else "Некорректный запрос",
        hint=hint,
        issues=issues,
    )
    return JSONResponse(
        # Код задан числом: имена констант starlette для 422 сейчас переименовывают.
        status_code=422,
        content=payload.model_dump(exclude_none=True),
    )
