"""Hyperliquid public REST connector.

API endpoint: POST https://api.hyperliquid.xyz/info  body: {"type": "metaAndAssetCtxs"}
Returns: [meta, [ctx, ctx, ...]] где meta.universe[i] описывает i-й перп, ctxs[i] — текущие данные.

Поле `funding` в ctx — это **часовая** ставка фандинга в виде decimal (0.0000125 = 0.00125%/h).
HL фиксирует фандинг каждый час.

HIP-3 builder-deployed perp-dex'ы (RWA: нефть, металлы, акции, индексы) живут в отдельных dex'ах
внутри HL и НЕ попадают в дефолтный metaAndAssetCtxs. Чтобы их забрать — тот же endpoint с
параметром `{"type": "metaAndAssetCtxs", "dex": "<name>"}`. Тикеры приходят с префиксом
`<dex>:TICKER` (напр. `xyz:BRENTOIL`); мы его срезаем, чтобы тикер матчился с тем же активом
на других venue (BRENTOIL на HL ↔ BRENTOIL на Lighter). venue для builder-dex = `hyperliquid-<dex>`,
чтобы отличать риск-профиль (deployer контролирует оракул — см. risk_framework.md).
Список builder-dex'ов: POST /info {"type":"perpDexs"}.

Docs: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
"""

from __future__ import annotations

import httpx
import structlog

from ..config import settings
from .base import Connector, FundingTick

log = structlog.get_logger()


class HyperliquidConnector(Connector):
    venue = "hyperliquid"

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
        dex: str | None = None,
    ):
        self.base_url = (base_url or settings.hyperliquid_api).rstrip("/")
        self.timeout = timeout or settings.http_timeout_seconds
        # transport — для тестов (httpx.MockTransport). В проде None.
        self.transport = transport
        # dex=None → основной perp-dex (крипта). dex="xyz" → builder-deployed RWA-dex.
        self.dex = dex
        if dex:
            self.venue = f"hyperliquid-{dex}"

    async def fetch_snapshot(self) -> list[FundingTick]:
        body: dict = {"type": "metaAndAssetCtxs"}
        if self.dex:
            body["dex"] = self.dex
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            r = await client.post(
                f"{self.base_url}/info",
                json=body,
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()

        # Defensive parse: API may evolve. Bail loudly if shape mismatches.
        if not isinstance(data, list) or len(data) != 2:
            raise ValueError(f"Unexpected HL response shape: {type(data).__name__} len={len(data) if isinstance(data, list) else 'n/a'}")

        meta, ctxs = data
        universe = meta.get("universe", [])
        if len(universe) != len(ctxs):
            log.warning(
                "hl_universe_ctx_mismatch",
                universe_len=len(universe),
                ctxs_len=len(ctxs),
            )

        prefix = f"{self.dex}:" if self.dex else None

        ticks: list[FundingTick] = []
        skipped = 0
        for asset, ctx in zip(universe, ctxs, strict=False):
            try:
                # HL marks delisted with isDelisted=True; пропускаем.
                if asset.get("isDelisted"):
                    skipped += 1
                    continue

                # Builder-dex тикеры приходят как "xyz:BRENTOIL" — срезаем префикс,
                # чтобы тикер матчился с тем же активом на других venue.
                ticker = asset["name"]
                if prefix and ticker.startswith(prefix):
                    ticker = ticker[len(prefix):]

                tick = FundingTick(
                    venue=self.venue,
                    ticker=ticker,
                    funding_rate_1h=float(ctx["funding"]),
                    mark_price=float(ctx["markPx"]),
                    index_price=float(ctx["midPx"]) if ctx.get("midPx") else None,
                    # HL не разделяет OI по сторонам; кладём общий в long, short оставляем None.
                    oi_long=float(ctx["openInterest"]) if ctx.get("openInterest") else None,
                    oi_short=None,
                    volume_24h=float(ctx["dayNtlVlm"]) if ctx.get("dayNtlVlm") else None,
                    # dex в raw — для risk-badge (deployer-controlled oracle) и аудита.
                    raw={"asset": asset, "ctx": ctx, "dex": self.dex},
                )
                ticks.append(tick)
            except (KeyError, ValueError, TypeError) as e:
                skipped += 1
                log.debug("hl_skip_malformed", ticker=asset.get("name"), err=str(e))

        log.info("hl_fetched", venue=self.venue, dex=self.dex, count=len(ticks), skipped=skipped)
        return ticks
