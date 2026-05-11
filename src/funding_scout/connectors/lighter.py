"""Lighter (zklighter) public REST connector.

Документация: https://apidocs.lighter.xyz/docs/get-started
Базовый URL прода: https://mainnet.zklighter.elliot.ai

Чтобы построить полный снапшот, нужны два endpoint'а — джойним по symbol:

  1) GET /api/v1/orderBookDetails
     {
       "code": 200,
       "order_book_details": [
         {
           "symbol": "BTC", "market_id": 1, "market_type": "perp", "status": "active",
           "last_trade_price": 78664.3,
           "open_interest": 1662.99,
           "daily_quote_token_volume": 411125783.22,
           ...
         },
         ...
       ],
       "spot_order_book_details": [...]   # игнорируем (это спот)
     }

  2) GET /api/v1/funding-rates
     {
       "code": 200,
       "funding_rates": [
         {"market_id": 1, "exchange": "lighter",     "symbol": "BTC", "rate": -0.000056},
         {"market_id": 1, "exchange": "binance",     "symbol": "BTC", "rate": 0.0000125},
         {"market_id": 1, "exchange": "bybit",       "symbol": "BTC", "rate": ...},
         {"market_id": 1, "exchange": "hyperliquid", "symbol": "BTC", "rate": ...},
         ...
       ]
     }

Lighter в своём API уже собирает кросс-биржевой компаратор по 4 биржам — это
их встроенный funding-arb tool. Нам тут нужна только нога `exchange == "lighter"`,
ставки HL/Binance/Bybit берём из своих коннекторов чтобы не плодить зависимости от Lighter API
и иметь канонические числа per venue.

Funding clamp: ±0.5%/час по доке Lighter. Реальные числа обычно намного меньше.
"""

from __future__ import annotations

import asyncio

import httpx
import structlog

from ..config import settings
from .base import Connector, FundingTick

log = structlog.get_logger()


class LighterConnector(Connector):
    venue = "lighter"

    ORDER_BOOK_DETAILS_PATH = "/api/v1/orderBookDetails"
    FUNDING_RATES_PATH = "/api/v1/funding-rates"

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = (base_url or settings.lighter_api).rstrip("/")
        self.timeout = timeout or settings.http_timeout_seconds
        self.transport = transport

    async def fetch_snapshot(self) -> list[FundingTick]:
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            details_resp, funding_resp = await asyncio.gather(
                client.get(f"{self.base_url}{self.ORDER_BOOK_DETAILS_PATH}"),
                client.get(f"{self.base_url}{self.FUNDING_RATES_PATH}"),
            )
            details_resp.raise_for_status()
            funding_resp.raise_for_status()
            details_data = details_resp.json()
            funding_data = funding_resp.json()

        # Defensive top-level shape checks.
        if not isinstance(details_data, dict) or "order_book_details" not in details_data:
            raise ValueError(
                f"Unexpected Lighter orderBookDetails shape: keys={list(details_data) if isinstance(details_data, dict) else type(details_data).__name__}"
            )
        if not isinstance(funding_data, dict) or "funding_rates" not in funding_data:
            raise ValueError(
                f"Unexpected Lighter funding-rates shape: keys={list(funding_data) if isinstance(funding_data, dict) else type(funding_data).__name__}"
            )

        # symbol -> Lighter's own funding rate (1h decimal)
        lighter_rates: dict[str, float] = {}
        for item in funding_data.get("funding_rates", []):
            if not isinstance(item, dict):
                continue
            if item.get("exchange") != self.venue:
                continue
            try:
                lighter_rates[item["symbol"]] = float(item["rate"])
            except (KeyError, ValueError, TypeError):
                continue

        ticks: list[FundingTick] = []
        skipped_inactive = 0
        skipped_no_funding = 0
        skipped_malformed = 0

        for d in details_data.get("order_book_details", []):
            try:
                if d.get("market_type") != "perp":
                    # spot/опционы — не наш домен
                    continue
                if d.get("status") != "active":
                    skipped_inactive += 1
                    continue

                symbol = d["symbol"]
                rate = lighter_rates.get(symbol)
                if rate is None:
                    # Маркет существует, но в funding-rates нет записи lighter.
                    # Возможно очень новый листинг до первого фандинга.
                    skipped_no_funding += 1
                    continue

                last_price = float(d["last_trade_price"])
                # Если по маркету ещё не было сделок, цена может быть 0 — пропускаем,
                # иначе мы запишем некорректный mark.
                if last_price <= 0:
                    skipped_malformed += 1
                    continue

                oi_raw = d.get("open_interest")
                vol_raw = d.get("daily_quote_token_volume")

                tick = FundingTick(
                    venue=self.venue,
                    ticker=symbol,
                    funding_rate_1h=rate,
                    mark_price=last_price,
                    index_price=None,  # Lighter не отдаёт отдельную index в этом endpoint
                    oi_long=float(oi_raw) if oi_raw is not None else None,
                    oi_short=None,  # OI не разделяется по сторонам
                    volume_24h=float(vol_raw) if vol_raw is not None else None,
                    raw={"orderBookDetails": d, "funding_rate": rate},
                )
                ticks.append(tick)
            except (KeyError, ValueError, TypeError) as e:
                skipped_malformed += 1
                log.debug("lighter_skip_malformed", symbol=d.get("symbol"), err=str(e))

        log.info(
            "lighter_fetched",
            count=len(ticks),
            skipped_inactive=skipped_inactive,
            skipped_no_funding=skipped_no_funding,
            skipped_malformed=skipped_malformed,
        )
        return ticks
