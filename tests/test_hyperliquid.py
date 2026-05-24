"""Тесты Hyperliquid коннектора через httpx.MockTransport.

Покрываем:
- Корректный парсинг нормального ответа (несколько активов)
- Пропуск delisted активов
- Толерантность к отсутствующим optional полям (midPx, openInterest, dayNtlVlm)
- Грейсфул skip кривых записей (плохое funding/markPx) без падения батча
- Жёсткий fail на принципиально неверной форме ответа (не [meta, ctxs])
- Числа приходят строками — float() их парсит (так HL и отдаёт)
"""

from __future__ import annotations

import json

import httpx
import pytest

from funding_scout.connectors.hyperliquid import HyperliquidConnector


def _mock_transport(payload, expect_dex=None):
    """Return MockTransport that responds with given payload to any POST.

    expect_dex: если задан — проверяем что в body есть {"dex": expect_dex}.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["type"] == "metaAndAssetCtxs"
        if expect_dex is None:
            assert "dex" not in body
        else:
            assert body["dex"] == expect_dex
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_parses_normal_response():
    payload = [
        {
            "universe": [
                {"name": "BTC", "szDecimals": 5},
                {"name": "ETH", "szDecimals": 4},
            ]
        },
        [
            {
                "funding": "0.0000125",
                "openInterest": "1234.567",
                "dayNtlVlm": "45000000",
                "markPx": "67510.0",
                "midPx": "67510.5",
            },
            {
                "funding": "-0.00005",
                "openInterest": "5000.0",
                "dayNtlVlm": "12000000",
                "markPx": "3500.0",
                "midPx": "3500.1",
            },
        ],
    ]
    c = HyperliquidConnector(transport=_mock_transport(payload))
    ticks = await c.fetch_snapshot()

    assert len(ticks) == 2

    btc, eth = ticks
    assert btc.venue == "hyperliquid"
    assert btc.ticker == "BTC"
    assert btc.funding_rate_1h == pytest.approx(0.0000125)
    assert btc.mark_price == pytest.approx(67510.0)
    assert btc.index_price == pytest.approx(67510.5)
    assert btc.oi_long == pytest.approx(1234.567)
    assert btc.oi_short is None  # HL не разделяет OI по сторонам
    assert btc.volume_24h == pytest.approx(45000000)
    assert btc.raw["asset"]["name"] == "BTC"

    assert eth.ticker == "ETH"
    assert eth.funding_rate_1h == pytest.approx(-0.00005)


@pytest.mark.asyncio
async def test_skips_delisted_assets():
    payload = [
        {
            "universe": [
                {"name": "BTC", "szDecimals": 5},
                {"name": "OLDCOIN", "szDecimals": 0, "isDelisted": True},
                {"name": "ETH", "szDecimals": 4},
            ]
        },
        [
            {"funding": "0.0", "markPx": "60000", "openInterest": "0"},
            {"funding": "0.0", "markPx": "0.001", "openInterest": "0"},
            {"funding": "0.0", "markPx": "3000", "openInterest": "0"},
        ],
    ]
    c = HyperliquidConnector(transport=_mock_transport(payload))
    ticks = await c.fetch_snapshot()

    tickers = {t.ticker for t in ticks}
    assert tickers == {"BTC", "ETH"}
    assert "OLDCOIN" not in tickers


@pytest.mark.asyncio
async def test_handles_missing_optional_fields():
    payload = [
        {"universe": [{"name": "BTC", "szDecimals": 5}]},
        [
            {
                "funding": "0.0",
                "markPx": "60000",
                # midPx, openInterest, dayNtlVlm все отсутствуют — это валидно
            }
        ],
    ]
    c = HyperliquidConnector(transport=_mock_transport(payload))
    ticks = await c.fetch_snapshot()

    assert len(ticks) == 1
    t = ticks[0]
    assert t.index_price is None
    assert t.oi_long is None
    assert t.volume_24h is None


@pytest.mark.asyncio
async def test_skips_malformed_entry_keeps_others():
    """Один кривой актив не валит батч — он скипается, остальные обрабатываются."""
    payload = [
        {
            "universe": [
                {"name": "GOOD", "szDecimals": 5},
                {"name": "BAD", "szDecimals": 5},
                {"name": "ALSOGOOD", "szDecimals": 5},
            ]
        },
        [
            {"funding": "0.0001", "markPx": "100"},
            {"funding": "not-a-number", "markPx": "100"},  # ValueError → skip
            {"funding": "0.0002", "markPx": "200"},
        ],
    ]
    c = HyperliquidConnector(transport=_mock_transport(payload))
    ticks = await c.fetch_snapshot()

    tickers = {t.ticker for t in ticks}
    assert tickers == {"GOOD", "ALSOGOOD"}


@pytest.mark.asyncio
async def test_skips_entry_missing_required_field():
    """Missing markPx или funding — KeyError → skip только этой записи."""
    payload = [
        {
            "universe": [
                {"name": "GOOD", "szDecimals": 5},
                {"name": "MISSING_MARK", "szDecimals": 5},
            ]
        },
        [
            {"funding": "0.0001", "markPx": "100"},
            {"funding": "0.0002"},  # markPx отсутствует — обязателен
        ],
    ]
    c = HyperliquidConnector(transport=_mock_transport(payload))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"GOOD"}


@pytest.mark.asyncio
async def test_raises_on_wrong_top_level_shape():
    """Если HL вдруг сменит формат на dict — лучше упасть громко, чем тихо."""
    payload = {"universe": [], "ctxs": []}  # dict вместо list
    c = HyperliquidConnector(transport=_mock_transport(payload))
    with pytest.raises(ValueError, match="Unexpected HL response shape"):
        await c.fetch_snapshot()


@pytest.mark.asyncio
async def test_raises_on_wrong_list_length():
    """Список должен быть [meta, ctxs] — длина 2."""
    payload = [{"universe": []}]  # длина 1, не 2
    c = HyperliquidConnector(transport=_mock_transport(payload))
    with pytest.raises(ValueError, match="Unexpected HL response shape"):
        await c.fetch_snapshot()


@pytest.mark.asyncio
async def test_handles_universe_ctx_length_mismatch():
    """Если длины universe и ctxs не совпали — обрабатываем по короткому, не падаем."""
    payload = [
        {
            "universe": [
                {"name": "BTC", "szDecimals": 5},
                {"name": "ETH", "szDecimals": 4},
                {"name": "SOL", "szDecimals": 3},  # лишний — ctx нет
            ]
        },
        [
            {"funding": "0.0", "markPx": "60000"},
            {"funding": "0.0", "markPx": "3000"},
        ],
    ]
    c = HyperliquidConnector(transport=_mock_transport(payload))
    ticks = await c.fetch_snapshot()
    assert {t.ticker for t in ticks} == {"BTC", "ETH"}


@pytest.mark.asyncio
async def test_http_error_propagates():
    """HTTP 500 поднимается — runner снаружи поймает и изолирует."""

    def handler(request):
        return httpx.Response(500, json={"error": "internal"})

    c = HyperliquidConnector(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await c.fetch_snapshot()


# --- HIP-3 builder-deployed perp-dex'ы ---


def test_builder_dex_sets_venue():
    """dex=xyz → venue=hyperliquid-xyz; основной коннектор остаётся hyperliquid."""
    assert HyperliquidConnector().venue == "hyperliquid"
    assert HyperliquidConnector(dex="xyz").venue == "hyperliquid-xyz"


@pytest.mark.asyncio
async def test_builder_dex_sends_dex_param_and_strips_prefix():
    """Запрос к builder-dex шлёт {"dex": "xyz"}, тикеры приходят как 'xyz:BRENTOIL',
    в БД кладём чистый 'BRENTOIL' чтобы матчился cross-DEX с тем же активом."""
    payload = [
        {
            "universe": [
                {"name": "xyz:BRENTOIL", "szDecimals": 2},
                {"name": "xyz:GOLD", "szDecimals": 4},
            ]
        },
        [
            {
                "funding": "0.00000625",
                "openInterest": "3331471.86",
                "dayNtlVlm": "286299431.35",
                "markPx": "94.791",
                "midPx": "94.844",
            },
            {
                "funding": "-0.0000885788",
                "openInterest": "2194318.24",
                "dayNtlVlm": "432564234.65",
                "markPx": "90.489",
                "midPx": "90.489",
            },
        ],
    ]
    c = HyperliquidConnector(transport=_mock_transport(payload, expect_dex="xyz"), dex="xyz")
    ticks = await c.fetch_snapshot()

    assert {t.ticker for t in ticks} == {"BRENTOIL", "GOLD"}  # префикс срезан
    assert all(t.venue == "hyperliquid-xyz" for t in ticks)

    brent = next(t for t in ticks if t.ticker == "BRENTOIL")
    assert brent.funding_rate_1h == pytest.approx(0.00000625)
    assert brent.mark_price == pytest.approx(94.791)
    assert brent.raw["dex"] == "xyz"  # deployer-метка для risk-badge
    # сырое имя с префиксом сохранено в raw для аудита
    assert brent.raw["asset"]["name"] == "xyz:BRENTOIL"


@pytest.mark.asyncio
async def test_builder_dex_ticker_without_prefix_passes_through():
    """Если тикер вдруг без префикса dex — не падаем, оставляем как есть."""
    payload = [
        {"universe": [{"name": "WEIRD", "szDecimals": 2}]},
        [{"funding": "0.0", "markPx": "100"}],
    ]
    c = HyperliquidConnector(transport=_mock_transport(payload, expect_dex="xyz"), dex="xyz")
    ticks = await c.fetch_snapshot()
    assert len(ticks) == 1
    assert ticks[0].ticker == "WEIRD"
    assert ticks[0].venue == "hyperliquid-xyz"


@pytest.mark.asyncio
async def test_main_connector_raw_has_null_dex():
    """Основной коннектор кладёт dex=None в raw (для единообразия схемы raw)."""
    payload = [
        {"universe": [{"name": "BTC", "szDecimals": 5}]},
        [{"funding": "0.0", "markPx": "60000"}],
    ]
    c = HyperliquidConnector(transport=_mock_transport(payload))
    ticks = await c.fetch_snapshot()
    assert ticks[0].raw["dex"] is None
