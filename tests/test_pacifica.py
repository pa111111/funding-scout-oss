"""Unit tests for Pacifica connector via httpx.MockTransport.

Покрываем:
- Корректный парсинг (BTC + equity + commodity варианты)
- Skip mark_price <= 0
- Missing optional поля → None
- Malformed entry skipped
- success=false → ValueError
- Wrong shape → ValueError
- HTTP error propagates
"""

from __future__ import annotations

import httpx
import pytest

from funding_scout.connectors.pacifica import PacificaConnector


def _ok_response(items):
    return {"success": True, "data": items, "error": None, "code": None}


def _transport(payload):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == PacificaConnector.PRICES_PATH
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def _item(
    symbol="BTC",
    funding="0.00001",
    mark="79574",
    mid="79573.5",
    oracle="79601",
    open_interest="448.13",
    volume_24h="381350218.09",
):
    return {
        "symbol": symbol,
        "funding": funding,
        "mark": mark,
        "mid": mid,
        "next_funding": "0.00001",
        "oracle": oracle,
        "open_interest": open_interest,
        "volume_24h": volume_24h,
        "timestamp": 1778216694321,
        "yesterday_price": "81030",
    }


@pytest.mark.asyncio
async def test_parses_normal_response():
    items = [
        _item(symbol="BTC", funding="0.00001", mark="79574", oracle="79601"),
        _item(symbol="PLTR", funding="0.000015", mark="137.33", oracle="137.33", open_interest="614.9", volume_24h="22859"),
        _item(symbol="ETH", funding="-0.00001157", mark="2279.8", oracle="2280.09"),
    ]
    c = PacificaConnector(transport=_transport(_ok_response(items)))
    ticks = await c.fetch_snapshot()
    assert len(ticks) == 3

    by_ticker = {t.ticker: t for t in ticks}

    btc = by_ticker["BTC"]
    assert btc.venue == "pacifica"
    assert btc.funding_rate_1h == pytest.approx(0.00001)
    assert btc.mark_price == pytest.approx(79574)
    assert btc.index_price == pytest.approx(79601)
    assert btc.oi_long == pytest.approx(448.13)
    assert btc.oi_short is None
    assert btc.volume_24h == pytest.approx(381350218.09)
    assert btc.raw["symbol"] == "BTC"

    pltr = by_ticker["PLTR"]
    assert pltr.ticker == "PLTR"  # equity perp passes through unchanged

    eth = by_ticker["ETH"]
    assert eth.funding_rate_1h == pytest.approx(-0.00001157)


@pytest.mark.asyncio
async def test_parses_equity_and_commodity_tickers():
    """Equity (PLTR/NVDA/HOOD) и commodity (XAU/SP500) - валидные тикеры."""
    items = [
        _item(symbol="NVDA", mark="212.85", oracle="212.72"),
        _item(symbol="TSLA", mark="412.12"),
        _item(symbol="HOOD", mark="50.5"),
        _item(symbol="XAU", mark="2600"),
        _item(symbol="SP500", mark="6000"),
        _item(symbol="EURUSD", mark="1.05"),
    ]
    c = PacificaConnector(transport=_transport(_ok_response(items)))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"NVDA", "TSLA", "HOOD", "XAU", "SP500", "EURUSD"}


@pytest.mark.asyncio
async def test_skips_zero_mark_price():
    items = [
        _item(symbol="BTC", mark="79574"),
        _item(symbol="UNTRADED", mark="0"),
        _item(symbol="ETH", mark="2279"),
    ]
    c = PacificaConnector(transport=_transport(_ok_response(items)))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"BTC", "ETH"}


@pytest.mark.asyncio
async def test_skips_negative_mark_price():
    items = [
        _item(symbol="BTC", mark="79574"),
        _item(symbol="WEIRD", mark="-1"),
    ]
    c = PacificaConnector(transport=_transport(_ok_response(items)))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"BTC"}


@pytest.mark.asyncio
async def test_handles_missing_optional_fields():
    items = [
        {
            "symbol": "BTC",
            "funding": "0.00001",
            "mark": "79574",
            # oracle/open_interest/volume_24h отсутствуют
        }
    ]
    c = PacificaConnector(transport=_transport(_ok_response(items)))
    ticks = await c.fetch_snapshot()
    assert len(ticks) == 1
    t = ticks[0]
    assert t.index_price is None
    assert t.oi_long is None
    assert t.volume_24h is None


@pytest.mark.asyncio
async def test_skips_entry_missing_required_fields():
    items = [
        _item(symbol="GOOD"),
        {"symbol": "MISSING_FUNDING", "mark": "100"},  # no funding
        {"symbol": "MISSING_MARK", "funding": "0.0"},  # no mark
        _item(symbol="ALSOGOOD"),
    ]
    c = PacificaConnector(transport=_transport(_ok_response(items)))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"GOOD", "ALSOGOOD"}


@pytest.mark.asyncio
async def test_skips_invalid_number():
    items = [
        _item(symbol="GOOD", funding="0.0001"),
        _item(symbol="BAD", funding="not-a-number"),
    ]
    c = PacificaConnector(transport=_transport(_ok_response(items)))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"GOOD"}


@pytest.mark.asyncio
async def test_skips_non_dict_item():
    payload = _ok_response(
        [_item(symbol="GOOD"), "not a dict", _item(symbol="ALSOGOOD")]
    )
    c = PacificaConnector(transport=_transport(payload))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"GOOD", "ALSOGOOD"}


@pytest.mark.asyncio
async def test_raises_on_success_false():
    payload = {"success": False, "data": [], "error": "rate limited", "code": 429}
    c = PacificaConnector(transport=_transport(payload))
    with pytest.raises(ValueError, match="success=false"):
        await c.fetch_snapshot()


@pytest.mark.asyncio
async def test_raises_on_missing_data_key():
    payload = {"success": True, "result": []}  # wrong key
    c = PacificaConnector(transport=_transport(payload))
    with pytest.raises(ValueError, match="Unexpected Pacifica response shape"):
        await c.fetch_snapshot()


@pytest.mark.asyncio
async def test_raises_on_data_not_list():
    payload = {"success": True, "data": {"BTC": {"mark": "79574"}}}
    c = PacificaConnector(transport=_transport(payload))
    with pytest.raises(ValueError, match="data should be list"):
        await c.fetch_snapshot()


@pytest.mark.asyncio
async def test_raises_on_top_level_not_dict():
    payload = ["array", "not", "dict"]
    c = PacificaConnector(transport=_transport(payload))
    with pytest.raises(ValueError, match="Unexpected Pacifica response shape"):
        await c.fetch_snapshot()


@pytest.mark.asyncio
async def test_http_error_propagates():
    def handler(request):
        return httpx.Response(503, json={"error": "service unavailable"})

    c = PacificaConnector(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await c.fetch_snapshot()
