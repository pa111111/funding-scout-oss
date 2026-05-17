"""Dash layout для funding-scout.

Структура:
- header bar: название + meta (snapshot ts, age, venue counts, capital)
- AG-Grid таблица сетапов

Без cell-coloring по риск-метрикам (парадигма transparent disclosure):
- spread_apr_pct подкрашен зелёным/красным только в знак (не в "хорошо/плохо")
- остальные колонки — нейтральные числа

Для v0.1 без auto-refresh: пользователь нажимает refresh-кнопку или F5.
В v0.2 добавим dcc.Interval с минутной частотой.
"""

from __future__ import annotations

import dash_ag_grid as dag
import dash_bootstrap_components as dbc
from dash import dcc, html


COLUMN_DEFS: list[dict] = [
    {
        "field": "ticker",
        "headerName": "Ticker",
        "pinned": "left",
        "filter": "agTextColumnFilter",
        "width": 100,
    },
    # NB: поле `type` приходит в row dict из setup_to_row, но в UI пока не показываем —
    # есть только один детектор (cross-dex-same-ticker), колонка занимала бы 180px ради
    # одинакового значения во всех строках. Вернуть в COLUMN_DEFS как только появится
    # второй тип (см. таксономию в arbitrage_strategies.md).
    {
        "field": "long_venue",
        "headerName": "LONG perp on",
        "headerTooltip": (
            "Биржа, на которой нужно открыть ЛОНГ перп. "
            "Long-нога = там где funding ниже (нам платят за лонг или меньше отнимают). "
            "Сейчас все связки — perp-only; когда добавим spot-варианты, "
            "появится отдельная отметка про инструмент."
        ),
        "filter": "agSetColumnFilter",
        "cellStyle": {"color": "#0a7d2c", "fontWeight": "600"},
        "width": 140,
    },
    {
        "field": "short_venue",
        "headerName": "SHORT perp on",
        "headerTooltip": (
            "Биржа, на которой нужно открыть ШОРТ перп. "
            "Short-нога = там где funding выше (нам платят за шорт). "
            "Сейчас все связки — perp-only."
        ),
        "filter": "agSetColumnFilter",
        "cellStyle": {"color": "#a02020", "fontWeight": "600"},
        "width": 140,
    },
    {
        "field": "min_volume_24h_m_usd",
        "headerName": "Min vol 24h $M",
        "headerTooltip": (
            "Минимум 24h volume среди двух ног, в миллионах USD. "
            "Один из ключевых показателей — определяет slippage под твой размер. "
            "Грубо: hourly_vol = vol_24h / 24; slippage % ≈ order_size / hourly_vol × k. "
            "Серым подсвечено vol < $1M (для $5k высокий slippage)."
        ),
        "type": "numericColumn",
        "filter": "agNumberColumnFilter",
        "valueFormatter": {
            "function": "params.value == null ? '—' : '$' + params.value.toFixed(2) + 'M'"
        },
        "cellStyle": {
            "function": (
                "params.value == null ? {color:'#aaa'} : "
                "(params.value < 1 ? {color:'#aaa'} : null)"
            )
        },
        "width": 140,
    },
    {
        "field": "spread_apr_pct",
        "headerName": "Spread APR %",
        "type": "numericColumn",
        "filter": "agNumberColumnFilter",
        "sort": "desc",
        "valueFormatter": {
            "function": "params.value == null ? '—' : (params.value >= 0 ? '+' : '') + params.value.toFixed(1) + '%'"
        },
        "cellStyle": {
            "function": "params.value > 0 ? {color:'#0a7d2c'} : (params.value < 0 ? {color:'#a02020'} : null)"
        },
        "width": 130,
    },
    {
        "field": "spread_sparkline",
        "headerName": "Trend 24h",
        "headerTooltip": (
            "Sparkline спреда за 24 часа (1 символ = 1 час). "
            "Каждый блок ▁..█ — относительная высота spread_apr_pct в окне 24h. "
            "· = в этом часе не было snapshot'а или одна нога пропала. "
            "Слева → 24h назад, справа → текущий час."
        ),
        "sortable": False,
        "filter": False,
        "valueFormatter": {
            "function": "params.value || ''"
        },
        "cellStyle": {
            "fontFamily": "Consolas, 'Courier New', monospace",
            "fontSize": "14px",
            "letterSpacing": "0px",
            "color": "#444",
        },
        "width": 230,
    },
    {
        "field": "window_age_hours",
        "headerName": "Age h",
        "headerTooltip": (
            "Сколько часов подряд (от текущего snapshot'а назад) спред держится ≥ 30% APR. "
            "0 = сейчас ниже порога. 1-3 = свежее окно, рано в стадии. "
            "4-12 = середина типичного окна. 24 = весь день держится, скорее carry чем окно. "
            "Считается по непрерывной цепочке — дыра в данных обрывает счёт."
        ),
        "type": "numericColumn",
        "filter": "agNumberColumnFilter",
        "valueFormatter": {
            "function": "params.value == null ? '—' : params.value + 'h'"
        },
        "cellStyle": {
            "function": (
                "params.value == null ? null : "
                "(params.value === 0 ? {color:'#aaa'} : "
                "(params.value <= 3 ? {color:'#0a7d2c', fontWeight:'600'} : "
                "(params.value >= 20 ? {color:'#8a6500'} : null)))"
            )
        },
        "width": 90,
    },
    {
        "field": "delta_spread_apr_pct_1h",
        "headerName": "Δ 1h",
        "headerTooltip": (
            "Изменение Spread APR % относительно предыдущего snapshot (~1h назад). "
            "Положительное = окно расширяется. Отрицательное = схлопывается. "
            "— = нет предыдущего снапшота в окне 2h."
        ),
        "type": "numericColumn",
        "filter": "agNumberColumnFilter",
        "valueFormatter": {
            "function": (
                "params.value == null ? '—' : "
                "(params.value >= 0 ? '+' : '') + params.value.toFixed(1) + '%'"
            )
        },
        "cellStyle": {
            "function": (
                "params.value == null ? null : "
                "(params.value > 5 ? {color:'#0a7d2c', fontWeight:'600'} : "
                "(params.value < -5 ? {color:'#a02020', fontWeight:'600'} : "
                "{color:'#888'}))"
            )
        },
        "width": 100,
    },
    {
        "field": "base_ev_usd_per_day",
        "headerName": "EV $/day",
        "headerTooltip": "Base EV в долларах в день на стандартный капитал ($5k). Без учёта риск-метрик.",
        "type": "numericColumn",
        "filter": "agNumberColumnFilter",
        "valueFormatter": {
            "function": "params.value == null ? '—' : '$' + params.value.toFixed(2)"
        },
        "width": 110,
    },
    {
        "field": "min_profitable_hours",
        "headerName": "Min hold h",
        "headerTooltip": "Часов держать чтобы покрыть round-trip cost. — = inf (никогда).",
        "type": "numericColumn",
        "filter": "agNumberColumnFilter",
        "valueFormatter": {
            "function": "params.value == null ? '—' : params.value.toFixed(1) + 'h'"
        },
        "width": 110,
    },
    {
        "field": "long_funding_apr_pct",
        "headerName": "Long APR %",
        "headerTooltip": "Funding APR на лонг-ноге (отрицательный = нам платят за лонг)",
        "type": "numericColumn",
        "filter": "agNumberColumnFilter",
        "valueFormatter": {
            "function": "params.value == null ? '—' : (params.value >= 0 ? '+' : '') + params.value.toFixed(1) + '%'"
        },
        "width": 110,
    },
    {
        "field": "short_funding_apr_pct",
        "headerName": "Short APR %",
        "headerTooltip": "Funding APR на шорт-ноге (положительный = нам платят за шорт)",
        "type": "numericColumn",
        "filter": "agNumberColumnFilter",
        "valueFormatter": {
            "function": "params.value == null ? '—' : (params.value >= 0 ? '+' : '') + params.value.toFixed(1) + '%'"
        },
        "width": 110,
    },
    {
        "field": "round_trip_cost_pct",
        "headerName": "RT cost %",
        "headerTooltip": "Round-trip cost обеих ног (в %, см. ev/costs.py)",
        "type": "numericColumn",
        "valueFormatter": {
            "function": "params.value == null ? '—' : params.value.toFixed(2) + '%'"
        },
        "width": 100,
    },
    {
        "field": "price_spread_pct",
        "headerName": "Price spread %",
        "headerTooltip": "(long_mark - short_mark) / short_mark. +ve = лонгуем дороже, convergence работает против нас.",
        "type": "numericColumn",
        "filter": "agNumberColumnFilter",
        "valueFormatter": {
            "function": "params.value == null ? '—' : (params.value >= 0 ? '+' : '') + params.value.toFixed(2) + '%'"
        },
        "width": 130,
    },
    {
        "field": "long_mark_price",
        "headerName": "Long $",
        "type": "numericColumn",
        "valueFormatter": {
            "function": "params.value == null ? '—' : params.value.toFixed(4)"
        },
        "width": 110,
    },
    {
        "field": "short_mark_price",
        "headerName": "Short $",
        "type": "numericColumn",
        "valueFormatter": {
            "function": "params.value == null ? '—' : params.value.toFixed(4)"
        },
        "width": 110,
    },
]


