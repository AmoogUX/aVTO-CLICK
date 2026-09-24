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

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from avtoklik.service.showcase import get_car, list_showcase
from avtoklik.web.view import build_card_view, build_showcase_view

__all__ = ["STATIC_DIR", "TEMPLATES_DIR", "create_router", "mount_web", "plural"]

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

    return router


def mount_web(app: FastAPI) -> None:
    """Подключить веб-клиент к приложению.

    Статика монтируется под именем `static`, потому что шаблоны обращаются к
    ней через `url_for('static', path=…)`: путь монтирования становится
    деталью реализации и может быть заменён на CDN.
    """
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(create_router())
