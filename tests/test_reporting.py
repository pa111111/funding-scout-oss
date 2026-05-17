"""Тесты daily-report — формирование сообщения и отправка."""

from __future__ import annotations

import pytest
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from funding_scout.reporting import (
    TELEGRAM_MIN_VOLUME_USD,
    format_daily_report,
    send_daily_report,
)
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


def _row(ticker="BTC", spread=100.0, vol_m=2.0, **overrides):
    """Helper для row dict с минимально-валидными полями для daily-report."""
    base = {
        "ticker": ticker,
        "long_venue": "lighter",
        "short_venue": "hyperliquid",
        "spread_apr_pct": spread,
        "base_ev_usd_per_day": spread / 100 * 5000 / 365,
        "min_volume_24h_m_usd": vol_m,
    }
    base.update(overrides)
    return base


def _meta(setups_count=1):
    return {
        "snapshot_ts": 1700000000,
        "snapshot_iso": "2026-05-03T12:00:00+00:00",
        "age_seconds": 60,
        "venue_counts": {"hyperliquid": 1, "lighter": 1},
        "setups_count": setups_count,
    }


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
        _row(ticker="MEGA", spread=126.7, vol_m=2.88, long_venue="hyperliquid", short_venue="lighter"),
        _row(ticker="LIT", spread=206.3, vol_m=2.16, long_venue="hyperliquid", short_venue="lighter"),
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


def test_send_daily_report_skips_when_no_credentials(monkeypatch):
    """Без telegram кредов send_daily_report не падает, просто возвращает False."""
    monkeypatch.delenv("FUNDING_SCOUT_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("FUNDING_SCOUT_TELEGRAM_CHAT_ID", raising=False)

    _ins(1000, "hyperliquid", "BTC", 0.0001)
    _ins(1000, "lighter", "BTC", -0.0001)

    assert send_daily_report() is False


def test_top_n_truncates():
    """Если установлен top_n=2, в сообщении не больше 2 связок."""
    rows = [_row(ticker=f"TKN{i}", spread=float(100 - i), vol_m=2.0) for i in range(5)]
    msg = format_daily_report(_meta(setups_count=5), rows, top_n=2)
    # TKN0 и TKN1 (топ-2) присутствуют, TKN3 и TKN4 нет
    assert "TKN0" in msg
    assert "TKN1" in msg
    assert "TKN3" not in msg
    assert "TKN4" not in msg


# === Volume filter (TELEGRAM_MIN_VOLUME_USD) ===


def test_filter_drops_setups_below_min_volume():
    """Связки с vol_m < 1.0 (= $1M) отфильтрованы."""
    rows = [
        _row(ticker="HIGH", spread=100.0, vol_m=5.0),   # пропустить
        _row(ticker="LOW", spread=200.0, vol_m=0.3),    # отбросить (хоть и больше APR)
    ]
    msg = format_daily_report(_meta(setups_count=2), rows)
    assert "HIGH" in msg
    assert "LOW" not in msg


def test_filter_drops_none_volume():
    """Связки с volume = None отфильтрованы (нет уверенности в размере)."""
    rows = [
        _row(ticker="KNOWN", spread=100.0, vol_m=2.0),
        _row(ticker="UNKNOWN", spread=200.0, vol_m=None),
    ]
    msg = format_daily_report(_meta(setups_count=2), rows)
    assert "KNOWN" in msg
    assert "UNKNOWN" not in msg


def test_filter_boundary_inclusive_at_1m():
    """vol = ровно $1M (vol_m=1.0) должен ПРОЙТИ — граница включающая."""
    rows = [_row(ticker="EDGE", spread=100.0, vol_m=1.0)]
    msg = format_daily_report(_meta(setups_count=1), rows)
    assert "EDGE" in msg


def test_message_header_contains_filter_badge():
    """В шапке сообщения видна пометка про активный фильтр."""
    rows = [_row(ticker="BTC", vol_m=2.0)]
    msg = format_daily_report(_meta(setups_count=1), rows)
    assert "vol ≥ $1M" in msg or "vol ≥ $1.0M" in msg


def test_message_footer_explains_filter_and_capital():
    """В футере: фильтр + капитал + где смотреть полный список."""
    rows = [_row(ticker="BTC", vol_m=2.0)]
    msg = format_daily_report(_meta(setups_count=1), rows)
    assert "vol ≥ $1M" in msg
    # Капитал и упоминание Web UI
    assert "5,000" in msg or "5000" in msg
    assert "Web UI" in msg


def test_setups_count_shows_pre_and_post_filter():
    """В шапке: 'setups: N (after filter: M)'."""
    rows = [
        _row(ticker="A", vol_m=2.0),
        _row(ticker="B", vol_m=0.5),  # будет отфильтрован
        _row(ticker="C", vol_m=3.0),
    ]
    msg = format_daily_report(_meta(setups_count=3), rows)
    assert "setups: 3 (after filter: 2)" in msg


def test_zero_after_filter_shows_explicit_message():
    """Если после фильтра пусто — отдельное сообщение, не пустая таблица."""
    rows = [
        _row(ticker="LOW1", vol_m=0.1),
        _row(ticker="LOW2", vol_m=0.5),
    ]
    msg = format_daily_report(_meta(setups_count=2), rows)
    assert "Нет связок проходящих фильтр" in msg
    assert "vol ≥ $1M" in msg
    # Низковолюмные тикеры не должны попасть в сообщение
    assert "LOW1" not in msg
    assert "LOW2" not in msg


def test_min_volume_none_disables_filter():
    """min_volume_usd=None — фильтр выключен, всё в топе, нет пометки про фильтр."""
    rows = [
        _row(ticker="LOW", spread=200.0, vol_m=0.1),
        _row(ticker="HIGH", spread=100.0, vol_m=5.0),
    ]
    msg = format_daily_report(_meta(setups_count=2), rows, min_volume_usd=None)
    assert "LOW" in msg
    assert "HIGH" in msg
    # Без фильтра нет пометки
    assert "vol ≥" not in msg
    assert "after filter" not in msg


def test_custom_min_volume_threshold():
    """Можно передать другой порог, например $5M для строгой выборки."""
    rows = [
        _row(ticker="MEDIUM", spread=100.0, vol_m=2.0),  # >$1M но <$5M
        _row(ticker="LARGE", spread=80.0, vol_m=10.0),
    ]
    msg = format_daily_report(_meta(setups_count=2), rows, min_volume_usd=5_000_000)
    assert "LARGE" in msg
    assert "MEDIUM" not in msg
    assert "vol ≥ $5M" in msg


def test_default_threshold_constant_is_one_million():
    """Документируем дефолтный порог как $1M ровно."""
    assert TELEGRAM_MIN_VOLUME_USD == 1_000_000.0
