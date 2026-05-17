"""Data-слой для UI. Чистые функции, возвращают сериализуемые dict'ы для AG-Grid.

Здесь запросы в БД и преобразование Setup → row dict. Layout/callbacks ничего
не знают о SQLAlchemy и работают только с этими dict'ами.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..detectors import CrossDexSameTickerDetector
from ..detectors.base import Setup
from ..storage import SessionLocal
from ..storage.models import FundingSnapshot

DEFAULT_CAPITAL_USD = 5000.0

# Окно поиска "предыдущего" снапшота для расчёта Δ Spread 1h.
# Snapshot loop = 3600s, в реальности ts разбегается на ±1-3 минуты.
# Берём ближайший ts в [latest - PREV_SNAPSHOT_MAX_LAG_SEC, latest).
# Если ничего нет (только что включили продакт / был долгий простой) — delta = None.
PREV_SNAPSHOT_MAX_LAG_SEC = 7200  # 2 часа: ловим даже если один цикл пропущен

# Sparkline tend-колонка: сколько часов истории спреда показывать.
# 24h хватает чтобы видеть полный цикл funding/окна и предыдущий день.
SPARKLINE_HOURS = 24

# Unicode block-elements для отрисовки sparkline в одной ячейке без SVG/HTML.
# 8 уровней высоты от низкого к высокому. Моноширинный шрифт в layout.
SPARKLINE_BLOCKS = "▁▂▃▄▅▆▇█"
SPARKLINE_NONE_CHAR = "·"  # для отсутствующих точек (нет данных в snapshot'е)

HOURS_PER_YEAR = 24 * 365


def setup_to_row(s: Setup, capital_usd: float = DEFAULT_CAPITAL_USD) -> dict:
    """Преобразовать Setup в плоский dict для AG-Grid.

    Все числа — raw (NaN/inf заменяются на None), форматирование — на стороне JS.
    Добавляем `base_ev_usd_per_day` посчитанное под конкретный капитал.
    """
    # JS не умеет инфинити в JSON, заменяем на None — AG-Grid отрисует прочерк.
    min_hours = s.min_profitable_hours
    if min_hours == float("inf") or min_hours != min_hours:  # inf or nan
        min_hours = None

    base_ev_usd_per_day = s.base_ev_per_dollar_per_day * capital_usd

    # min_volume_24h в миллионах для удобства отображения
    min_vol_m = (s.min_volume_24h_usd / 1e6) if s.min_volume_24h_usd is not None else None

    return {
        "type": s.type,
        "ticker": s.ticker,
        "long_venue": s.long_venue,
        "short_venue": s.short_venue,
        "spread_apr_pct": s.spread_apr_pct,
        "delta_spread_apr_pct_1h": None,  # заполняется в get_latest_setups, см. ниже
        "spread_sparkline": "",  # заполняется в get_latest_setups (история spread за 24h)
        "base_ev_usd_per_day": base_ev_usd_per_day,
        "min_profitable_hours": min_hours,
        "long_funding_apr_pct": s.long_funding_apr_pct,
        "short_funding_apr_pct": s.short_funding_apr_pct,
        "round_trip_cost_pct": s.round_trip_cost_pct,
        "min_volume_24h_m_usd": min_vol_m,
        "long_mark_price": s.long_mark_price,
        "short_mark_price": s.short_mark_price,
        "price_spread_pct": s.price_spread_pct,
        "snapshot_ts": s.snapshot_ts,
    }


def find_prev_snapshot_ts(
    session: Session,
    latest_ts: int,
    max_lag_sec: int = PREV_SNAPSHOT_MAX_LAG_SEC,
) -> int | None:
    """Ближайший ts ДО latest_ts в окне [latest_ts - max_lag_sec, latest_ts).

    Возвращает None если в окне ничего нет (свежий продакт, длинный простой).
    """
    return session.scalar(
        select(func.max(FundingSnapshot.ts)).where(
            FundingSnapshot.ts < latest_ts,
            FundingSnapshot.ts >= latest_ts - max_lag_sec,
        )
    )


def _spread_index(setups: list[Setup]) -> dict[tuple[str, str, str], float]:
    """Индекс по (ticker, long_venue, short_venue) → spread_apr_pct.

    Используется для матчинга связок между снапшотами при расчёте Δ Spread.
    Ключ нормализован: long/short venues в порядке детектора (long = меньший funding).
    """
    return {
        (s.ticker, s.long_venue, s.short_venue): s.spread_apr_pct
        for s in setups
    }


def render_sparkline_blocks(values: list[float | None]) -> str:
    """Отрендерить sparkline как строку из unicode block-elements.

    Чистая функция: список значений → строка. Маппит values в 8 уровней высоты
    относительно min/max доступных. None → '·' (отсутствие данных).
    Пустой список → пустая строка. Все одинаковые значения → средний уровень.

    Каждый символ — один tick (1 час в нашем UI). Чем выше блок — тем больше spread.
    """
    if not values:
        return ""

    valid = [v for v in values if v is not None]
    if not valid:
        return SPARKLINE_NONE_CHAR * len(values)

    vmin = min(valid)
    vmax = max(valid)
    span = vmax - vmin
    levels = len(SPARKLINE_BLOCKS)

    chars: list[str] = []
    for v in values:
        if v is None:
            chars.append(SPARKLINE_NONE_CHAR)
        elif span == 0:
            chars.append(SPARKLINE_BLOCKS[levels // 2])
        else:
            normalized = (v - vmin) / span
            idx = min(int(normalized * levels), levels - 1)
            chars.append(SPARKLINE_BLOCKS[idx])
    return "".join(chars)


def compute_spread_history(
    session: Session,
    latest_setups: list[Setup],
    latest_ts: int,
    hours: int = SPARKLINE_HOURS,
) -> dict[tuple[str, str, str], list[float | None]]:
    """История spread_apr_pct по часам для каждой связки из latest_setups.

    Возвращает dict[(ticker, long_venue, short_venue), list[float | None]].
    Список выровнен по списку DISTINCT ts в окне [latest_ts - hours*3600, latest_ts],
    отсортированному по возрастанию. Последний элемент — текущий ts.

    Реализация:
    1. Один SQL за все funding_rate_1h в окне (фильтр по тикерам latest_setups).
    2. Группировка в Python в dict[(ts, venue, ticker), rate].
    3. Для каждой связки пробегаем все ts окна, вычисляем spread = (rate_short -
       rate_long) × 8760 × 100. Если на этом ts какая-то нога отсутствует — None.

    Edge cases:
    - latest_setups пустой → возвращаем {}.
    - В окне нет ts → каждая связка получит пустой список [].
    """
    if not latest_setups:
        return {}

    min_ts = latest_ts - hours * 3600

    timestamps = [
        row[0]
        for row in session.execute(
            select(FundingSnapshot.ts)
            .where(FundingSnapshot.ts >= min_ts, FundingSnapshot.ts <= latest_ts)
            .distinct()
            .order_by(FundingSnapshot.ts)
        ).all()
    ]
    if not timestamps:
        return {key: [] for key in {
            (s.ticker, s.long_venue, s.short_venue) for s in latest_setups
        }}

    tickers = {s.ticker for s in latest_setups}
    rate_rows = session.execute(
        select(
            FundingSnapshot.ts,
            FundingSnapshot.venue,
            FundingSnapshot.ticker,
            FundingSnapshot.funding_rate_1h,
        ).where(
            FundingSnapshot.ts >= min_ts,
            FundingSnapshot.ts <= latest_ts,
            FundingSnapshot.ticker.in_(tickers),
        )
    ).all()

    rates: dict[tuple[int, str, str], float] = {
        (ts, venue, ticker): rate for ts, venue, ticker, rate in rate_rows
    }

    histories: dict[tuple[str, str, str], list[float | None]] = {}
    for s in latest_setups:
        key = (s.ticker, s.long_venue, s.short_venue)
        series: list[float | None] = []
        for ts in timestamps:
            long_rate = rates.get((ts, s.long_venue, s.ticker))
            short_rate = rates.get((ts, s.short_venue, s.ticker))
            if long_rate is None or short_rate is None:
                series.append(None)
            else:
                series.append((short_rate - long_rate) * HOURS_PER_YEAR * 100.0)
        histories[key] = series
    return histories


def compute_spread_deltas(
    latest_setups: list[Setup],
    prev_setups: list[Setup],
) -> dict[tuple[str, str, str], float]:
    """Δ Spread (1h) = spread_latest − spread_prev, ключ как в _spread_index.

    Чистая функция: список latest setups + список prev setups → dict дельт.
    Ключи, которых нет в prev, в результат не попадают (delta = None для них).

    ВАЖНО: long/short venues нормализованы детектором (long = меньший funding),
    то есть при смене знака funding на одной из ног между снапшотами связка
    "перевернётся" и сматчится с другим ключом. Это корректное поведение:
    Δ показывает изменение того же DIRECTIONAL setup, а если направление
    поменялось — это новый setup, delta = None.
    """
    prev_idx = _spread_index(prev_setups)
    deltas: dict[tuple[str, str, str], float] = {}
    for s in latest_setups:
        key = (s.ticker, s.long_venue, s.short_venue)
        if key in prev_idx:
            deltas[key] = s.spread_apr_pct - prev_idx[key]
    return deltas


def get_latest_setups(
    session: Session | None = None,
    capital_usd: float = DEFAULT_CAPITAL_USD,
) -> tuple[dict, list[dict]]:
    """Возвращает (meta, rows) для последнего снапшота.

    meta = {
        "snapshot_ts": int | None,
        "snapshot_iso": str | None,    # ISO-8601 UTC, для отображения
        "age_seconds": int | None,     # сколько секунд назад был снапшот
        "venue_counts": dict[str, int],  # {venue: tickers_count}
        "setups_count": int,
    }
    rows — list of dicts, готовых к скармливанию AG-Grid через rowData.
    """
    own_session = False
    if session is None:
        session = SessionLocal()
        own_session = True
    try:
        latest_ts = session.scalar(select(func.max(FundingSnapshot.ts)))
        if latest_ts is None:
            meta = {
                "snapshot_ts": None,
                "snapshot_iso": None,
                "age_seconds": None,
                "venue_counts": {},
                "setups_count": 0,
            }
            return meta, []

        # venue counts на этом ts
        venue_count_rows = session.execute(
            select(FundingSnapshot.venue, func.count())
            .where(FundingSnapshot.ts == latest_ts)
            .group_by(FundingSnapshot.venue)
        ).all()
        venue_counts = dict(venue_count_rows)

        # Запускаем все имеющиеся детекторы и аггрегируем setups
        detector = CrossDexSameTickerDetector()
        setups = detector.detect_for_snapshot(session, latest_ts)

        # Δ Spread (1h): сравниваем с предыдущим снапшотом (~ts-3600).
        # Если предыдущего нет (свежий продакт) — у всех строк delta = None.
        prev_ts = find_prev_snapshot_ts(session, int(latest_ts))
        deltas: dict[tuple[str, str, str], float] = {}
        if prev_ts is not None:
            prev_setups = detector.detect_for_snapshot(session, prev_ts)
            deltas = compute_spread_deltas(setups, prev_setups)

        # Sparkline истории спреда за 24h — один SQL и группировка в Python.
        # Для каждой связки получаем list[float | None], рендерим в unicode-строку.
        histories = compute_spread_history(session, setups, int(latest_ts))

        rows = []
        for s in setups:
            row = setup_to_row(s, capital_usd=capital_usd)
            key = (s.ticker, s.long_venue, s.short_venue)
            row["delta_spread_apr_pct_1h"] = deltas.get(key)
            row["spread_sparkline"] = render_sparkline_blocks(histories.get(key, []))
            rows.append(row)

        now_ts = int(datetime.now(UTC).timestamp())
        meta = {
            "snapshot_ts": int(latest_ts),
            "snapshot_iso": datetime.fromtimestamp(latest_ts, UTC).isoformat(),
            "age_seconds": max(0, now_ts - int(latest_ts)),
            "venue_counts": venue_counts,
            "setups_count": len(rows),
        }
        return meta, rows
    finally:
        if own_session:
            session.close()


# Re-export для удобства тестирования
__all__ = [
    "DEFAULT_CAPITAL_USD",
    "HOURS_PER_YEAR",
    "PREV_SNAPSHOT_MAX_LAG_SEC",
    "SPARKLINE_BLOCKS",
    "SPARKLINE_HOURS",
    "SPARKLINE_NONE_CHAR",
    "asdict",
    "compute_spread_deltas",
    "compute_spread_history",
    "find_prev_snapshot_ts",
    "get_latest_setups",
    "render_sparkline_blocks",
    "setup_to_row",
]
