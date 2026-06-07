"""Тесты read-only JSON-API (web/app.py) — машиночитаемая витрина для Hermes."""

from __future__ import annotations

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from funding_scout.storage import SessionLocal
from funding_scout.storage.models import FundingSnapshot
from funding_scout.web.app import create_app


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


def _client():
    app = create_app(capital_usd=5000)
    return app.server.test_client()


def test_api_setups_empty_db_returns_envelope():
    """Пустая БД → валидный конверт, setups=[], не 500."""
    resp = _client().get("/api/setups")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["capital_usd"] == 5000
    assert "computed_at" in body
    assert body["meta"]["setups_count"] == 0
    assert body["setups"] == []


def test_api_setups_returns_candidate_with_id():
    """Связка попадает в JSON с candidate_id и ключевыми полями контракта."""
    ts = 1_700_000_000
    # одна и та же монета на двух венью с разным фандингом → cross-dex setup
    _ins(ts, "hyperliquid", "BTC", 0.0005)
    _ins(ts, "lighter", "BTC", -0.0005)

    body = _client().get("/api/setups").get_json()
    assert body["meta"]["setups_count"] >= 1

    s = body["setups"][0]
    # контракт §4.1: id, ноги, spread, funding APR, EV, friction, capacity
    for field in (
        "candidate_id",
        "ticker",
        "long_venue",
        "short_venue",
        "spread_apr_pct",
        "long_funding_apr_pct",
        "short_funding_apr_pct",
        "base_ev_usd_per_day",
        "round_trip_cost_pct",
        "min_volume_24h_m_usd",
    ):
        assert field in s
    assert s["candidate_id"] == f"{s['ticker']}:{s['long_venue']}:{s['short_venue']}"


def test_api_setups_is_json_serializable_no_inf_nan():
    """Ответ — валидный JSON (inf/nan уже вычищены в setup_to_row)."""
    ts = 1_700_000_000
    _ins(ts, "hyperliquid", "ETH", 0.0003)
    _ins(ts, "lighter", "ETH", -0.0003)
    resp = _client().get("/api/setups")
    # get_json() бросит, если тело не строгий JSON
    assert resp.get_json() is not None
