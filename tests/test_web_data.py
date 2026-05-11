"""Тесты web/data.py — слоя между БД и UI."""

from __future__ import annotations

import math

import pytest
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from funding_scout.detectors.base import Setup
from funding_scout.storage import SessionLocal
from funding_scout.storage.models import FundingSnapshot
from funding_scout.web.data import (
    DEFAULT_CAPITAL_USD,
    get_latest_setups,
    setup_to_row,
)


def _ins(ts, venue, ticker, rate, mark=100.0, vol=1_000_000):
    with SessionLocal() as s:
        s.execute(
            sqlite_insert(FundingSnapshot)
            .values(
                ts=ts,
                venue=venue,
                ticker=ticker,
                funding_rate_1h=rate,
                mark_price=mark,
                index_price=None,
                oi_long=None,
                oi_short=None,
                volume_24h=vol,
                raw={},
            )
            .prefix_with("OR IGNORE")
        )
        s.commit()


def _setup(**overrides) -> Setup:
    base = dict(
        type="cross-dex-same-ticker",
        ticker="BTC",
        long_venue="lighter",
        short_venue="hyperliquid",
        spread_apr_pct=100.0,
        base_ev_per_dollar_per_day=100.0 / 100 / 365,  # arbitrary
        long_funding_apr_pct=-50.0,
        short_funding_apr_pct=50.0,
        round_trip_cost_pct=0.06,
        min_profitable_hours=10.0,
        long_mark_price=70000.0,
        short_mark_price=70010.0,
        price_spread_pct=-0.014,
        min_volume_24h_usd=1_000_000.0,
        long_volume_24h_usd=1_000_000.0,
        short_volume_24h_usd=2_000_000.0,
        snapshot_ts=1700000000,
    )
    base.update(overrides)
    return Setup(**base)


# === setup_to_row ===


def test_row_includes_all_display_fields():
    row = setup_to_row(_setup(), capital_usd=5000)
    keys = {
        "type",
        "ticker",
        "long_venue",
        "short_venue",
        "spread_apr_pct",
        "base_ev_usd_per_day",
        "min_profitable_hours",
        "long_funding_apr_pct",
        "short_funding_apr_pct",
        "round_trip_cost_pct",
        "min_volume_24h_m_usd",
        "long_mark_price",
        "short_mark_price",
        "price_spread_pct",
        "snapshot_ts",
    }
    assert keys.issubset(row)


def test_base_ev_scaled_by_capital():
    s = _setup(base_ev_per_dollar_per_day=0.001)  # $0.001/day per $1
    row = setup_to_row(s, capital_usd=5000)
    assert row["base_ev_usd_per_day"] == pytest.approx(5.0)

    row2 = setup_to_row(s, capital_usd=20000)
    assert row2["base_ev_usd_per_day"] == pytest.approx(20.0)


def test_inf_min_holding_replaced_with_none():
    """JSON не сериализует inf — для UI заменяем на None (отрисуется как —)."""
    s = _setup(min_profitable_hours=math.inf)
    row = setup_to_row(s)
    assert row["min_profitable_hours"] is None


def test_nan_min_holding_replaced_with_none():
    s = _setup(min_profitable_hours=float("nan"))
    row = setup_to_row(s)
    assert row["min_profitable_hours"] is None


def test_volume_converted_to_millions():
    s = _setup(min_volume_24h_usd=2_500_000.0)
    row = setup_to_row(s)
    assert row["min_volume_24h_m_usd"] == pytest.approx(2.5)


def test_volume_none_passes_through():
    s = _setup(min_volume_24h_usd=None)
    row = setup_to_row(s)
    assert row["min_volume_24h_m_usd"] is None


def test_default_capital():
    """DEFAULT_CAPITAL_USD используется если параметр не передан."""
    s = _setup(base_ev_per_dollar_per_day=0.001)
    row = setup_to_row(s)
    assert row["base_ev_usd_per_day"] == pytest.approx(0.001 * DEFAULT_CAPITAL_USD)


# === get_latest_setups ===


def test_empty_db_returns_empty_meta_and_rows():
    meta, rows = get_latest_setups()
    assert meta["snapshot_ts"] is None
    assert meta["snapshot_iso"] is None
    assert meta["setups_count"] == 0
    assert meta["venue_counts"] == {}
    assert rows == []


def test_returns_meta_with_venue_counts():
    _ins(1000, "hyperliquid", "BTC", 0.0001)
    _ins(1000, "hyperliquid", "ETH", 0.0001)
    _ins(1000, "lighter", "BTC", -0.0001)

    meta, rows = get_latest_setups()
    assert meta["snapshot_ts"] == 1000
    assert meta["venue_counts"] == {"hyperliquid": 2, "lighter": 1}
    assert meta["setups_count"] == 1  # только BTC даёт пару


def test_meta_age_is_non_negative():
    _ins(1000, "hyperliquid", "BTC", 0.0001)
    _ins(1000, "lighter", "BTC", -0.0001)

    meta, _ = get_latest_setups()
    assert meta["age_seconds"] is not None
    assert meta["age_seconds"] >= 0


def test_uses_only_latest_ts():
    """В БД может быть несколько ts — UI смотрит только на самый свежий."""
    _ins(900, "hyperliquid", "BTC", 0.0001)
    _ins(900, "lighter", "BTC", -0.0001)
    _ins(1000, "hyperliquid", "BTC", 0.0002)
    _ins(1000, "lighter", "BTC", 0.0)

    meta, rows = get_latest_setups()
    assert meta["snapshot_ts"] == 1000
    assert len(rows) == 1
    # spread = 0.0002 × 8760 = 1.752 → 175.2%
    assert rows[0]["spread_apr_pct"] == pytest.approx(175.2, rel=1e-3)


def test_rows_are_json_serializable():
    """AG-Grid ест rowData через JSON. Никаких inf/nan/Decimal/datetime в значениях."""
    import json

    _ins(1000, "hyperliquid", "BTC", 0.0001, vol=None)
    _ins(1000, "lighter", "BTC", -0.0001)
    _, rows = get_latest_setups()

    # Должно сериализоваться без TypeError или ValueError на inf/nan
    json.dumps(rows)


def test_capital_parameter_affects_ev():
    _ins(1000, "hyperliquid", "BTC", 0.0001)
    _ins(1000, "lighter", "BTC", -0.0001)

    _, rows_5k = get_latest_setups(capital_usd=5000)
    _, rows_50k = get_latest_setups(capital_usd=50000)

    assert rows_5k[0]["base_ev_usd_per_day"] == pytest.approx(
        rows_50k[0]["base_ev_usd_per_day"] / 10, rel=1e-9
    )
