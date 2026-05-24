"""Unit-тесты EV-движка. Чисто арифметика — без БД."""

from __future__ import annotations

import math

import pytest

from funding_scout.ev.base import HOURS_PER_YEAR, compute_setup_ev
from funding_scout.ev.costs import (
    DEFAULT_ROUND_TRIP_COST_PCT,
    ROUND_TRIP_COST_PCT,
    round_trip_cost_pair,
)


# === ev/costs.py ===


def test_known_venue_cost():
    assert round_trip_cost_pair("hyperliquid", "lighter") == pytest.approx(0.06)
    assert round_trip_cost_pair("lighter", "hyperliquid") == pytest.approx(0.06)


def test_unknown_venue_falls_back_to_default():
    cost = round_trip_cost_pair("hyperliquid", "unknown-dex-xyz")
    assert cost == pytest.approx(0.06 + DEFAULT_ROUND_TRIP_COST_PCT)


def test_two_unknown_venues():
    cost = round_trip_cost_pair("foo", "bar")
    assert cost == pytest.approx(2 * DEFAULT_ROUND_TRIP_COST_PCT)


def test_hip3_builder_dex_inherits_hl_cost():
    """hyperliquid-xyz (HIP-3 builder-dex) наследует base HL cost, не pessimistic default."""
    # builder-dex ↔ lighter: 0.06 (HL base) + 0.00 (lighter) = 0.06, не 0.10+0.00
    assert round_trip_cost_pair("hyperliquid-xyz", "lighter") == pytest.approx(0.06)
    # builder-dex ↔ основной HL
    assert round_trip_cost_pair("hyperliquid-xyz", "hyperliquid") == pytest.approx(0.12)
    # любой builder-dex суффикс
    assert round_trip_cost_pair("hyperliquid-abc", "lighter") == pytest.approx(0.06)


def test_known_venues_dict_includes_hl_and_lighter():
    """Sanity: критичные коннекторы покрыты."""
    assert "hyperliquid" in ROUND_TRIP_COST_PCT
    assert "lighter" in ROUND_TRIP_COST_PCT


# === ev/base.py — арифметика ===


def test_apr_conversion_basic():
    """Hourly 0.0001 → 0.0001 * 8760 = 0.876 (87.6% APR)."""
    ev = compute_setup_ev(
        funding_rate_long_1h=0.0,
        funding_rate_short_1h=0.0001,
        long_venue="lighter",
        short_venue="hyperliquid",
    )
    assert ev.funding_long_apr == 0.0
    assert ev.funding_short_apr == pytest.approx(0.876)
    assert ev.spread_apr == pytest.approx(0.876)


def test_spread_apr_correct_when_long_is_negative():
    """long_rate=-0.0001 (платят лонгу), short_rate=+0.0002 (платят шорту).
    Spread = 0.0003/h × 8760 = 2.628 (262.8% APR)."""
    ev = compute_setup_ev(
        funding_rate_long_1h=-0.0001,
        funding_rate_short_1h=0.0002,
        long_venue="lighter",
        short_venue="hyperliquid",
    )
    assert ev.funding_long_apr == pytest.approx(-0.876)
    assert ev.funding_short_apr == pytest.approx(1.752)
    assert ev.spread_apr == pytest.approx(2.628)


def test_base_ev_per_dollar_per_day():
    """Spread 100% APR → $1 капитала зарабатывает 1.0/365 = $0.00274/день."""
    ev = compute_setup_ev(
        funding_rate_long_1h=0.0,
        funding_rate_short_1h=1.0 / HOURS_PER_YEAR,  # точно 100% APR
        long_venue="lighter",
        short_venue="hyperliquid",
    )
    assert ev.spread_apr == pytest.approx(1.0)
    assert ev.base_ev_per_dollar_per_day == pytest.approx(1.0 / 365)


def test_zero_spread_gives_inf_min_holding():
    ev = compute_setup_ev(
        funding_rate_long_1h=0.0001,
        funding_rate_short_1h=0.0001,
        long_venue="lighter",
        short_venue="hyperliquid",
    )
    assert ev.spread_apr == 0.0
    assert math.isinf(ev.min_profitable_hours)


def test_negative_spread_gives_inf_min_holding():
    """Если детектор облажался и передал перевернутые ставки — мы не падаем,
    просто min_profitable = inf (никогда не окупится)."""
    ev = compute_setup_ev(
        funding_rate_long_1h=0.0002,
        funding_rate_short_1h=0.0001,
        long_venue="lighter",
        short_venue="hyperliquid",
    )
    assert ev.spread_apr < 0
    assert math.isinf(ev.min_profitable_hours)


def test_min_holding_with_lighter_hl_realistic():
    """Реальный пример: spread 100% APR на HL+Lighter (round-trip = 0.06%).
    spread/hour = 1.0/8760 = 0.0001142 (~0.01142%/h).
    min_hours = 0.06%/0.01142% = ~5.26 часа."""
    ev = compute_setup_ev(
        funding_rate_long_1h=0.0,
        funding_rate_short_1h=1.0 / HOURS_PER_YEAR,
        long_venue="lighter",
        short_venue="hyperliquid",
    )
    expected_hours = 0.06 / (1.0 / HOURS_PER_YEAR * 100)
    assert ev.min_profitable_hours == pytest.approx(expected_hours, rel=1e-6)


def test_round_trip_cost_threaded_through():
    """Проверяем что round_trip из ev.costs действительно используется."""
    ev = compute_setup_ev(
        funding_rate_long_1h=0.0,
        funding_rate_short_1h=0.0001,
        long_venue="hyperliquid",
        short_venue="lighter",
    )
    assert ev.round_trip_cost_pct == pytest.approx(0.06)


def test_unknown_venues_use_pessimistic_default():
    ev = compute_setup_ev(
        funding_rate_long_1h=0.0,
        funding_rate_short_1h=0.0001,
        long_venue="brand-new-dex",
        short_venue="another-new-dex",
    )
    assert ev.round_trip_cost_pct == pytest.approx(2 * DEFAULT_ROUND_TRIP_COST_PCT)
