"""Unit tests for EdgeX connector via httpx.MockTransport.

Стратегия мока: handler роутит /getMetaData и /getTicker?contractId=X
по path и параметрам, отдаёт preset-данные. Покрываем:
- Корректный двухступенчатый запрос (meta → tickers per contract)
- Фильтрация по enableDisplay AND enableTrade
- Naming: BTCUSD → BTC, 1000PEPEUSD → 1000PEPE
- markPrice <= 0 → skip
- Один кривой ticker не валит остальной батч
- Один failed ticker fetch (HTTP 500) не валит остальной батч
- Empty data в getTicker → skip
- Wrong shape metadata → ValueError
- isStock=True → ticker всё равно обрабатывается (важно для equity)
"""

from __future__ import annotations

import httpx
import pytest

from funding_scout.connectors.edgex import EdgeXConnector


def _meta(contracts):
    return {
        "code": "SUCCESS",
        "data": {"contractList": contracts},
        "msg": None,
    }


def _ticker(
    contract_id="10000001",
    contract_name="BTCUSD",
    funding="0.00001",
    mark="79574",
    index="79640",
    open_interest="448",
    value="381350218.09",
):
    return {
        "code": "SUCCESS",
        "data": [
            {
                "contractId": contract_id,
                "contractName": contract_name,
                "fundingRate": funding,
                "fundingTime": "1778212800000",
                "markPrice": mark,
                "indexPrice": index,
                "oraclePrice": index,
                "openInterest": open_interest,
                "value": value,
                "size": "8735",
                "lastPrice": mark,
                "high": "81000",
                "low": "79000",
                "open": "80000",
                "close": mark,
            }
        ],
        "msg": None,
    }


def _contract(contract_id, contract_name, enable_display=True, enable_trade=True, is_stock=False):
    return {
        "contractId": contract_id,
        "contractName": contract_name,
        "enableDisplay": enable_display,
        "enableTrade": enable_trade,
        "isStock": is_stock,
        "tickSize": "0.1",
    }


