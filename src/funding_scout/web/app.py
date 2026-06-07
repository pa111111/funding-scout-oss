"""Dash-приложение funding-scout. Точка входа — `create_app()`.

В v0.1 layout рендерится один раз при старте приложения и не обновляется без F5.
В v0.2 добавим dcc.Interval + callback для авто-рефреша каждые N секунд после
снапшота на VPS.
"""

from __future__ import annotations

from datetime import UTC, datetime

import dash
import dash_bootstrap_components as dbc
from flask import jsonify

from .data import DEFAULT_CAPITAL_USD, get_latest_setups
from .layout import make_layout


def register_api(app: dash.Dash, capital_usd: float = DEFAULT_CAPITAL_USD) -> None:
    """Регистрирует read-only JSON-эндпоинты на Flask-сервере Dash.

    Машиночитаемая витрина вердикта scout'а для Hermes-оператора. Тот же расчёт,
    что и у Dash-UI (`get_latest_setups`) — один источник, две витрины. Без auth:
    периметр доверия тот же, что у UI (Tailscale/localhost), ключей здесь нет.
    """

    @app.server.route("/api/setups")
    def api_setups():  # type: ignore[unused-ignore]
        meta, rows = get_latest_setups(capital_usd=capital_usd)
        return jsonify(
            {
                # когда scout посчитал вердикт (≠ snapshot_ts — это возраст сырья)
                "computed_at": datetime.now(UTC).isoformat(),
                "capital_usd": capital_usd,
                "meta": meta,
                "setups": rows,
            }
        )


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
    register_api(app, capital_usd=capital_usd)
    return app


def run(host: str = "127.0.0.1", port: int = 8050, debug: bool = False) -> None:
    """Запустить Dash dev server (не для прода — в проде через gunicorn)."""
    app = create_app()
    app.run(host=host, port=port, debug=debug)
