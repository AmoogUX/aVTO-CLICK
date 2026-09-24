"""Поднять веб-клиент «АвтоКлика» локально.

    PYTHONPATH=. python3 examples/run_web.py

Открывается витрина (`/`), с неё — карточка автомобиля (`/car/{id}`).
API проверки остаётся на своём месте: `/api/v1`, документация — `/api/v1/docs`.

Данные — фикстуры сценария: живые адаптеры площадок не реализованы, пока не
закрыт правовой вопрос о сборе объявлений.
"""

from __future__ import annotations

import uvicorn

from avtoklik.api.app import create_app

HOST = "127.0.0.1"
PORT = 8000

if __name__ == "__main__":
    print(f"Витрина: http://{HOST}:{PORT}/")
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="info")
