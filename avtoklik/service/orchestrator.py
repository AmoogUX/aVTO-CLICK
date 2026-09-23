"""Рабочий оркестратор проверки: от госномера до вердикта.

Здесь сходятся четыре модуля, каждый из которых до сих пор жил отдельно:

* :mod:`avtoklik.collector` опрашивает источники и отдаёт состояние прогона;
* :mod:`avtoklik.matching` склеивает объявления одного автомобиля с разных
  площадок;
* :mod:`avtoklik.knowledge` считает ожидаемые затраты на ремонт под пробег;
* :mod:`avtoklik.service.verdict` собирает из всего этого экран B3.

Класс :class:`CheckService` реализует протокол
:class:`avtoklik.api.deps.CheckOrchestrator`, поэтому передаётся прямо в
``create_app`` — слой API при этом не меняется ни на строку.

Состояние живёт в памяти. Целевая схема (5.1) хранит прогоны в Postgres, и
это осознанный следующий шаг: подключение хранилища меняет то, где лежит
``_CheckEntry``, но не логику сборки вердикта.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time

from avtoklik.api.deps import SseEvent
from avtoklik.api.schemas import (
    CheckStatus as ApiCheckStatus,
)
from avtoklik.api.schemas import (
    CheckStatusResponse,
    CheckVerdict,
    DegradedSource,
    RepairForecastOut,
    StartedCheck,
    Subject,
    VehicleBrief,
)
from avtoklik.api.schemas import (
    SourceState as ApiSourceState,
)
from avtoklik.api.schemas import (
    SourceStatus as ApiSourceStatus,
)
from avtoklik.collector.base import SourceAdapter, SourceQuery, SourceStatus
from avtoklik.collector.orchestrator import CacheProtocol, CheckRun, CheckStatus, run_check
from avtoklik.knowledge import DEFAULT_HORIZON_KM, expected_repair_cost
from avtoklik.matching.dedup import ListingFeatures, cluster_listings, similarity_score
from avtoklik.matching.plate import normalize_plate
from avtoklik.matching.vin import normalize_vin
from avtoklik.service.defects import (
    RIO_III_GENERATION_ID,
    defects_for_model,
    remedies_for_defect,
)
from avtoklik.service.payloads import ListingPayload, RegistryPayload
from avtoklik.service.sources import default_sources
from avtoklik.service.verdict import build_verdict_bundle

__all__ = ["CheckService"]

#: Соответствие статусов прогона сбора статусам ответа API. Два перечисления
#: существуют отдельно не по недосмотру: внутренние состояния сбора — наше дело,
#: а контракт API менять дороже.
_STATUS_MAP: dict[CheckStatus, ApiCheckStatus] = {
    CheckStatus.RUNNING: ApiCheckStatus.RUNNING,
    CheckStatus.DONE: ApiCheckStatus.DONE,
    CheckStatus.PARTIAL: ApiCheckStatus.PARTIAL,
    CheckStatus.MISSING_REQUIRED: ApiCheckStatus.FAILED,
}


@dataclass(slots=True)
class _CheckEntry:
    """Одна проверка в памяти сервиса."""

    check_id: str
    subject: Subject
    run: CheckRun | None = None
    verdict: CheckVerdict | None = None
    events: list[SseEvent] = field(default_factory=list)
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    cancelled: bool = False


def _listing_features(listing: ListingPayload) -> ListingFeatures:
    """Перевести объявление в признаки для дедупликации.

    Живёт здесь, а не в пакете источников: решение о том, что считать
    признаком склейки, принадлежит сборке проверки, а не адаптеру площадки.
    """
    return ListingFeatures(
        listing_id=listing.listing_id,
        vin=normalize_vin(listing.vin) if listing.vin else None,
        plate=normalize_plate(listing.plate) if listing.plate else None,
        phone_hash=listing.phone_hash,
        photo_phashes=list(listing.photo_phashes),
        model_id=listing.model_id or "",
        year=listing.year or 0,
        mileage_km=listing.mileage_km or 0,
        region_id=str(listing.region_id) if listing.region_id is not None else "",
        price=listing.price,
        source_id=listing.platform,
        status="active" if listing.is_active else "archived",
        last_price_change_at=(
            datetime.combine(listing.price_changed_on, time.min)
            if listing.price_changed_on is not None
            else None
        ),
    )


def _collect_listings(run: CheckRun) -> list[ListingPayload]:
    """Достать объявления из нагрузок всех источников.

    Источники отдают объявление по-разному: кто-то напрямую, кто-то в
    контейнере рядом с отзывами, кто-то списком. Разбор формы — здесь, чтобы
    ни адаптеру, ни сборке вердикта не приходилось этим заниматься.
    """
    found: list[ListingPayload] = []
    for state in run.sources:
        payload = state.payload
        if payload is None:
            continue
        candidates = payload if isinstance(payload, list | tuple) else [payload]
        for item in candidates:
            if isinstance(item, ListingPayload):
                found.append(item)
            else:
                nested = getattr(item, "listing", None)
                if isinstance(nested, ListingPayload):
                    found.append(nested)
    return found


def _registry_of(run: CheckRun) -> RegistryPayload | None:
    """Собрать регистрационные данные, дополнив их VIN из Номерограма.

    ГИБДД по госномеру VIN не отдаёт — его закрывает Номерограм (5.6, §6).
    Поэтому регистрационная запись склеивается из двух источников, и это
    именно то место, где принимается решение «кому верить»: базовые факты
    берём у обязательного источника, а VIN — у того, кто его знает.
    """
    base: RegistryPayload | None = None
    vin: str | None = None
    for state in run.sources:
        payload = state.payload
        candidate = (
            payload if isinstance(payload, RegistryPayload) else getattr(payload, "registry", None)
        )
        if not isinstance(candidate, RegistryPayload):
            continue
        if state.is_required and base is None:
            base = candidate
        if vin is None and candidate.vin:
            vin = candidate.vin
        if base is None:
            base = candidate
    if base is None:
        return None
    if base.vin is None and vin is not None:
        return RegistryPayload(
            vin=vin,
            brand=base.brand,
            model=base.model,
            year=base.year,
            engine=base.engine,
            owners_count=base.owners_count,
            mileage_history=base.mileage_history,
            accidents=base.accidents,
            is_pledged=base.is_pledged,
            is_taxi=base.is_taxi,
            has_restrictions=base.has_restrictions,
        )
    return base


def _current_mileage(registry: RegistryPayload, listings: Sequence[ListingPayload]) -> int | None:
    """Текущий пробег: максимум из истории и объявлений.

    Максимум, а не последнее значение: одометр не уменьшается, и расхождение
    источников безопаснее трактовать в большую сторону — заниженный пробег
    дал бы заниженный прогноз ремонта, то есть обманул бы покупателя в
    сторону оптимизма.
    """
    values = [r.mileage_km for r in registry.mileage_history]
    values.extend(listing.mileage_km for listing in listings if listing.mileage_km is not None)
    return max(values) if values else None


def _repair_forecast(
    registry: RegistryPayload, listings: Sequence[ListingPayload]
) -> RepairForecastOut | None:
    """Прогноз затрат на ремонт под пробег этого автомобиля (5.6.4)."""
    mileage = _current_mileage(registry, listings)
    if mileage is None:
        return None
    # Пока каталог болячек существует только для эталонного поколения: сборка
    # генерации по марке и модели — задача каталога комплектаций (§6).
    generation_id = RIO_III_GENERATION_ID if registry.model.startswith("Rio") else -1
    defects = defects_for_model(generation_id)
    if not defects:
        # Штатный случай: по редкой модели данных нет. Блок не рисуется —
        # показывать нули было бы хуже, чем не показывать ничего.
        return None
    remedies = {defect.id: remedies_for_defect(defect.id) for defect in defects}
    horizon_km = int(DEFAULT_HORIZON_KM)
    forecast = expected_repair_cost(mileage, defects, remedies, horizon_km=horizon_km)
    calibrated = forecast.prevalence_calibrated
    headline = (
        f"Ремонт на ближайшие {horizon_km // 1000} т. км"
        if calibrated
        else f"Ремонт на ближайшие {horizon_km // 1000} т. км — оценка сверху"
    )
    lines = [
        f"{risk.defect.title} — {round(risk.contribution):,} ₽".replace(",", " ")
        for risk in forecast.top_contributors[:3]
    ]
    if not calibrated:
        lines.append("Встречаемость болячек ещё не откалибрована, поэтому доли не показываем")
    return RepairForecastOut(
        amount_rub=round(forecast.expected),
        low_rub=round(forecast.low),
        high_rub=round(forecast.high),
        horizon_km=horizon_km,
        calibrated=calibrated,
        headline=headline,
        lines=lines,
    )


def _degraded(run: CheckRun) -> list[DegradedSource]:
    """Источники, отдавшие кэш или не ответившие вовсе (CC2)."""
    result: list[DegradedSource] = []
    for state in run.sources:
        if state.status is SourceStatus.CACHED:
            result.append(
                DegradedSource(
                    source=state.title,
                    reason="cached",
                    message="показываем сохранённые данные",
                )
            )
        elif state.status is SourceStatus.FAILED:
            result.append(
                DegradedSource(
                    source=state.title,
                    reason=state.error or "unavailable",
                    message="источник не ответил",
                )
            )
    return result


def _api_sources(run: CheckRun) -> list[ApiSourceStatus]:
    """Строки экрана B2 в форме контракта API."""
    mapping = {
        SourceStatus.QUEUED: ApiSourceState.QUEUED,
        SourceStatus.RUNNING: ApiSourceState.RUNNING,
        SourceStatus.DONE: ApiSourceState.OK,
        SourceStatus.CACHED: ApiSourceState.FROM_CACHE,
        SourceStatus.FAILED: ApiSourceState.ERROR,
    }
    return [
        ApiSourceStatus(
            code=state.source_id,
            title=state.title,
            state=mapping[state.status],
        )
        for state in sorted(run.sources, key=lambda s: s.order)
    ]


class CheckService:
    """Оркестратор проверки, реализующий протокол API.

    Инвариант, унаследованный из 5.1: деградация источника — это состояние,
    а не исключение. Ни один метод не поднимает ошибку «источник упал»;
    вместо этого проверка завершается с неполным покрытием, и вердикт честно
    сообщает, сколько источников ответило.
    """

    def __init__(
        self,
        adapters: Sequence[SourceAdapter] | None = None,
        cache: CacheProtocol | None = None,
        deadline_seconds: float = 40.0,
        today: date | None = None,
    ) -> None:
        self._adapters = list(adapters) if adapters is not None else default_sources()
        self._cache = cache
        self._deadline = deadline_seconds
        self._today = today
        self._checks: dict[str, _CheckEntry] = {}

    async def start_check(self, subject: Subject) -> StartedCheck:
        """Поставить проверку в работу и сразу вернуть идентификатор."""
        check_id = str(uuid.uuid4())
        entry = _CheckEntry(check_id=check_id, subject=subject)
        self._checks[check_id] = entry
        entry.task = asyncio.create_task(self._run(entry))
        return StartedCheck(check_id=check_id, eta_sec=int(self._deadline))

    async def _run(self, entry: _CheckEntry) -> None:
        """Опросить источники и собрать вердикт."""
        value = entry.subject.value
        query = SourceQuery(
            key=value,
            plate=value if entry.subject.type == "plate" else None,
            vin=value if entry.subject.type == "vin" else None,
        )
        run = await run_check(
            self._adapters, query, deadline_seconds=self._deadline, cache=self._cache
        )
        entry.run = run
        if run.late_completion is not None:
            # Опоздавшие дописываются в фоне: пользователь уже получил
            # частичный результат, но проверка не считается законченной,
            # пока они не вернутся (5.1).
            run = await run.late_completion
            entry.run = run
        entry.verdict = self._build_verdict(run)
        entry.finished.set()

    def _build_verdict(self, run: CheckRun) -> CheckVerdict | None:
        """Собрать вердикт из состояния прогона."""
        registry = _registry_of(run)
        if registry is None or not run.verdict_ready:
            # Без обязательного источника вердикта не бывает — это не ошибка
            # сборки, а правило продукта (5.1).
            return None
        listings = _collect_listings(run)
        cluster, reasons = self._cluster(listings)
        bundle = build_verdict_bundle(
            registry,
            sources_ok=sum(1 for s in run.sources if s.is_usable),
            sources_total=len(run.sources),
            listings=cluster,
            match_reasons=reasons,
            cluster_id=run.run_id,
            degraded=_degraded(run),
            today=self._today,
        )
        verdict = bundle.verdict
        forecast = _repair_forecast(registry, cluster)
        if forecast is not None:
            verdict = verdict.model_copy(update={"repair": forecast})
        return verdict

    @staticmethod
    def _cluster(listings: Sequence[ListingPayload]) -> tuple[list[ListingPayload], list[str]]:
        """Выбрать кластер объявлений одного автомобиля и признаки склейки."""
        if not listings:
            return [], []
        features = [_listing_features(item) for item in listings]
        by_id = {item.listing_id: item for item in listings}
        clusters = cluster_listings(features)
        # Нас интересует самый крупный кластер: это и есть «одно и то же авто
        # на трёх площадках». Одиночки — другие автомобили, попавшие в выдачу.
        biggest = max(clusters, key=len)
        chosen = [by_id[f.listing_id] for f in biggest if f.listing_id in by_id]
        reasons: set[str] = set()
        for left in range(len(biggest)):
            for right in range(left + 1, len(biggest)):
                reasons.update(similarity_score(biggest[left], biggest[right]).reasons)
        return chosen, sorted(reasons)

    async def get_check(self, check_id: str) -> CheckStatusResponse | None:
        """Текущее состояние проверки."""
        entry = self._checks.get(check_id)
        if entry is None:
            return None
        run = entry.run
        if run is None:
            return CheckStatusResponse(check_id=check_id, status=ApiCheckStatus.QUEUED)
        return CheckStatusResponse(
            check_id=check_id,
            status=_STATUS_MAP[run.status],
            progress=run.progress,
            elapsed_ms=run.elapsed_ms(),
            vehicle=self._vehicle_brief(run),
            sources=_api_sources(run),
            verdict=entry.verdict,
        )

    @staticmethod
    def _vehicle_brief(run: CheckRun) -> VehicleBrief | None:
        """Шапка экрана: марка, модель, год."""
        registry = _registry_of(run)
        if registry is None or not registry.brand:
            return None
        return VehicleBrief(
            title=f"{registry.brand} {registry.model}".strip(),
            year=registry.year,
            vin=registry.vin,
        )

    async def get_verdict(self, check_id: str) -> CheckVerdict | None:
        """Готовый вердикт; `None` — проверки нет или вердикт ещё не собран."""
        entry = self._checks.get(check_id)
        return entry.verdict if entry is not None else None

    async def stream(
        self, check_id: str, last_event_id: int | None = None
    ) -> AsyncIterator[SseEvent]:
        """События проверки. Поток обязан завершаться, иначе соединение висит."""
        entry = self._checks.get(check_id)
        if entry is None:
            return
        await entry.finished.wait()
        run = entry.run
        if run is None:
            return
        yield SseEvent(
            event="progress",
            data={
                "check_id": check_id,
                "progress": run.progress,
                "elapsed_ms": run.elapsed_ms(),
                "sources": [s.model_dump() for s in _api_sources(run)],
            },
            id=1,
        )
        if entry.verdict is None:
            yield SseEvent(
                event="error",
                data={"code": "critical_source_unavailable"},
                id=2,
            )
        else:
            yield SseEvent(event="done", data={"check_id": check_id}, id=2)

    async def cancel_check(self, check_id: str) -> bool:
        """Отменить проверку («Отменить» на экране B2)."""
        entry = self._checks.get(check_id)
        if entry is None:
            return False
        entry.cancelled = True
        if entry.task is not None and not entry.task.done():
            entry.task.cancel()
        entry.finished.set()
        return True
