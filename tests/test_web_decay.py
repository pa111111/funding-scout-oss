"""Тесты сигнала decay/staleness (concept §5.4).

Покрывают чистую функцию `decay_from_history`, per-candidate lookup
`get_candidate_decay` (работает и для исчезнувшей связки) и то, что decay-поля
доезжают до `/api/setups` и `/api/setups/<candidate_id>`.
"""

from __future__ import annotations

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from funding_scout.storage import SessionLocal
from funding_scout.storage.models import FundingSnapshot
from funding_scout.web.app import create_app
from funding_scout.web.data import decay_from_history, get_candidate_decay


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
    return create_app(capital_usd=5000).server.test_client()


# === decay_from_history — чистая функция ===


def test_decay_empty_history_is_unknown():
    assert decay_from_history([])["staleness"] == "unknown"
    assert decay_from_history([None, None])["staleness"] == "unknown"


def test_decay_flat_peak_is_fresh():
    d = decay_from_history([100.0, 100.0, 100.0])
    assert d["staleness"] == "fresh"
    assert d["peak_spread_apr_pct"] == 100.0
    assert d["decay_from_peak_pct"] == 0.0
    assert d["hours_since_peak"] == 0


def test_decay_25_to_50_is_cooling():
    # 100 → 70 = упал на 30% от пика
    d = decay_from_history([100.0, 100.0, 70.0])
    assert d["staleness"] == "cooling"
    assert d["decay_from_peak_pct"] == 30.0


def test_decay_over_50_is_stale():
    # 100 → 40 = упал на 60%, при этом 40 ≥ порога торгуемости (30)
    d = decay_from_history([100.0, 100.0, 40.0])
    assert d["staleness"] == "stale"
    assert d["decay_from_peak_pct"] == 60.0


def test_decay_below_tradeable_threshold_is_stale():
    # текущий спред 20 < 30% порога, пик был выше → stale даже без 50%-просадки
    d = decay_from_history([35.0, 30.0, 20.0])
    assert d["staleness"] == "stale"


def test_decay_missing_latest_point_is_gone():
    # связку перестали детектить (последняя точка отсутствует) → gone
    d = decay_from_history([100.0, 100.0, None])
    assert d["staleness"] == "gone"


def test_decay_negative_current_is_gone():
    # спред перевернулся (≤ 0) → закрывать
    d = decay_from_history([100.0, 50.0, -5.0])
    assert d["staleness"] == "gone"


def test_decay_hours_since_peak_uses_last_peak_occurrence():
    # пик 100 в индексах 1 и 2, последний — idx2; конец окна idx3 → 1 час с пика
    d = decay_from_history([50.0, 100.0, 100.0, 60.0])
    assert d["hours_since_peak"] == 1
    assert d["decay_from_peak_pct"] == 40.0


# === /api/setups — decay-поля на каждой связке ===


def test_api_setups_carries_decay_fields():
    ts = 1_700_000_000
    _ins(ts, "hyperliquid", "BTC", 0.0005)
    _ins(ts, "lighter", "BTC", -0.0005)

    s = _client().get("/api/setups").get_json()["setups"][0]
    for field in ("peak_spread_apr_pct", "hours_since_peak", "decay_from_peak_pct", "staleness"):
        assert field in s
    # один ts → история из одной точки = текущая, пик = текущий → fresh
    assert s["staleness"] == "fresh"
    assert s["decay_from_peak_pct"] == 0.0


# === /api/setups/<candidate_id> — per-candidate decay ===


def test_api_candidate_decay_present_and_decaying():
    t = 1_700_000_000
    # час назад спред был большой, сейчас схлопнулся → stale, present=true
    _ins(t - 3600, "hyperliquid", "BTC", 0.0010)
    _ins(t - 3600, "lighter", "BTC", -0.0010)
    _ins(t, "hyperliquid", "BTC", 0.0002)
    _ins(t, "lighter", "BTC", -0.0002)

    body = _client().get("/api/setups/BTC:lighter:hyperliquid").get_json()
    assert body["present"] is True
    assert body["staleness"] == "stale"
    assert body["peak_spread_apr_pct"] > body["current_spread_apr_pct"]
    assert "computed_at" in body


def test_api_candidate_decay_gone_when_leg_disappears():
    t = 1_700_000_000
    # связка была час назад, сейчас одной ноги нет → gone, present=false
    _ins(t - 3600, "hyperliquid", "BTC", 0.0010)
    _ins(t - 3600, "lighter", "BTC", -0.0010)
    _ins(t, "hyperliquid", "BTC", 0.0002)  # lighter отвалился

    body = _client().get("/api/setups/BTC:lighter:hyperliquid").get_json()
    assert body["present"] is False
    assert body["staleness"] == "gone"


def test_api_candidate_decay_invalid_id_is_400():
    resp = _client().get("/api/setups/NOTACANDIDATE")
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_api_candidate_decay_empty_db_is_unknown():
    body = _client().get("/api/setups/BTC:lighter:hyperliquid").get_json()
    assert body["present"] is False
    assert body["staleness"] == "unknown"
    assert body["snapshot_ts"] is None


def test_get_candidate_decay_rejects_malformed_id():
    assert get_candidate_decay("BTC:lighter") is None
    assert get_candidate_decay("BTC::hyperliquid") is None
    with SessionLocal() as s:
        assert get_candidate_decay("BTC:lighter:hyperliquid", session=s) is not None
