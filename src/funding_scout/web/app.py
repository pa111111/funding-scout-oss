"""Dash-приложение funding-scout. Точка входа — `create_app()`.

В v0.1 layout рендерится один раз при старте приложения и не обновляется без F5.
В v0.2 добавим dcc.Interval + callback для авто-рефреша каждые N секунд после
снапшота на VPS.
"""

from __future__ import annotations

import dash
import dash_bootstrap_components as dbc

from .data import DEFAULT_CAPITAL_USD, get_latest_setups
from .layout import make_layout


def create_app(capital_usd: float = DEFAULT_CAPITAL_USD) -> dash.Dash:
    """Создаёт Dash app со всеми компонентами. Возвращает готовый app."""
    app = dash.Dash(
        __name__,
        external_stylesheets=[dbc.themes.BOOTSTRAP],
        title="funding-scout",
        update_title=None,
        suppress_callback_exceptions=False,
    )

    # Layout — функция, чтобы он перестраивался на каждый перезаход страницы.
    def serve_layout():
        meta, rows = get_latest_setups(capital_usd=capital_usd)
        return make_layout(meta, rows)

    app.layout = serve_layout
    return app


def run(host: str = "127.0.0.1", port: int = 8050, debug: bool = False) -> None:
    """Запустить Dash dev server (не для прода — в проде через gunicorn)."""
    app = create_app()
    app.run(host=host, port=port, debug=debug)
