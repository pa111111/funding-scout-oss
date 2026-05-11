"""Тесты cross-DEX same-ticker детектора. Используем реальную in-memory БД (через conftest)."""

from __future__ import annotations

import pytest
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from funding_scout.detectors.cross_dex_same_ticker import CrossDexSameTickerDetector
from funding_scout.storage import SessionLocal
from funding_scout.storage.models import FundingSnapshot


def _insert(ts, venue, ticker, funding_rate_1h, mark_price=100.0, volume_24h=1_000_000):
    with SessionLocal() as s:
        s.execute(
            sqlite_insert(FundingSnapshot)
            .values(
                ts=ts,
                venue=venue,
                ticker=ticker,
                funding_rate_1h=funding_rate_1h,
                mark_price=mark_price,
                index_price=None,
                oi_long=None,
                oi_short=None,
                volume_24h=volume_24h,
                raw={},
            )
            .prefix_with("OR IGNORE")
        )
        s.commit()


def test_no_snapshots_returns_empty():
    d = CrossDexSameTickerDetector()
    with SessionLocal() as s:
        ts, setups = d.detect_latest(s)
    assert ts is None
    assert setups == []


def test_single_venue_ticker_skipped():
    """BTC только на одной бирже → не пара → нет setup'а."""
    _insert(1000, "hyperliquid", "BTC", 0.0001)

    d = CrossDexSameTickerDetector()
    with SessionLocal() as s:
        ts, setups = d.detect_latest(s)

    assert ts == 1000
    assert setups == []


def test_two_venues_one_ticker_creates_one_setup():
    _insert(1000, "hyperliquid", "BTC", 0.0001, mark_price=70000)
    _insert(1000, "lighter", "BTC", -0.0001, mark_price=70010)

    d = CrossDexSameTickerDetector()
    with SessionLocal() as s:
        ts, setups = d.detect_latest(s)

    assert len(setups) == 1
    s = setups[0]
    assert s.ticker == "BTC"
    assert s.type == "cross-dex-same-ticker"
    # rate_lighter < rate_hl → лонгуем Lighter, шортим HL
    assert s.long_venue == "lighter"
    assert s.short_venue == "hyperliquid"
    # spread = 0.0002/h × 8760 = 1.752 → 175.2% APR
    assert s.spread_apr_pct == pytest.approx(175.2, rel=1e-3)
    assert s.long_funding_apr_pct == pytest.approx(-87.6, rel=1e-3)
    assert s.short_funding_apr_pct == pytest.approx(87.6, rel=1e-3)


def test_long_short_orientation_uses_lower_funding_for_long():
    """Контракт: лонгуем там где funding меньше, шортим где больше."""
    _insert(1000, "hyperliquid", "ETH", 0.0005)  # higher → short
    _insert(1000, "lighter", "ETH", 0.0001)       # lower → long

    d = CrossDexSameTickerDetector()
    with SessionLocal() as s:
        _, setups = d.detect_latest(s)

    assert len(setups) == 1
    assert setups[0].long_venue == "lighter"
    assert setups[0].short_venue == "hyperliquid"
    assert setups[0].spread_apr_pct > 0


def test_detector_emits_even_when_spread_negative_or_zero():
    """Парадигма: эмитим всё. spread 0 — нормально, юзер сам сортирует."""
    _insert(1000, "hyperliquid", "BTC", 0.0001)
    _insert(1000, "lighter", "BTC", 0.0001)  # одинаковый funding → spread 0

    d = CrossDexSameTickerDetector()
    with SessionLocal() as s:
        _, setups = d.detect_latest(s)

    assert len(setups) == 1
    assert setups[0].spread_apr_pct == pytest.approx(0.0)


def test_three_venues_makes_three_pairs():
    """Для одного тикера на 3 биржах: C(3,2)=3 пары."""
    _insert(1000, "hyperliquid", "BTC", 0.0001)
    _insert(1000, "lighter", "BTC", 0.00005)
    _insert(1000, "edgex", "BTC", 0.00015)  # synthetic third venue

    d = CrossDexSameTickerDetector()
    with SessionLocal() as s:
        _, setups = d.detect_latest(s)

    assert len(setups) == 3
    pairs = {(s.long_venue, s.short_venue) for s in setups}
    # каждая пара уникальна, длинная нога = с меньшим funding
    assert pairs == {
        ("lighter", "hyperliquid"),
        ("lighter", "edgex"),
        ("hyperliquid", "edgex"),
    }