def _format_meta(meta: dict) -> list:
    """Формирует строку статуса в верхней панели."""
    if meta.get("snapshot_ts") is None:
        return [
            dbc.Alert(
                "В БД ещё нет снапшотов. Запусти `funding-scout snapshot` чтобы появились данные.",
                color="warning",
                className="mb-0",
            )
        ]

    age_min = (meta["age_seconds"] or 0) // 60
    age_color = "success" if age_min < 70 else ("warning" if age_min < 180 else "danger")
    age_label = f"{age_min} min ago" if age_min < 60 else f"{age_min // 60}h {age_min % 60}m ago"

    venue_badges = [
        dbc.Badge(
            f"{venue}: {count}",
            color="secondary",
            className="me-1",
        )
        for venue, count in sorted(meta["venue_counts"].items())
    ]

    return [
        dbc.Row(
            [
                dbc.Col(
                    [
                        html.Span("Snapshot: ", className="text-muted"),
                        html.Span(meta["snapshot_iso"], className="font-monospace"),
                        " ",
                        dbc.Badge(age_label, color=age_color, className="ms-2"),
                    ],
                    width="auto",
                ),
                dbc.Col(venue_badges, width="auto"),
                dbc.Col(
                    [
                        html.Span(f"Setups: {meta['setups_count']}", className="text-muted"),
                    ],
                    width="auto",
                ),
            ],
            className="g-2 align-items-center",
        )
    ]


