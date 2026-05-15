"""Pacifica public REST connector.

Документация: https://docs.pacifica.fi/api-documentation/api
Базовый URL: https://api.pacifica.fi

Один endpoint покрывает всё что нужно для снапшота:
  GET /api/v1/info/prices
  {
    "success": true,
    "data": [
      {
        "symbol": "BTC",
        "funding": "0.000015",          # 1h funding rate, decimal-as-string
        "mark": "79574",                 # mark price
        "mid": "79573.5",                # best-bid+ask average
        "oracle": "79601.86455",         # oracle price (используем как index)
        "open_interest": "448.13237",    # в **base units** (не USD как в доках),
                                         #   для BTC = 448 BTC, не $448
        "volume_24h": "381350218.09266", # в USD
        "next_funding": "...",
        "timestamp": 1778216694321,      # ms
        "yesterday_price": "..."
      },
      ...
    ],
    "error": null,
    "code": null
  }

Pacifica сильно ценен тем что тут лежат **equity-перпы** (PLTR, NVDA, TSLA, HOOD)
и **commodities/forex** (XAU, XAG, SP500, GOOGL, CRCL, BP, COPPER, NATGAS, EURUSD, USDJPY).
Это разблокирует weekend cross-DEX equity стратегию (см. docs/strategies.md тип 2).

Формат чисел — strings, как в HL. float() парсит и то и другое.
Funding interval = 1h (как заявлено в доках, "calculated based on previous hour").
"""

from __future__ import annotations

import httpx
import structlog

from ..config import settings
from .base import Connector, FundingTick

log = structlog.get_logger()


class PacificaConnector(Connector):
    venue = "pacifica"

    PRICES_PATH = "/api/v1/info/prices"

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = (base_url or settings.pacifica_api).rstrip("/")
        self.timeout = timeout or settings.http_timeout_seconds
        self.transport = transport

    async def fetch_snapshot(self) -> list[FundingTick]:
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            r = await client.get(f"{self.base_url}{self.PRICES_PATH}")
            r.raise_for_status()
            data = r.json()

        # Defensive shape check.
        if not isinstance(data, dict) or "data" not in data:
            raise ValueError(
                f"Unexpected Pacifica response shape: keys={list(data) if isinstance(data, dict) else type(data).__name__}"
            )
        if not data.get("success", False):
            raise ValueError(
                f"Pacifica returned success=false: error={data.get('error')} code={data.get('code')}"
            )

        items = data.get("data", [])
        if not isinstance(items, list):
            raise ValueError(f"Pacifica data should be list, got {type(items).__name__}")

        ticks: list[FundingTick] = []
        skipped = 0

        for item in items:
            if not isinstance(item, dict):
                skipped += 1
                continue
            try:
                symbol = item["symbol"]
                mark = float(item["mark"])
                if mark <= 0:
                    skipped += 1
                    continue

                tick = FundingTick(
                    venue=self.venue,
                    ticker=symbol,
                    funding_rate_1h=float(item["funding"]),
                    mark_price=mark,
                    # oracle — это агрегат с разных источников, ближе всего к "истинной" цене
                    index_price=float(item["oracle"]) if item.get("oracle") else None,
                    # OI в base units (BTC, ETH, etc.) — консистентно с HL/Lighter
                    oi_long=float(item["open_interest"]) if item.get("open_interest") else None,
                    oi_short=None,  # Pacifica не разделяет OI по сторонам
                    volume_24h=float(item["volume_24h"]) if item.get("volume_24h") else None,
                    raw=item,
                )
                ticks.append(tick)
            except (KeyError, ValueError, TypeError) as e:
                skipped += 1
                log.debug("pacifica_skip_malformed", symbol=item.get("symbol"), err=str(e))

        log.info("pacifica_fetched", count=len(ticks), skipped=skipped)
        return ticks
