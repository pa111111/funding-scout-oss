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
    PREV_SNAPSHOT_MAX_LAG_SEC,
    SPARKLINE_BLOCKS,
    SPARKLINE_NONE_CHAR,
    compute_spread_deltas,
    compute_spread_history,
    find_prev_snapshot_ts,
    get_latest_setups,
    render_sparkline_blocks,
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
        "delta_spread_apr_pct_1h",
        "spread_sparkline",
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


def test_setup_to_row_default_delta_is_none():
    """setup_to_row сам не считает delta — её доставляет get_latest_setups."""
    row = setup_to_row(_setup())
    assert row["delta_spread_apr_pct_1h"] is None


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


# === find_prev_snapshot_ts ===


def test_find_prev_returns_max_ts_in_window():
    """Если есть несколько prev ts, берём максимальный (ближайший к latest)."""
    _ins(5000, "hyperliquid", "BTC", 0.0001)
    _ins(4000, "hyperliquid", "BTC", 0.0001)  # 1000s до latest, в окне
    _ins(3500, "hyperliquid", "BTC", 0.0001)  # глубже, тоже в окне 7200s
    _ins(9000, "hyperliquid", "BTC", 0.0001)  # latest

    with SessionLocal() as s:
        assert find_prev_snapshot_ts(s, 9000) == 5000


def test_find_prev_returns_none_when_no_history():
    """Свежий продакт — единственный снапшот без истории."""
    _ins(9000, "hyperliquid", "BTC", 0.0001)
    with SessionLocal() as s:
        assert find_prev_snapshot_ts(s, 9000) is None


def test_find_prev_ignores_too_old():
    """Если предыдущий снапшот старше окна 2h — игнорим (был долгий простой)."""
    _ins(9000, "hyperliquid", "BTC", 0.0001)  # latest
    _ins(1000, "hyperliquid", "BTC", 0.0001)  # 8000s раньше, > 7200 окна

    with SessionLocal() as s:
        assert find_prev_snapshot_ts(s, 9000) is None


def test_find_prev_does_not_pick_latest_itself():
    """Не должен вернуть сам latest_ts."""
    _ins(9000, "hyperliquid", "BTC", 0.0001)
    with SessionLocal() as s:
        # custom_window достаточно широкий — но всё равно не latest
        assert find_prev_snapshot_ts(s, 9000, max_lag_sec=999999) is None


# === compute_spread_deltas (чистая функция) ===


def test_compute_delta_matches_by_ticker_and_venues():
    latest = [_setup(spread_apr_pct=120.8)]
    prev = [_setup(spread_apr_pct=80.0)]
    deltas = compute_spread_deltas(latest, prev)
    key = ("BTC", "lighter", "hyperliquid")
    assert key in deltas
    assert deltas[key] == pytest.approx(40.8)


def test_compute_delta_skips_when_no_match_in_prev():
    """Если в prev нет такой связки — её нет в словаре дельт."""
    latest = [_setup(ticker="SOL")]
    prev = [_setup(ticker="BTC")]
    deltas = compute_spread_deltas(latest, prev)
    assert deltas == {}


def test_compute_delta_handles_direction_flip():
    """Если на prev long/short поменялись местами — это другой ключ, в deltas не попадёт.

    Это намеренно: Δ Spread показывает изменение того же DIRECTIONAL setup.
    Если знак funding на ноге перевернулся — это новый setup, надёжной delta нет.
    """
    latest = [_setup(long_venue="lighter", short_venue="hyperliquid")]
    prev = [_setup(long_venue="hyperliquid", short_venue="lighter")]
    deltas = compute_spread_deltas(latest, prev)
    assert deltas == {}


# === get_latest_setups: Δ Spread end-to-end ===


def test_delta_spread_computed_when_prev_snapshot_exists():
    """Главный happy-path: latest и prev снапшоты на 1h apart → delta = разница спредов."""
    # prev: spread = (0.0002 - (-0.0001)) × 8760 × 100 = 262.8%
    _ins(1000, "lighter", "BTC", -0.0001)
    _ins(1000, "hyperliquid", "BTC", 0.0002)
    # latest: spread = (0.0001 - (-0.0001)) × 8760 × 100 = 175.2%
    _ins(4600, "lighter", "BTC", -0.0001)
    _ins(4600, "hyperliquid", "BTC", 0.0001)

    _, rows = get_latest_setups()
    assert len(rows) == 1
    # Δ = 175.2 - 262.8 = -87.6 (окно схлопывается)
    assert rows[0]["delta_spread_apr_pct_1h"] == pytest.approx(-87.6, rel=1e-3)


