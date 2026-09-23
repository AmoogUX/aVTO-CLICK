"""HTTP-слой «АвтоКлика»: флоу B «проверка автомобиля» (ТЗ, разделы 7.1 и 7.3).

Пакет зависит от нижних модулей только через протоколы из :mod:`avtoklik.api.deps`,
поэтому собирается и тестируется независимо от них.
"""

from avtoklik.api.app import API_PREFIX, create_app

__all__ = ["API_PREFIX", "create_app"]
