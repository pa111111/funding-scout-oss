"""Smoke-тесты против живых endpoint'ов бирж.

По умолчанию **пропускаются** — чтобы CI не флэппил из-за аптайма / сети.
Запускать вручную:

    FUNDING_SCOUT_E2E=1 pytest tests/test_smoke_real_hl.py -v

Цель — поймать регрессию формата ответа биржи раньше, чем она просочится в прод-снапшоты.
"""

from __future__ import annotations

import os

import pytest

from funding_scout.connectors.edgex import EdgeXConnector
from funding_scout.connectors.hyperliquid import HyperliquidConnector
from funding_scout.connectors.lighter import LighterConnector
from funding_scout.connectors.pacifica import PacificaConnector

pytestmark = pytest.mark.skipif(
    os.environ.get("FUNDING_SCOUT_E2E") != "1",
    reason="set FUNDING_SCOUT_E2E=1 to run real-network tests",
)


@pytest.mark.asyncio
async def test_real_hl_responds_with_perps():
    c = HyperliquidConnector()
    ticks = await c.fetch_snapshot()

    assert len(ticks) > 50, "Hyperliquid обычно листит >100 перпов"

    tickers = {t.ticker for t in ticks}
    assert "BTC" in tickers
    assert "ETH" in tickers

    for t in ticks:
        assert t.venue == "hyperliquid"
        assert t.mark_price > 0
        # Funding 1h редко выходит за ±0.01 = ±1% / час
        assert abs(t.funding_rate_1h) < 0.05, f"подозрительно большой funding на {t.ticker}: {t.funding_rate_1h}"


@pytest.mark.asyncio
async def test_real_lighter_responds_with_perps():
    c = LighterConnector()
    ticks = await c.fetch_snapshot()

    assert len(ticks) > 50, "На Lighter обычно >100 активных перпов"

    tickers = {t.ticker for t in ticks}
    assert "BTC" in tickers
    assert "ETH" in tickers

    for t in ticks:
        assert t.venue == "lighter"
        assert t.mark_price > 0
        # Lighter clamp'ит funding на ±0.5% / час (документированно)
        assert abs(t.funding_rate_1h) <= 0.005 + 1e-9, (
            f"funding на {t.ticker} вышел за clamp ±0.5%/час: {t.funding_rate_1h}"
        )


@pytest.mark.asyncio
async def test_real_pacifica_responds_with_perps():
    c = PacificaConnector()
    ticks = await c.fetch_snapshot()

    assert len(ticks) > 30, "На Pacifica обычно 50+ перпов (включая equity/commodity)"

    tickers = {t.ticker for t in ticks}
    assert "BTC" in tickers
    assert "ETH" in tickers
    # Pacifica's killer feature — equity perps:
    assert "PLTR" in tickers or "NVDA" in tickers, "Pacifica должна листать equity-перпы"

    for t in ticks:
        assert t.venue == "pacifica"
        assert t.mark_price > 0
        # Funding 1h редко выходит за ±0.01 = ±1% / час
        assert abs(t.funding_rate_1h) < 0.05, (
            f"подозрительно большой funding на {t.ticker}: {t.funding_rate_1h}"
        )


@pytest.mark.asyncio
async def test_real_edgex_responds_with_perps():
    """E2E EdgeX. Существуют ДВА варианта блокировок по IP:
    - Полный бан (все 403) — если IP попал в rate-limit или общую гео-блокировку
    - Частичный бан (equity 403, crypto OK) — отдельная регуляторная политика на equity-перпы
    На VPS (Германия) обычно всё доступно. Локальные прогоны могут падать —
    тест мягко skip'ит если EdgeX недоступен и пишет warning."""
    import warnings

    c = EdgeXConnector()
    ticks = await c.fetch_snapshot()

    if not ticks:
        warnings.warn(
            "EdgeX вернул 0 ticks — вероятно полный rate-limit/гео бан с этого IP. "
            "На VPS должно работать. SKIP теста.",
            stacklevel=2,
        )
        pytest.skip("EdgeX недоступен с этого IP")

    tickers = {t.ticker for t in ticks}

    # Минимум — должны быть BTC и ETH (доступны везде где доступен EdgeX вообще)
    assert "BTC" in tickers
    assert "ETH" in tickers

    # Equity — soft check. Если IP частично заблокирован — warn но не падаем.
    found_equity = tickers & {"NVDA", "TSLA", "AAPL", "MSFT", "META", "GOOG", "PLTR", "HOOD"}
    if not found_equity:
        warnings.warn(
            "EdgeX equity-перпы недоступны с этого IP (региональная блокировка). "
            "На прод-VPS должны быть доступны.",
            stacklevel=2,
        )

    for t in ticks:
        assert t.venue == "edgex"
        assert t.mark_price > 0
        assert abs(t.funding_rate_1h) < 0.05, (
            f"подозрительно большой funding на {t.ticker}: {t.funding_rate_1h}"
        )


@pytest.mark.asyncio
async def test_real_hl_lighter_overlap_makes_sense():
    """Sanity: BTC присутствует на обеих биржах, цены близки (в пределах 5%)."""
    hl_ticks = {t.ticker: t for t in await HyperliquidConnector().fetch_snapshot()}
    lighter_ticks = {t.ticker: t for t in await LighterConnector().fetch_snapshot()}

    overlap = set(hl_ticks) & set(lighter_ticks)
    assert "BTC" in overlap
    assert "ETH" in overlap
    assert len(overlap) > 20, "ожидали хотя бы 20 общих тикеров"

    btc_hl = hl_ticks["BTC"].mark_price
    btc_lt = lighter_ticks["BTC"].mark_price
    diff_pct = abs(btc_hl - btc_lt) / btc_hl
    assert diff_pct < 0.05, f"BTC mark расходится >5%: HL={btc_hl}, Lighter={btc_lt}"