def _make_transport(meta_payload, ticker_payload_by_id):
    """meta_payload — dict для /getMetaData. ticker_payload_by_id — dict {contractId: response_dict_or_status_code}."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == EdgeXConnector.META_PATH:
            return httpx.Response(200, json=meta_payload)
        if request.url.path == EdgeXConnector.TICKER_PATH:
            cid = request.url.params.get("contractId")
            payload = ticker_payload_by_id.get(cid)
            if isinstance(payload, int):  # status code shortcut for failures
                return httpx.Response(payload, json={"code": "ERROR"})
            if payload is None:
                return httpx.Response(200, json={"code": "SUCCESS", "data": []})
            return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"error": f"unexpected path {request.url.path}"})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_parses_normal_response():
    contracts = [
        _contract("10000001", "BTCUSD"),
        _contract("10000002", "ETHUSD"),
    ]
    tickers = {
        "10000001": _ticker(contract_id="10000001", contract_name="BTCUSD", funding="0.00001", mark="79574"),
        "10000002": _ticker(contract_id="10000002", contract_name="ETHUSD", funding="-0.00001", mark="2280"),
    }
    c = EdgeXConnector(transport=_make_transport(_meta(contracts), tickers))
    ticks = await c.fetch_snapshot()

    by_ticker = {t.ticker: t for t in ticks}
    assert set(by_ticker) == {"BTC", "ETH"}
    assert by_ticker["BTC"].funding_rate_1h == pytest.approx(0.00001)
    assert by_ticker["BTC"].mark_price == pytest.approx(79574)
    assert by_ticker["ETH"].funding_rate_1h == pytest.approx(-0.00001)


@pytest.mark.asyncio
async def test_filters_by_enableDisplay_and_enableTrade():
    """Контракты с enableDisplay=False или enableTrade=False — игнорируем (legacy/v2)."""
    contracts = [
        _contract("10000001", "BTCUSD", enable_display=True, enable_trade=True),
        _contract("10000002", "ETHUSD", enable_display=False, enable_trade=True),  # legacy
        _contract("10000003", "SOLUSD", enable_display=True, enable_trade=False),  # disabled
        _contract("10000004", "BNBUSD", enable_display=True, enable_trade=True),
    ]
    tickers = {
        "10000001": _ticker(contract_id="10000001", contract_name="BTCUSD"),
        "10000004": _ticker(contract_id="10000004", contract_name="BNBUSD"),
    }
    c = EdgeXConnector(transport=_make_transport(_meta(contracts), tickers))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"BTC", "BNB"}


@pytest.mark.asyncio
async def test_strips_USD_suffix_from_name():
    contracts = [
        _contract("1", "BTCUSD"),
        _contract("2", "1000PEPEUSD"),
        _contract("3", "PLTRUSD"),
        _contract("4", "WEIRDNAME"),  # no USD suffix — leave as is
    ]
    tickers = {
        str(i): _ticker(contract_id=str(i), contract_name=c["contractName"])
        for i, c in enumerate(contracts, 1)
    }
    c = EdgeXConnector(transport=_make_transport(_meta(contracts), tickers))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"BTC", "1000PEPE", "PLTR", "WEIRDNAME"}


@pytest.mark.asyncio
async def test_includes_equity_perps():
    """isStock=True контракты обрабатываются как любой другой — это ключевая фича EdgeX."""
    contracts = [
        _contract("1", "BTCUSD", is_stock=False),
        _contract("2", "NVDAUSD", is_stock=True),
        _contract("3", "TSLAUSD", is_stock=True),
        _contract("4", "PLTRUSD", is_stock=True),
    ]
    tickers = {str(i): _ticker(contract_id=str(i), contract_name=c["contractName"], mark=str(100*i+50))
               for i, c in enumerate(contracts, 1)}
    c = EdgeXConnector(transport=_make_transport(_meta(contracts), tickers))
    ticks = await c.fetch_snapshot()
    assert {"NVDA", "TSLA", "PLTR", "BTC"} == {t.ticker for t in ticks}


@pytest.mark.asyncio
async def test_skips_zero_mark_price():
    contracts = [
        _contract("1", "BTCUSD"),
        _contract("2", "DEADUSD"),
    ]
    tickers = {
        "1": _ticker(contract_id="1", contract_name="BTCUSD", mark="79574"),
        "2": _ticker(contract_id="2", contract_name="DEADUSD", mark="0"),
    }
    c = EdgeXConnector(transport=_make_transport(_meta(contracts), tickers))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"BTC"}


@pytest.mark.asyncio
async def test_individual_ticker_failure_does_not_kill_batch():
    """Если getTicker для одного контракта упал HTTP 500 — остальные доходят."""
    contracts = [
        _contract("1", "BTCUSD"),
        _contract("2", "BROKENUSD"),
        _contract("3", "ETHUSD"),
    ]
    tickers = {
        "1": _ticker(contract_id="1", contract_name="BTCUSD"),
        "2": 503,  # simulated server error
        "3": _ticker(contract_id="3", contract_name="ETHUSD"),
    }
    c = EdgeXConnector(transport=_make_transport(_meta(contracts), tickers))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"BTC", "ETH"}


@pytest.mark.asyncio
async def test_individual_ticker_empty_data_skipped():
    contracts = [
        _contract("1", "BTCUSD"),
        _contract("2", "EMPTYUSD"),
    ]
    tickers = {
        "1": _ticker(contract_id="1", contract_name="BTCUSD"),
        "2": {"code": "SUCCESS", "data": []},  # биржа вернула пустой список
    }
    c = EdgeXConnector(transport=_make_transport(_meta(contracts), tickers))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"BTC"}


@pytest.mark.asyncio
async def test_individual_ticker_malformed_skipped():
    """Один контракт с битыми числами в ticker — он скипается, остальные обрабатываются."""
    contracts = [
        _contract("1", "BTCUSD"),
        _contract("2", "BADUSD"),
        _contract("3", "ETHUSD"),
    ]
    tickers = {
        "1": _ticker(contract_id="1", contract_name="BTCUSD"),
        "2": {
            "code": "SUCCESS",
            "data": [{
                "contractId": "2",
                "contractName": "BADUSD",
                "fundingRate": "not-a-number",
                "markPrice": "100",
            }],
        },
        "3": _ticker(contract_id="3", contract_name="ETHUSD"),
    }
    c = EdgeXConnector(transport=_make_transport(_meta(contracts), tickers))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"BTC", "ETH"}


@pytest.mark.asyncio
async def test_handles_missing_optional_fields():
    contracts = [_contract("1", "BTCUSD")]
    tickers = {
        "1": {
            "code": "SUCCESS",
            "data": [{
                "contractId": "1",
                "contractName": "BTCUSD",
                "fundingRate": "0.00001",
                "markPrice": "79574",
                # indexPrice/openInterest/value отсутствуют
            }],
        }
    }
    c = EdgeXConnector(transport=_make_transport(_meta(contracts), tickers))
    ticks = await c.fetch_snapshot()
    assert len(ticks) == 1
    t = ticks[0]
    assert t.index_price is None
    assert t.oi_long is None
    assert t.volume_24h is None


@pytest.mark.asyncio
async def test_raises_on_metadata_error_code():
    payload = {"code": "RATE_LIMIT", "data": None, "msg": "too many requests"}
    c = EdgeXConnector(transport=_make_transport(payload, {}))
    with pytest.raises(ValueError, match="Unexpected EdgeX metadata response"):
        await c.fetch_snapshot()


@pytest.mark.asyncio
async def test_raises_on_metadata_contractList_not_list():
    payload = {"code": "SUCCESS", "data": {"contractList": "not-a-list"}}
    c = EdgeXConnector(transport=_make_transport(payload, {}))
    with pytest.raises(ValueError, match="contractList is not a list"):
        await c.fetch_snapshot()


@pytest.mark.asyncio
async def test_metadata_http_error_propagates():
    """Если /getMetaData упал HTTP 500 — поднимаем (runner снаружи изолирует)."""

    def handler(request):
        if request.url.path == EdgeXConnector.META_PATH:
            return httpx.Response(503)
        return httpx.Response(404)

    c = EdgeXConnector(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await c.fetch_snapshot()


@pytest.mark.asyncio
async def test_no_active_contracts_returns_empty():
    """Если все контракты задизейблены — возвращаем пустой список без падения."""
    contracts = [
        _contract("1", "BTCUSD", enable_display=False),
        _contract("2", "ETHUSD", enable_trade=False),
    ]
    c = EdgeXConnector(transport=_make_transport(_meta(contracts), {}))
    ticks = await c.fetch_snapshot()
    assert ticks == []