def test_delta_spread_none_when_only_one_snapshot():
    """Свежий продакт — нет prev, delta = None у всех строк."""
    _ins(1000, "lighter", "BTC", -0.0001)
    _ins(1000, "hyperliquid", "BTC", 0.0001)

    _, rows = get_latest_setups()
    assert len(rows) == 1
    assert rows[0]["delta_spread_apr_pct_1h"] is None


def test_delta_spread_none_when_prev_too_old():
    """Если последний доступный prev > 2h назад — delta = None."""
    _ins(1000, "lighter", "BTC", -0.0001)
    _ins(1000, "hyperliquid", "BTC", 0.0001)
    # latest на 10000s позже — за пределами PREV_SNAPSHOT_MAX_LAG_SEC=7200
    _ins(11000, "lighter", "BTC", -0.0001)
    _ins(11000, "hyperliquid", "BTC", 0.0002)

    assert 11000 - 1000 > PREV_SNAPSHOT_MAX_LAG_SEC  # sanity
    _, rows = get_latest_setups()
    assert rows[0]["delta_spread_apr_pct_1h"] is None


def test_delta_spread_none_for_new_pair_not_in_prev():
    """Новый тикер появился только в latest — delta = None, остальные посчитаны."""
    # prev: только BTC
    _ins(1000, "lighter", "BTC", -0.0001)
    _ins(1000, "hyperliquid", "BTC", 0.0001)
    # latest: BTC и новый SOL
    _ins(4600, "lighter", "BTC", -0.0001)
    _ins(4600, "hyperliquid", "BTC", 0.0001)
    _ins(4600, "lighter", "SOL", -0.0002)
    _ins(4600, "hyperliquid", "SOL", 0.0001)

    _, rows = get_latest_setups()
    by_ticker = {r["ticker"]: r for r in rows}
    assert by_ticker["BTC"]["delta_spread_apr_pct_1h"] == pytest.approx(0.0, abs=1e-9)
    assert by_ticker["SOL"]["delta_spread_apr_pct_1h"] is None


# === render_sparkline_blocks (чистая функция) ===


def test_sparkline_empty_returns_empty():
    assert render_sparkline_blocks([]) == ""


def test_sparkline_all_none_returns_dots():
    out = render_sparkline_blocks([None, None, None])
    assert out == SPARKLINE_NONE_CHAR * 3