def test_skips_pair_with_zero_or_negative_price():
    """Если на одной из ног mark_price <= 0 — связку не строим."""
    _insert(1000, "hyperliquid", "BTC", 0.0001, mark_price=70000)
    _insert(1000, "lighter", "BTC", -0.0001, mark_price=0.0)

    d = CrossDexSameTickerDetector()
    with SessionLocal() as s:
        _, setups = d.detect_latest(s)

    assert setups == []


def test_uses_only_latest_snapshot_ts():
    """Старые ts игнорируются — детектор смотрит только на самый свежий снапшот."""
    _insert(900, "hyperliquid", "BTC", 0.0001)
    _insert(900, "lighter", "BTC", -0.0001)
    _insert(1000, "hyperliquid", "BTC", 0.0002)
    _insert(1000, "lighter", "BTC", 0.0)

    d = CrossDexSameTickerDetector()
    with SessionLocal() as s:
        ts, setups = d.detect_latest(s)

    assert ts == 1000
    assert len(setups) == 1
    # spread = 0.0002 * 8760 = 1.752 → 175.2%
    assert setups[0].spread_apr_pct == pytest.approx(175.2, rel=1e-3)


def test_price_spread_pct_orientation():
    """price_spread_pct = (long - short) / short × 100."""
    _insert(1000, "hyperliquid", "BTC", 0.0001, mark_price=70000)
    _insert(1000, "lighter", "BTC", -0.0001, mark_price=70700)
    # rate_lighter < rate_hl → long=lighter (70700), short=hl (70000)
    # price_spread = (70700-70000)/70000 × 100 = 1.0%

    d = CrossDexSameTickerDetector()
    with SessionLocal() as s:
        _, setups = d.detect_latest(s)

    assert len(setups) == 1
    assert setups[0].long_mark_price == pytest.approx(70700)
    assert setups[0].short_mark_price == pytest.approx(70000)
    assert setups[0].price_spread_pct == pytest.approx(1.0, rel=1e-6)


def test_min_volume_with_one_none_falls_to_none():
    """Если хоть один venue не отдал volume — min_volume_24h_usd = None
    (не имитируем нулём, чтобы пользователь видел что данных нет)."""
    _insert(1000, "hyperliquid", "BTC", 0.0001, volume_24h=1_000_000)
    _insert(1000, "lighter", "BTC", -0.0001, volume_24h=None)

    d = CrossDexSameTickerDetector()
    with SessionLocal() as s:
        _, setups = d.detect_latest(s)

    assert setups[0].min_volume_24h_usd is None


def test_min_volume_uses_smaller():
    _insert(1000, "hyperliquid", "BTC", 0.0001, volume_24h=2_000_000)
    _insert(1000, "lighter", "BTC", -0.0001, volume_24h=500_000)

    d = CrossDexSameTickerDetector()
    with SessionLocal() as s:
        _, setups = d.detect_latest(s)

    assert setups[0].min_volume_24h_usd == 500_000


def test_round_trip_cost_threaded_for_known_venues():
    _insert(1000, "hyperliquid", "BTC", 0.0001)
    _insert(1000, "lighter", "BTC", -0.0001)

    d = CrossDexSameTickerDetector()
    with SessionLocal() as s:
        _, setups = d.detect_latest(s)

    assert setups[0].round_trip_cost_pct == pytest.approx(0.06)


def test_multi_ticker_isolated():
    """Тикеры обрабатываются независимо."""
    _insert(1000, "hyperliquid", "BTC", 0.0001)
    _insert(1000, "lighter", "BTC", -0.0001)
    _insert(1000, "hyperliquid", "ETH", 0.0002)
    _insert(1000, "lighter", "ETH", 0.0001)

    d = CrossDexSameTickerDetector()
    with SessionLocal() as s:
        _, setups = d.detect_latest(s)

    by_ticker = {s.ticker: s for s in setups}
    assert {"BTC", "ETH"} == set(by_ticker)
    assert by_ticker["BTC"].spread_apr_pct == pytest.approx(175.2, rel=1e-3)
    assert by_ticker["ETH"].spread_apr_pct == pytest.approx(87.6, rel=1e-3)