def make_layout(meta: dict, rows: list[dict]) -> html.Div:
    """Главная страница funding-scout."""
    return dbc.Container(
        [
            dcc.Store(id="setups-store", data=rows),
            html.Div(
                [
                    html.H3("funding-scout", className="d-inline-block me-3"),
                    html.Span(
                        "DEX-only funding-rate arbitrage scout",
                        className="text-muted",
                    ),
                    html.Span(
                        " · v0.1",
                        className="text-muted ms-2",
                    ),
                ],
                className="mt-3 mb-2",
            ),
            html.Div(_format_meta(meta), className="mb-3"),
            html.Hr(className="my-2"),
            dag.AgGrid(
                id="setups-grid",
                rowData=rows,
                columnDefs=COLUMN_DEFS,
                dashGridOptions={
                    "animateRows": True,
                    "pagination": False,
                    "rowHeight": 32,
                    "tooltipShowDelay": 300,
                    "suppressMenuHide": True,
                },
                defaultColDef={
                    "sortable": True,
                    "resizable": True,
                    "filter": True,
                    "floatingFilter": True,
                },
                style={"height": "75vh"},
                className="ag-theme-balham",
            ),
            html.Div(
                [
                    html.Small(
                        "Парадигма: показываем все связки с risk-метриками рядом, "
                        "не фильтруем по EV/риску. Сортируй и фильтруй сам через шапки колонок. "
                        "Pre-market и low-vol тоже видны — это твой выбор, не наш.",
                        className="text-muted",
                    )
                ],
                className="mt-3 small",
            ),
        ],
        fluid=True,
    )
