"""Маршруты веб-клиента: витрина (D1) и карточка автомобиля (C1).

HTML собирается на сервере и приходит готовым. Это требование роли веба из
3.1 («SEO-витрина и точка входа»): страница, которая рисуется JavaScript'ом
после загрузки, для поискового трафика существует хуже, а бесплатный трафик
на карточки — главный канал классифайда.

Ни один из этих маршрутов не ходит во внешние источники. Открытие витрины и
карточки обязано быть бесплатным для нас (5.5а): платные запросы начинаются
только после покупки проверки.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from avtoklik.api.schemas import PLATE_HINT
from avtoklik.service.sell import create_draft, get_draft, publish_draft, set_price
from avtoklik.service.showcase import get_car, list_showcase
from avtoklik.web.view import build_card_view, build_sell_view, build_showcase_view

__all__ = ["STATIC_DIR", "TEMPLATES_DIR", "create_router", "mount_web", "plural"]


def _as_int(raw: str) -> int | None:
    """Прочитать число из формы. Мусор — это ``None``, а не исключение.

    Поля формы приходят строками, и «сто тысяч» вместо «100000» не должно
    ронять запрос: дальше по флоу отсутствующий пробег поймают автопроверки
    и объяснят продавцу, что именно исправить.
    """
    digits = "".join(char for char in raw if char.isdigit())
    return int(digits) if digits else None


TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def plural(count: int, one: str, few: str, many: str) -> str:
    """Русское склонение существительного при числе.

    «1 объявление», «2 объявления», «5 объявлений». Без этого интерфейс
    начинает писать «5 объявление» или уходить в «объявлений: 5» — оба
    варианта противоречат §1.6 («коротко, по-человечески»).
    """
    tail_100 = abs(count) % 100
    tail_10 = abs(count) % 10
    if 11 <= tail_100 <= 14:
        return many
    if tail_10 == 1:
        return one
    if 2 <= tail_10 <= 4:
        return few
    return many


def _templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["plural"] = plural
    return templates


def create_router() -> APIRouter:
    """Роутер веб-клиента.

    Имена маршрутов (`showcase`, `car_card`) — часть контракта шаблонов:
    ссылки строятся через `url_for`, поэтому путь можно поменять, не трогая
    разметку.
    """
    router = APIRouter(include_in_schema=False)
    templates = _templates()

    @router.get("/", response_class=HTMLResponse, name="showcase")
    async def showcase(request: Request) -> HTMLResponse:
        """Витрина: лента объявлений с оценкой цены относительно рынка."""
        cars = build_showcase_view(list_showcase())
        return templates.TemplateResponse(request, "showcase.html", {"cars": cars})

    @router.get("/car/{listing_id}", response_class=HTMLResponse, name="car_card")
    async def car_card(request: Request, listing_id: str) -> HTMLResponse:
        """Карточка автомобиля: бесплатный блок ремонта, ниже — проверка по базам."""
        car = get_car(listing_id)
        if car is None:
            # 404 отдаётся именно кодом, а не «пустой страницей с текстом»:
            # иначе поисковик проиндексирует снятое объявление как живое.
            return templates.TemplateResponse(request, "not_found.html", {}, status_code=404)
        return templates.TemplateResponse(request, "car.html", {"car": build_card_view(car)})

    # ── Флоу продажи (E1 → E3 → E4) ──────────────────────────────────────
    #
    # Шагов ровно три, и каждый — отдельный адрес: продавец уходит искать
    # пробег в ПТС и возвращается по ссылке, а не начинает заново. Черновик
    # живёт на сервере (T-12-5), поэтому возврат ничего не теряет.

    @router.get("/sell", response_class=HTMLResponse, name="sell_start")
    async def sell_start(request: Request) -> HTMLResponse:
        """E1: госномер и то, что знает только владелец."""
        return templates.TemplateResponse(request, "sell_start.html", {})

    @router.post("/sell", response_class=HTMLResponse, name="sell_create")
    async def sell_create(
        request: Request,
        subject: Annotated[str, Form()] = "",
        mileage_km: Annotated[str, Form()] = "",
        region_id: Annotated[str, Form()] = "",
        description: Annotated[str, Form()] = "",
        ownership: Annotated[str, Form()] = "",
    ) -> Response:
        """Создать черновик по введённому госномеру.

        Пустой или неопознанный номер — не ошибка формы, а состояние CC5:
        объясняем, что не так, и предлагаем обходной путь, а не отбиваем ввод
        красной рамкой.
        """
        draft, found = create_draft(
            subject,
            mileage_km=_as_int(mileage_km),
            region_id=_as_int(region_id),
            description=description.strip(),
            ownership_confirmed=bool(ownership),
        )
        if not found.found:
            return templates.TemplateResponse(
                request,
                "sell_start.html",
                {"subject": subject, "not_found": True, "hint": PLATE_HINT},
                status_code=422,
            )
        return RedirectResponse(
            request.url_for("sell_price", draft_id=draft.draft_id), status_code=303
        )

    @router.get("/sell/{draft_id}/price", response_class=HTMLResponse, name="sell_price")
    async def sell_price(request: Request, draft_id: str) -> HTMLResponse:
        """E3: что сервис заполнил сам и сколько просить."""
        draft = get_draft(draft_id)
        if draft is None:
            return templates.TemplateResponse(request, "not_found.html", {}, status_code=404)
        return templates.TemplateResponse(
            request, "sell_price.html", {"view": build_sell_view(draft)}
        )

    @router.post("/sell/{draft_id}/price", response_class=HTMLResponse, name="sell_publish")
    async def sell_publish(
        request: Request, draft_id: str, price_rub: Annotated[str, Form()] = ""
    ) -> Response:
        """Назначить цену и опубликовать."""
        draft = get_draft(draft_id)
        if draft is None:
            return templates.TemplateResponse(request, "not_found.html", {}, status_code=404)
        price = _as_int(price_rub)
        if price is not None and price > 0:
            set_price(draft_id, price)
        publish_draft(draft_id)
        return RedirectResponse(request.url_for("sell_done", draft_id=draft_id), status_code=303)

    @router.get("/sell/{draft_id}/done", response_class=HTMLResponse, name="sell_done")
    async def sell_done(request: Request, draft_id: str) -> HTMLResponse:
        """E4: опубликовано — или список правок, если автопроверки не прошли (CC6)."""
        draft = get_draft(draft_id)
        if draft is None:
            return templates.TemplateResponse(request, "not_found.html", {}, status_code=404)
        return templates.TemplateResponse(
            request, "sell_done.html", {"view": build_sell_view(draft)}
        )

    return router


def mount_web(app: FastAPI) -> None:
    """Подключить веб-клиент к приложению.

    Статика монтируется под именем `static`, потому что шаблоны обращаются к
    ней через `url_for('static', path=…)`: путь монтирования становится
    деталью реализации и может быть заменён на CDN.
    """
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(create_router())
