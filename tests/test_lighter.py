"""Unit tests for Lighter connector via httpx.MockTransport.

Покрываем:
- Корректный парсинг (perp, активный, есть funding lighter)
- Игнорируется spot и market_type != "perp"
- Игнорируются status != "active"
- Markets без записи lighter в funding-rates пропускаются
- Multi-exchange funding-rates: только lighter попадает в snapshot, остальные exchange'и игнорируются
- last_trade_price <= 0 → skip (маркет ещё не торговался)
- open_interest / volume отсутствуют → None
- Один кривой entry не валит остальной батч
- Неправильная верхняя форма ответа → ValueError
"""

from __future__ import annotations

import httpx
import pytest

from funding_scout.connectors.lighter import LighterConnector


def _transport(details_payload, funding_payload):
    """MockTransport, маршрутизирует по path: orderBookDetails / funding-rates."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == LighterConnector.ORDER_BOOK_DETAILS_PATH:
            return httpx.Response(200, json=details_payload)
        if path == LighterConnector.FUNDING_RATES_PATH:
            return httpx.Response(200, json=funding_payload)
        return httpx.Response(404, json={"error": f"unexpected path {path}"})

    return httpx.MockTransport(handler)


def _detail(
    symbol="BTC",
    market_id=1,
    market_type="perp",
    status="active",
    last_trade_price=78000.0,
    open_interest=100.0,
    daily_quote_token_volume=1_000_000.0,
):
    return {
        "symbol": symbol,
        "market_id": market_id,
        "market_type": market_type,
        "status": status,
        "last_trade_price": last_trade_price,
        "open_interest": open_interest,
        "daily_quote_token_volume": daily_quote_token_volume,
    }


def _funding(symbol="BTC", market_id=1, exchange="lighter", rate=-5.6e-05):
    return {"market_id": market_id, "exchange": exchange, "symbol": symbol, "rate": rate}


@pytest.mark.asyncio
async def test_parses_normal_response():
    details = {
        "code": 200,
        "order_book_details": [
            _detail(symbol="BTC", market_id=1, last_trade_price=78000.0, open_interest=1000),
            _detail(symbol="ETH", market_id=0, last_trade_price=3500.0, open_interest=2000),
        ],
        "spot_order_book_details": [],
    }
    funding = {
        "code": 200,
        "funding_rates": [
            _funding("BTC", 1, "lighter", -5.6e-05),
            _funding("ETH", 0, "lighter", 0.000012),
        ],
    }
    c = LighterConnector(transport=_transport(details, funding))
    ticks = await c.fetch_snapshot()

    assert len(ticks) == 2
    by_ticker = {t.ticker: t for t in ticks}

    btc = by_ticker["BTC"]
    assert btc.venue == "lighter"
    assert btc.funding_rate_1h == pytest.approx(-5.6e-05)
    assert btc.mark_price == pytest.approx(78000.0)
    assert btc.index_price is None
    assert btc.oi_long == pytest.approx(1000)
    assert btc.oi_short is None
    assert btc.volume_24h == pytest.approx(1_000_000)

    assert by_ticker["ETH"].funding_rate_1h == pytest.approx(0.000012)


@pytest.mark.asyncio
async def test_filters_only_lighter_exchange_in_funding():
    """Multi-exchange entries в funding-rates: берём только lighter."""
    details = {
        "code": 200,
        "order_book_details": [_detail(symbol="BTC", market_id=1, last_trade_price=78000)],
        "spot_order_book_details": [],
    }
    funding = {
        "code": 200,
        "funding_rates": [
            _funding("BTC", 1, "binance", 0.0001),
            _funding("BTC", 1, "bybit", 0.00015),
            _funding("BTC", 1, "hyperliquid", 0.00012),
            _funding("BTC", 1, "lighter", -0.00005),
        ],
    }
    c = LighterConnector(transport=_transport(details, funding))
    ticks = await c.fetch_snapshot()
    assert len(ticks) == 1
    assert ticks[0].funding_rate_1h == pytest.approx(-0.00005)


@pytest.mark.asyncio
async def test_skips_spot_and_non_perp():
    details = {
        "code": 200,
        "order_book_details": [
            _detail(symbol="BTC", market_id=1, market_type="perp"),
            _detail(symbol="USDC", market_id=99, market_type="spot"),
            _detail(symbol="ASML", market_id=151, market_type="option"),  # на всякий
        ],
        "spot_order_book_details": [
            _detail(symbol="SKY/USDC", market_id=2053, market_type="spot"),
        ],
    }
    funding = {
        "code": 200,
        "funding_rates": [
            _funding("BTC", 1, "lighter"),
            _funding("USDC", 99, "lighter"),
            _funding("ASML", 151, "lighter"),
        ],
    }
    c = LighterConnector(transport=_transport(details, funding))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"BTC"}


@pytest.mark.asyncio
async def test_skips_inactive_markets():
    details = {
        "code": 200,
        "order_book_details": [
            _detail(symbol="BTC", market_id=1, status="active"),
            _detail(symbol="OLDCOIN", market_id=99, status="halted"),
            _detail(symbol="DEAD", market_id=100, status="delisted"),
        ],
        "spot_order_book_details": [],
    }
    funding = {
        "code": 200,
        "funding_rates": [
            _funding("BTC", 1, "lighter"),
            _funding("OLDCOIN", 99, "lighter"),
            _funding("DEAD", 100, "lighter"),
        ],
    }
    c = LighterConnector(transport=_transport(details, funding))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"BTC"}


@pytest.mark.asyncio
async def test_skips_market_without_lighter_funding_entry():
    """Маркет в orderBookDetails есть, в funding-rates у lighter нет → skip."""
    details = {
        "code": 200,
        "order_book_details": [
            _detail(symbol="BTC", market_id=1),
            _detail(symbol="VERYNEW", market_id=200),  # листинг без funding
        ],
        "spot_order_book_details": [],
    }
    funding = {
        "code": 200,
        "funding_rates": [
            _funding("BTC", 1, "lighter"),
            _funding("VERYNEW", 200, "binance"),  # есть на binance, но не на lighter
        ],
    }
    c = LighterConnector(transport=_transport(details, funding))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"BTC"}


@pytest.mark.asyncio
async def test_skips_zero_or_negative_last_trade_price():
    details = {
        "code": 200,
        "order_book_details": [
            _detail(symbol="BTC", market_id=1, last_trade_price=78000),
            _detail(symbol="UNTRADED", market_id=2, last_trade_price=0.0),  # ещё не торговался
        ],
        "spot_order_book_details": [],
    }
    funding = {
        "code": 200,
        "funding_rates": [
            _funding("BTC", 1, "lighter"),
            _funding("UNTRADED", 2, "lighter"),
        ],
    }
    c = LighterConnector(transport=_transport(details, funding))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"BTC"}


@pytest.mark.asyncio
async def test_handles_missing_optional_fields():
    details = {
        "code": 200,
        "order_book_details": [
            {
                "symbol": "BTC",
                "market_id": 1,
                "market_type": "perp",
                "status": "active",
                "last_trade_price": 78000.0,
                # open_interest и daily_quote_token_volume отсутствуют
            }
        ],
        "spot_order_book_details": [],
    }
    funding = {"code": 200, "funding_rates": [_funding("BTC", 1, "lighter")]}
    c = LighterConnector(transport=_transport(details, funding))
    ticks = await c.fetch_snapshot()
    assert len(ticks) == 1
    assert ticks[0].oi_long is None
    assert ticks[0].volume_24h is None


@pytest.mark.asyncio
async def test_malformed_entry_does_not_kill_batch():
    details = {
        "code": 200,
        "order_book_details": [
            _detail(symbol="GOOD", market_id=1),
            {  # missing required last_trade_price
                "symbol": "BAD",
                "market_id": 2,
                "market_type": "perp",
                "status": "active",
            },
            _detail(symbol="ALSOGOOD", market_id=3),
        ],
        "spot_order_book_details": [],
    }
    funding = {
        "code": 200,
        "funding_rates": [
            _funding("GOOD", 1, "lighter"),
            _funding("BAD", 2, "lighter"),
            _funding("ALSOGOOD", 3, "lighter"),
        ],
    }
    c = LighterConnector(transport=_transport(details, funding))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"GOOD", "ALSOGOOD"}


@pytest.mark.asyncio
async def test_invalid_funding_rate_in_one_entry_skipped():
    """Если у одного market'а в funding-rates не парсится rate — он не попадает в map,
    значит market пропустится по skipped_no_funding (это валидное поведение)."""
    details = {
        "code": 200,
        "order_book_details": [
            _detail(symbol="GOOD", market_id=1),
            _detail(symbol="BADFUNDING", market_id=2),
        ],
        "spot_order_book_details": [],
    }
    funding = {
        "code": 200,
        "funding_rates": [
            _funding("GOOD", 1, "lighter", rate=0.0001),
            {"market_id": 2, "exchange": "lighter", "symbol": "BADFUNDING", "rate": "not-a-number"},
        ],
    }
    c = LighterConnector(transport=_transport(details, funding))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"GOOD"}


@pytest.mark.asyncio
async def test_raises_on_bad_details_shape():
    details = ["not", "a", "dict"]
    funding = {"code": 200, "funding_rates": []}
    c = LighterConnector(transport=_transport(details, funding))
    with pytest.raises(ValueError, match="Unexpected Lighter orderBookDetails shape"):
        await c.fetch_snapshot()


@pytest.mark.asyncio
async def test_raises_on_bad_funding_shape():
    details = {"code": 200, "order_book_details": [], "spot_order_book_details": []}
    funding = ["not", "a", "dict"]
    c = LighterConnector(transport=_transport(details, funding))
    with pytest.raises(ValueError, match="Unexpected Lighter funding-rates shape"):
        await c.fetch_snapshot()


@pytest.mark.asyncio
async def test_http_error_propagates():
    """HTTP 5xx с любого из двух endpoints поднимается — runner снаружи изолирует."""

    def handler(request):
        return httpx.Response(503, json={"error": "service unavailable"})

    c = LighterConnector(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await c.fetch_snapshot()