def test_sparkline_constant_uses_middle_level():
    """Если spread не меняется — все блоки одинаковые средние."""
    out = render_sparkline_blocks([100.0, 100.0, 100.0])
    expected_char = SPARKLINE_BLOCKS[len(SPARKLINE_BLOCKS) // 2]
    assert out == expected_char * 3


def test_sparkline_min_is_lowest_block_max_is_highest():
    """Минимум → ▁ (первый), максимум → █ (последний)."""
    out = render_sparkline_blocks([10.0, 50.0, 100.0])
    assert out[0] == SPARKLINE_BLOCKS[0]
    assert out[-1] == SPARKLINE_BLOCKS[-1]
    # Среднее значение — где-то посередине
    assert SPARKLINE_BLOCKS.index(out[1]) > 0
    assert SPARKLINE_BLOCKS.index(out[1]) < len(SPARKLINE_BLOCKS) - 1


def test_sparkline_none_renders_as_dot_in_middle():
    """None в середине ряда → · в строке, остальные нормализуются по valid."""
    out = render_sparkline_blocks([0.0, None, 100.0])
    assert len(out) == 3
    assert out[1] == SPARKLINE_NONE_CHAR
    assert out[0] == SPARKLINE_BLOCKS[0]
    assert out[2] == SPARKLINE_BLOCKS[-1]


def test_sparkline_monotonic_increasing_is_non_decreasing_chars():
    """Возрастающая серия → монотонно неубывающий sparkline."""
    out = render_sparkline_blocks([i * 1.0 for i in range(8)])
    indices = [SPARKLINE_BLOCKS.index(c) for c in out]
    assert all(indices[i] <= indices[i + 1] for i in range(len(indices) - 1))


# === compute_spread_history ===


def _setup_for_pair(ticker, long_venue, short_venue):
    """Минимальный Setup только с identity-полями, остальное по умолчанию."""
    return _setup(ticker=ticker, long_venue=long_venue, short_venue=short_venue)


def test_history_empty_setups_returns_empty():
    with SessionLocal() as s:
        assert compute_spread_history(s, [], 1000) == {}


def test_history_no_snapshots_in_window_returns_empty_series_per_pair():
    """Если в окне 24h нет snapshot'ов — каждой связке пустая серия."""
    setups = [_setup_for_pair("BTC", "lighter", "hyperliquid")]
    with SessionLocal() as s:
        result = compute_spread_history(s, setups, 1000, hours=24)
    assert result == {("BTC", "lighter", "hyperliquid"): []}


def test_history_returns_spread_series_over_three_snapshots():
    """3 snapshot'а подряд → series длиной 3, формула spread = (short-long)×8760×100."""
    # ts=1000: long=-0.0001, short=+0.0001 → spread = 0.0002 × 8760 × 100 = 175.2
    _ins(1000, "lighter", "BTC", -0.0001)
    _ins(1000, "hyperliquid", "BTC", 0.0001)
    # ts=4600: long=-0.00005, short=+0.0001 → spread = 0.00015 × 8760 × 100 = 131.4
    _ins(4600, "lighter", "BTC", -0.00005)
    _ins(4600, "hyperliquid", "BTC", 0.0001)
    # ts=8200: long=0.0, short=+0.0001 → spread = 0.0001 × 8760 × 100 = 87.6
    _ins(8200, "lighter", "BTC", 0.0)
    _ins(8200, "hyperliquid", "BTC", 0.0001)

    setups = [_setup_for_pair("BTC", "lighter", "hyperliquid")]
    with SessionLocal() as s:
        result = compute_spread_history(s, setups, 8200, hours=24)

    series = result[("BTC", "lighter", "hyperliquid")]
    assert len(series) == 3
    assert series[0] == pytest.approx(175.2, rel=1e-3)
    assert series[1] == pytest.approx(131.4, rel=1e-3)
    assert series[2] == pytest.approx(87.6, rel=1e-3)


def test_history_missing_leg_yields_none_in_that_slot():
    """Если на ts=4600 одна нога пропала — в этом slot'е None."""
    _ins(1000, "lighter", "BTC", -0.0001)
    _ins(1000, "hyperliquid", "BTC", 0.0001)
    # ts=4600: только lighter, hyperliquid не отдал
    _ins(4600, "lighter", "BTC", 0.0)
    _ins(8200, "lighter", "BTC", 0.0)
    _ins(8200, "hyperliquid", "BTC", 0.0001)

    setups = [_setup_for_pair("BTC", "lighter", "hyperliquid")]
    with SessionLocal() as s:
        result = compute_spread_history(s, setups, 8200, hours=24)

    series = result[("BTC", "lighter", "hyperliquid")]
    assert len(series) == 3
    assert series[0] is not None
    assert series[1] is None  # дыра
    assert series[2] is not None


def test_history_respects_hours_window():
    """ts старше latest_ts - hours*3600 не должен попасть в серию."""
    _ins(100, "lighter", "BTC", -0.0001)  # вне окна 24h, latest_ts=100000
    _ins(100, "hyperliquid", "BTC", 0.0001)
    _ins(50000, "lighter", "BTC", -0.0001)  # вне окна 24h (24×3600=86400, 100000-50000=50000 < 86400, ВНУТРИ)
    _ins(50000, "hyperliquid", "BTC", 0.0001)
    _ins(100000, "lighter", "BTC", -0.0001)
    _ins(100000, "hyperliquid", "BTC", 0.0001)

    setups = [_setup_for_pair("BTC", "lighter", "hyperliquid")]
    with SessionLocal() as s:
        result = compute_spread_history(s, setups, 100000, hours=24)

    # 100000 - 24*3600 = 13600, окно [13600, 100000], ts=100 ВНЕ, остальные ВНУТРИ
    series = result[("BTC", "lighter", "hyperliquid")]
    assert len(series) == 2  # ts=50000 и ts=100000


# === Sparkline end-to-end через get_latest_setups ===


def test_get_latest_setups_includes_sparkline_string():
    """Самый базовый sanity-check: spread_sparkline существует и не пустой при наличии истории."""
    _ins(1000, "lighter", "BTC", -0.0001)
    _ins(1000, "hyperliquid", "BTC", 0.0001)
    _ins(4600, "lighter", "BTC", -0.0001)
    _ins(4600, "hyperliquid", "BTC", 0.0001)

    _, rows = get_latest_setups()
    assert "spread_sparkline" in rows[0]
    assert len(rows[0]["spread_sparkline"]) == 2  # 2 ts в окне 24h


def test_get_latest_setups_sparkline_is_string_when_no_history():
    """С единственным snapshot'ом — sparkline = 1 символ (точка спреда)."""
    _ins(1000, "lighter", "BTC", -0.0001)
    _ins(1000, "hyperliquid", "BTC", 0.0001)
    _, rows = get_latest_setups()
    assert isinstance(rows[0]["spread_sparkline"], str)
    assert len(rows[0]["spread_sparkline"]) == 1
