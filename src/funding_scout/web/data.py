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
        rows = [setup_to_row(s, capital_usd=capital_usd) for s in setups]

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
__all__ = ["DEFAULT_CAPITAL_USD", "asdict", "get_latest_setups", "setup_to_row"]
