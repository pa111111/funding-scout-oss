"""Snapshot runner.

`run_once()` — снимает один снапшот по всем коннекторам и пишет в БД.
`run_loop(interval)` — крутит снапшоты по расписанию (для systemd unit / docker entrypoint).

Все коннекторы фетчатся параллельно через asyncio.gather. Падение одного не валит весь снимок.
ON CONFLICT DO NOTHING — не дублируем строки если запустили дважды на одну минуту.
"""

from __future__ import annotations

import asyncio
import time

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..connectors import ALL_CONNECTORS
from ..connectors.base import Connector, FundingTick
from ..storage.db import SessionLocal, engine
from ..storage.models import FundingSnapshot

log = structlog.get_logger()


async def _safe_fetch(c: Connector) -> list[FundingTick]:
    """Wrapper that swallows errors per-connector so one bad DEX не валит всё."""
    try:
        return await c.fetch_snapshot()
    except Exception as e:
        log.error("connector_failed", venue=c.venue, error=str(e), exc_info=True)
        return []


async def take_snapshot() -> dict[str, int]:
    """Снимаем снапшот со всех коннекторов параллельно. Возвращает {venue: rows_inserted}."""
    ts = int(time.time())

    results = await asyncio.gather(*(_safe_fetch(c) for c in ALL_CONNECTORS))

    counts: dict[str, int] = {}
    all_rows: list[dict] = []

    for connector, ticks in zip(ALL_CONNECTORS, results, strict=True):
        counts[connector.venue] = len(ticks)
        for t in ticks:
            all_rows.append(
                {
                    "ts": ts,
                    "venue": t.venue,
                    "ticker": t.ticker,
                    "funding_rate_1h": t.funding_rate_1h,
                    "mark_price": t.mark_price,
                    "index_price": t.index_price,
                    "oi_long": t.oi_long,
                    "oi_short": t.oi_short,
                    "volume_24h": t.volume_24h,
                    "raw": t.raw,
                }
            )

    if not all_rows:
        log.warning("snapshot_empty")
        return counts

    with SessionLocal() as session:
        dialect = engine.dialect.name
        if dialect == "sqlite":
            stmt = sqlite_insert(FundingSnapshot).values(all_rows).prefix_with("OR IGNORE")
        elif dialect in ("postgresql", "postgres"):
            stmt = pg_insert(FundingSnapshot).values(all_rows).on_conflict_do_nothing()
        else:
            raise RuntimeError(f"Unsupported DB dialect for upsert: {dialect}")
        session.execute(stmt)
        session.commit()

    log.info("snapshot_written", ts=ts, **counts)
    return counts


def run_once() -> dict[str, int]:
    return asyncio.run(take_snapshot())


async def _loop(interval_seconds: int) -> None:
    log.info("snapshot_loop_start", interval=interval_seconds)
    while True:
        try:
            await take_snapshot()
        except Exception as e:
            log.error("snapshot_loop_iteration_failed", error=str(e), exc_info=True)
        await asyncio.sleep(interval_seconds)


def run_loop(interval_seconds: int) -> None:
    asyncio.run(_loop(interval_seconds))
