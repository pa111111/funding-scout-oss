"""Тесты daily-report — формирование сообщения и отправка."""

from __future__ import annotations

import pytest
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from funding_scout.reporting import format_daily_report, send_daily_report
from funding_scout.storage import SessionLocal
from funding_scout.storage.models import FundingSnapshot


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


def test_format_with_no_setups():
    msg = format_daily_report(
        meta={"snapshot_ts": None, "snapshot_iso": None, "age_seconds": None,
              "venue_counts": {}, "setups_count": 0},
        rows=[],
    )
    assert "<b>funding-scout daily</b>" in msg
    assert "Нет данных" in msg


def test_format_includes_top_setups():
    meta = {
        "snapshot_ts": 1700000000,
        "snapshot_iso": "2026-05-03T12:00:00+00:00",
        "age_seconds": 60,
        "venue_counts": {"hyperliquid": 191, "lighter": 156},
        "setups_count": 93,
    }
    rows = [
        {
            "ticker": "MEGA",
            "long_venue": "hyperliquid",
            "short_venue": "lighter",
            "spread_apr_pct": 126.7,
            "base_ev_usd_per_day": 17.36,
            "min_volume_24h_m_usd": 2.88,
        },
        {
            "ticker": "LIT",
            "long_venue": "hyperliquid",
            "short_venue": "lighter",
            "spread_apr_pct": 206.3,
            "base_ev_usd_per_day": 28.26,
            "min_volume_24h_m_usd": 2.16,
        },
    ]
    msg = format_daily_report(meta, rows, top_n=10)
    assert "<b>funding-scout daily</b>" in msg
    assert "hyperliquid=191" in msg
    assert "lighter=156" in msg
    assert "setups: 93" in msg
    # LIT идёт первым (выше APR)
    lit_pos = msg.find("LIT")
    mega_pos = msg.find("MEGA")
    assert lit_pos > 0 and mega_pos > 0
    assert lit_pos < mega_pos
    # Числа форматируются
    assert "+206.3" in msg
    assert "+126.7" in msg


def test_format_handles_none_volume():
    meta = {
        "snapshot_ts": 1700000000,
        "snapshot_iso": "2026-05-03T12:00:00+00:00",
        "age_seconds": 60,
        "venue_counts": {"hyperliquid": 1, "lighter": 1},
        "setups_count": 1,
    }
    rows = [
        {
            "ticker": "BTC",
            "long_venue": "lighter",
            "short_venue": "hyperliquid",
            "spread_apr_pct": 50.0,
            "base_ev_usd_per_day": 7.0,
            "min_volume_24h_m_usd": None,
        }
    ]
    msg = format_daily_report(meta, rows)
    # None volume → "—" в ячейке, не падает
    assert "BTC" in msg


def test_send_daily_report_skips_when_no_credentials(monkeypatch):
    """Без telegram кредов send_daily_report не падает, просто возвращает False."""
    monkeypatch.delenv("FUNDING_SCOUT_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("FUNDING_SCOUT_TELEGRAM_CHAT_ID", raising=False)

    _ins(1000, "hyperliquid", "BTC", 0.0001)
    _ins(1000, "lighter", "BTC", -0.0001)

    assert send_daily_report() is False


def test_top_n_truncates(monkeypatch):
    """Если установлен top_n=2, в сообщении не больше 2 связок."""
    meta = {
        "snapshot_ts": 1700000000,
        "snapshot_iso": "2026-05-03T12:00:00+00:00",
        "age_seconds": 60,
        "venue_counts": {"hyperliquid": 1, "lighter": 1},
        "setups_count": 5,
    }
    rows = [
        {"ticker": f"TKN{i}", "long_venue": "lighter", "short_venue": "hyperliquid",
         "spread_apr_pct": float(100 - i), "base_ev_usd_per_day": 1.0,
         "min_volume_24h_m_usd": 1.0}
        for i in range(5)
    ]
    msg = format_daily_report(meta, rows, top_n=2)
    # TKN0 и TKN1 (топ-2) присутствуют, TKN3 и TKN4 нет
    assert "TKN0" in msg
    assert "TKN1" in msg
    assert "TKN3" not in msg
    assert "TKN4" not in msg
