"""Тесты survival/service.py — оркестрация history→окна→KM→оценка. Seeded sqlite."""

from __future__ import annotations

import pytest
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from funding_scout.detectors.base import Setup
from funding_scout.storage import SessionLocal
from funding_scout.storage.models import FundingSnapshot
from funding_scout.survival.service import (
    compute_survival_for_setups,
    reset_survival_cache,
)

STEP = 3600
START = 100_000


def _ins(ts, venue, ticker, rate, mark=100.0, vol=1_000_000):
    with SessionLocal() as s:
        s.execute(
            sqlite_insert(FundingSnapshot)
            .values(
                ts=ts, venue=venue, ticker=ticker, funding_rate_1h=rate,
                mark_price=mark, index_price=None, oi_long=None, oi_short=None,
                volume_24h=vol, raw={},
            )
            .prefix_with("OR IGNORE")
        )
        s.commit()


def _seed(flags, ticker="BTC", lv="lighter", sv="hyperliquid", start=START, step=STEP):
    """Засеять часовую историю по флагам: True = spread выше 30%, False = ниже.

    above: long=0, short=0.0001 → spread = 87.6% (> 30). below: short=0 → spread 0.
    Обе ноги присутствуют всегда → без None-дыр. Возвращает latest_ts.
    """
    last_ts = start
    for i, above in enumerate(flags):
        ts = start + i * step
        _ins(ts, lv, ticker, 0.0)
        _ins(ts, sv, ticker, 0.0001 if above else 0.0)
        last_ts = ts
    return last_ts


def _setup(ticker="BTC", lv="lighter", sv="hyperliquid", min_hold=10.0) -> Setup:
    return Setup(
        type="cross-dex-same-ticker", ticker=ticker, long_venue=lv, short_venue=sv,
        spread_apr_pct=87.6, base_ev_per_dollar_per_day=0.0024,
        long_funding_apr_pct=0.0, short_funding_apr_pct=87.6,
        round_trip_cost_pct=0.06, min_profitable_hours=min_hold,
        long_mark_price=100.0, short_mark_price=100.0, price_spread_pct=0.0,
        min_volume_24h_usd=1e6, long_volume_24h_usd=1e6, short_volume_24h_usd=1e6,
        snapshot_ts=START,
    )


def _key(s: Setup):
    return (s.ticker, s.long_venue, s.short_venue)


# === базовая оркестрация ===


def test_empty_setups_returns_empty():
    with SessionLocal() as s:
        assert compute_survival_for_setups(s, [], START, use_cache=False) == {}


def test_no_history_yields_none_estimate():
    setup = _setup()
    with SessionLocal() as s:
        res = compute_survival_for_setups(s, [setup], START, use_cache=False)
    est = res[_key(setup)]
    assert est.sample_size == 0
    assert est.confidence == "none"
    assert est.median_total_lifetime_h is None
    assert est.median_remaining_h is None
    assert est.current_age_h == 0


def test_pair_path_known_numbers():
    """flags = F,T,T,F,T,T,T,F,T,T → окна dur 2,3,2(open).
    deaths={2,3}, sample=2. current_age=2.
    KM: S(2)=1, S(3)=2/3, S(4)=0 → median_lifetime=4, median_remaining(age2)=2."""
    flags = [False, True, True, False, True, True, True, False, True, True]
    latest = _seed(flags)
    setup = _setup(min_hold=1.0)
    with SessionLocal() as s:
        res = compute_survival_for_setups(
            s, [setup], latest, min_windows=2, use_cache=False
        )
    est = res[_key(setup)]
    assert est.pooled is False
    assert est.sample_size == 2
    assert est.current_age_h == 2
    assert est.median_total_lifetime_h == pytest.approx(4.0)
    assert est.median_remaining_h == pytest.approx(2.0)
    assert est.confidence == "medium"  # 2 < 12 и не pooled


def test_current_age_zero_when_no_open_window():
    """Серия заканчивается ниже порога → нет активного окна → age 0, remaining None."""
    flags = [False, True, True, False]
    latest = _seed(flags)
    setup = _setup(min_hold=1.0)
    with SessionLocal() as s:
        res = compute_survival_for_setups(
            s, [setup], latest, min_windows=1, use_cache=False
        )
    est = res[_key(setup)]
    assert est.current_age_h == 0
    assert est.median_remaining_h is None
    assert est.p_survive_min_hold is None
    assert est.curve == []


