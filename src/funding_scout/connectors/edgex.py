"""EdgeX (pro.edgex.exchange) public REST connector.

Документация: https://edgex-1.gitbook.io/edgeX-documentation/edgex-v1
Базовый URL: https://pro.edgex.exchange

EdgeX даёт два endpoint'а нужных для снапшота:

  1) GET /api/v1/public/meta/getMetaData
     Возвращает список 292 контрактов с метаданными (contractId, contractName,
     enableDisplay, enableTrade, isStock, и т.д.). Bulk-эндпоинта тут нет.

  2) GET /api/v1/public/quote/getTicker?contractId=<id>
     Возвращает per-contract ticker с funding/mark/index/oracle/OI/value(24h volume).
     Параметр обязателен — без него data пустой.

Стратегия фетча:
1. Один раз — getMetaData → фильтруем по enableDisplay=True AND enableTrade=True
   (≈73 контракта vs 292 общих — отрезаем legacy v2 и hidden flag'и)
2. Параллельно — 73 запроса getTicker через asyncio.gather с ограничением
   max_connections=10 чтобы не словить rate-limit. Занимает ~3-7 сек.

Naming: contractName приходит как "BTCUSD"/"NVDAUSD"/"1000PEPEUSD" — убираем
суффикс "USD". `1000PEPE` остаётся как есть (нормализация в `kPEPE` не делается,
это сделает потом отдельный mapping слой если cross-DEX detector упрётся в это).

EdgeX killer-feature — самый широкий equity-набор среди DEX:
NVDA, TSLA, AAPL, MSFT, META, GOOG, AMZN, MSTR, COIN, AMD, INTC, AVGO, PLTR, HOOD
+ commodity (XAUT, PAXG, SILVER, COPPER, NATGAS) + ETF-style (SPY, QQQ).
Используется для weekend cross-DEX equity стратегии (см. docs/strategies.md).

Числа в JSON приходят как strings (как HL и Pacifica) — float() парсит.
Funding interval = 1h.
"""

from __future__ import annotations

import asyncio

import httpx
import structlog

from ..config import settings
from .base import Connector, FundingTick

log = structlog.get_logger()


class EdgeXConnector(Connector):
    venue = "edgex"

    META_PATH = "/api/v1/public/meta/getMetaData"
    TICKER_PATH = "/api/v1/public/quote/getTicker"

    # Параллельность одновременных запросов к /getTicker. Биржа явно не публикует
    # rate-limit, 10 кажется безопасно (не словит 429), общее время ~5 сек на 73 контракта.
    MAX_CONCURRENT = 10

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = (base_url or settings.edgex_api).rstrip("/")
        self.timeout = timeout or settings.http_timeout_seconds
        self.transport = transport

    async def fetch_snapshot(self) -> list[FundingTick]:
        limits = httpx.Limits(max_connections=self.MAX_CONCURRENT, max_keepalive_connections=self.MAX_CONCURRENT)
        async with httpx.AsyncClient(
            timeout=self.timeout, transport=self.transport, limits=limits
        ) as client:
            # 1) metadata
            meta_resp = await client.get(f"{self.base_url}{self.META_PATH}")
            meta_resp.raise_for_status()
            meta_json = meta_resp.json()

            if not isinstance(meta_json, dict) or meta_json.get("code") != "SUCCESS":
                raise ValueError(
                    f"Unexpected EdgeX metadata response: code={meta_json.get('code') if isinstance(meta_json, dict) else 'n/a'}"
                )

            data = meta_json.get("data") or {}
            contracts = data.get("contractList") or []
            if not isinstance(contracts, list):
                raise ValueError(f"contractList is not a list: {type(contracts).__name__}")

            # Берём только активные и primary (display=True). Тех у которых display=False
            # биржа считает legacy/v2 версиями — их не торгуем.
            primary = [
                c for c in contracts
                if isinstance(c, dict)
                and c.get("enableDisplay")
                and c.get("enableTrade")
            ]

            # 2) tickers — параллельно с лимитом
            sem = asyncio.Semaphore(self.MAX_CONCURRENT)

            async def fetch_ticker(contract_id: str) -> dict | None:
                async with sem:
                    try:
                        r = await client.get(
                            f"{self.base_url}{self.TICKER_PATH}",
                            params={"contractId": contract_id},
                        )
                        r.raise_for_status()
                        return r.json()
                    except (httpx.RequestError, httpx.HTTPStatusError) as e:
                        log.debug("edgex_ticker_fetch_failed", contractId=contract_id, err=str(e))
                        return None

            ticker_jsons = await asyncio.gather(
                *[fetch_ticker(c["contractId"]) for c in primary]
            )

        ticks: list[FundingTick] = []
        skipped_no_data = 0
        skipped_malformed = 0

        for contract, tj in zip(primary, ticker_jsons, strict=True):
            if tj is None:
                skipped_no_data += 1
                continue
            if not isinstance(tj, dict) or tj.get("code") != "SUCCESS":
                skipped_no_data += 1
                continue

            tickers_data = tj.get("data") or []
            if not isinstance(tickers_data, list) or not tickers_data:
                skipped_no_data += 1
                continue

            t = tickers_data[0]
            try:
                # Имя: BTCUSD → BTC, 1000PEPEUSD → 1000PEPE.
                # Если суффикс не USD (хз пока такие были, но защищаемся) — оставляем как есть.
                contract_name = contract["contractName"]
                if contract_name.endswith("USD"):
                    ticker = contract_name[:-3]
                else:
                    ticker = contract_name

                mark = float(t["markPrice"])
                if mark <= 0:
                    skipped_malformed += 1
                    continue

                tick = FundingTick(
                    venue=self.venue,
                    ticker=ticker,
                    funding_rate_1h=float(t["fundingRate"]),
                    mark_price=mark,
                    # indexPrice — взвешенная среди источников цена, ближе всего к "истине"
                    index_price=float(t["indexPrice"]) if t.get("indexPrice") else None,
                    # openInterest — в base units (BTC, ETH, акции в штуках)
                    oi_long=float(t["openInterest"]) if t.get("openInterest") else None,
                    oi_short=None,  # EdgeX не разделяет OI по сторонам
                    # value — 24h trade value в USD (size × avg_price)
                    volume_24h=float(t["value"]) if t.get("value") else None,
                    raw={"contract": contract, "ticker": t},
                )
                ticks.append(tick)
            except (KeyError, ValueError, TypeError) as e:
                skipped_malformed += 1
                log.debug(
                    "edgex_skip_malformed",
                    contractId=contract.get("contractId"),
                    err=str(e),
                )

        log.info(
            "edgex_fetched",
            count=len(ticks),
            primary_total=len(primary),
            skipped_no_data=skipped_no_data,
            skipped_malformed=skipped_malformed,
        )
        return ticks
