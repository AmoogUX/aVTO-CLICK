"""Сквозная демонстрация: госномер → опрос источников → вердикт (B1 → B2 → B3).

Запуск: ``python3 examples/demo_check.py``

Показывает то, что реально собирается из кода: экран вердикта по эталонному
автомобилю дизайна и по автомобилю с проблемами. Источники берут данные из
фикстур — сетевых запросов нет (см. docs/07-план-по-ролям.md).
"""

from __future__ import annotations

import asyncio

from avtoklik.api.schemas import Subject
from avtoklik.service.orchestrator import CheckService


def rub(value: int) -> str:
    """Рубли с неразрывной группировкой — как на экране."""
    return f"{value:,}".replace(",", " ")


async def show(plate: str) -> None:
    """Прогнать проверку и напечатать вердикт."""
    service = CheckService()
    started = await service.start_check(Subject(type="plate", value=plate))

    status = await service.get_check(started.check_id)
    assert status is not None
    while status.status in ("queued", "running"):
        await asyncio.sleep(0.02)
        status = await service.get_check(started.check_id)
        assert status is not None

    print(f"\n{'=' * 70}")
    print(f"  {plate}   ·   опрошено источников: {status.progress:.0%}")
    for source in status.sources:
        print(f"      {source.title:<34} {source.state}")

    verdict = await service.get_verdict(started.check_id)
    if verdict is None:
        print("\n  Вердикт не выдан: обязательный источник недоступен.")
        return

    if status.vehicle is not None:
        print(f"\n  {status.vehicle.title}, {status.vehicle.year}   VIN {status.vehicle.vin}")

    print(f"\n  {verdict.score} ИЗ 100 — {verdict.headline}")
    print(f"  {verdict.subtext}\n")
    for block in verdict.checks:
        print(f"    [{block.status:<9}] {block.title:<14} {block.value or ''}  {block.note or ''}")

    if verdict.cluster is not None:
        print(f"\n  Это авто на площадках · разница {rub(verdict.cluster.spread_rub)} ₽")
        for price in verdict.cluster.listings:
            print(f"    {price.source:<10} {rub(price.price_rub):>10} ₽   {price.seen or ''}")
        print(f"    → {verdict.cluster.dedup_note}")

    print(f"\n  Источники: {verdict.coverage.summary}")

    if verdict.repair is not None:
        repair = verdict.repair
        print(f"\n  {repair.headline}")
        print(
            f"    {rub(repair.amount_rub)} ₽   (от {rub(repair.low_rub)} до {rub(repair.high_rub)})"
        )
        for line in repair.lines:
            print(f"    · {line}")


async def main() -> None:
    """Эталонный автомобиль дизайна и автомобиль с проблемами."""
    await show("К999КК799")
    await show("У777МН178")


if __name__ == "__main__":
    asyncio.run(main())