def test_left_truncated_first_window_excluded():
    """flags = T,T,F → единственное окно начинается на левом крае (left-truncated)
    и выбрасывается → sample 0, оценка пустая."""
    flags = [True, True, False]
    latest = _seed(flags)
    setup = _setup()
    with SessionLocal() as s:
        res = compute_survival_for_setups(
            s, [setup], latest, min_windows=1, use_cache=False
        )
    est = res[_key(setup)]
    assert est.sample_size == 0
    assert est.confidence == "none"


def test_pooling_when_sample_below_min():
    """sample=2 < min_windows(5) → pooled fallback, confidence low."""
    flags = [False, True, True, False, True, True, True, False]
    latest = _seed(flags)
    setup = _setup()
    with SessionLocal() as s:
        res = compute_survival_for_setups(
            s, [setup], latest, min_windows=5, use_cache=False
        )
    est = res[_key(setup)]
    assert est.pooled is True
    assert est.confidence == "low"


def test_p_survive_min_hold_present_with_active_window():
    """При активном окне и конечном min_hold p_survive_min_hold — число в [0,1]."""
    flags = [False, True, True, False, True, True, True, False, True, True]
    latest = _seed(flags)
    setup = _setup(min_hold=2.0)
    with SessionLocal() as s:
        res = compute_survival_for_setups(
            s, [setup], latest, min_windows=2, use_cache=False
        )
    est = res[_key(setup)]
    assert est.p_survive_min_hold is not None
    assert 0.0 <= est.p_survive_min_hold <= 1.0


def test_curve_horizon_and_monotonic():
    """curve — список (k, P), k от 1, P невозрастающая по k."""
    flags = [False, True, True, False, True, True, True, False, True, True]
    latest = _seed(flags)
    setup = _setup(min_hold=1.0)
    with SessionLocal() as s:
        res = compute_survival_for_setups(
            s, [setup], latest, min_windows=2, use_cache=False
        )
    curve = res[_key(setup)].curve
    assert curve  # непустая
    ks = [k for k, _p in curve]
    ps = [p for _k, p in curve]
    assert ks[0] == 1
    assert all(a >= b - 1e-12 for a, b in zip(ps, ps[1:], strict=False))


# === кэш ===


def test_cache_returns_same_object_for_same_ts():
    flags = [False, True, True, False]
    latest = _seed(flags)
    setup = _setup()
    with SessionLocal() as s:
        first = compute_survival_for_setups(s, [setup], latest)
        second = compute_survival_for_setups(s, [setup], latest)
    assert first is second  # кэш-хит по latest_ts


def test_use_cache_false_recomputes():
    flags = [False, True, True, False]
    latest = _seed(flags)
    setup = _setup()
    with SessionLocal() as s:
        cached = compute_survival_for_setups(s, [setup], latest, use_cache=True)
        fresh = compute_survival_for_setups(s, [setup], latest, use_cache=False)
    assert fresh is not cached


def test_reset_cache_forces_recompute():
    flags = [False, True, True, False]
    latest = _seed(flags)
    setup = _setup()
    with SessionLocal() as s:
        first = compute_survival_for_setups(s, [setup], latest)
        reset_survival_cache()
        second = compute_survival_for_setups(s, [setup], latest)
    assert first is not second


# === интеграция в get_latest_setups ===


def test_get_latest_setups_includes_survival_fields():
    from funding_scout.web.data import get_latest_setups

    # один снапшот с парой BTC на двух venue
    _ins(START, "lighter", "BTC", -0.0001)
    _ins(START, "hyperliquid", "BTC", 0.0001)
    _, rows = get_latest_setups()
    assert rows
    row = rows[0]
    for field in (
        "survival_median_remaining_h",
        "survival_median_lifetime_h",
        "survival_confidence",
        "survival_sample_size",
        "survival_pooled",
        "survival_sparkline",
    ):
        assert field in row
