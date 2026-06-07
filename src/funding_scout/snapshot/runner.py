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
from ..detectors import Setup, detect_setups
from ..storage.db import SessionLocal, engine
from ..storage.models import FundingSnapshot, SetupSnapshot

log = structlog.get_logger()


def _finite_or_none(x: float | None) -> float | None:
    """inf/nan → None. Float-колонки не должны хранить inf (spread<=0 даёт inf hours)."""
    if x is None or x != x or x in (float("inf"), float("-inf")):
        return None
    return x


def _setup_to_persist_row(s: Setup) -> dict:
    """Setup → строка setup_snapshot. Только экономическое ядро, без display-полей."""
    return {
        "ts": s.snapshot_ts,
        "candidate_id": s.candidate_id,
        "type": s.type,
        "ticker": s.ticker,
        "long_venue": s.long_venue,
        "short_venue": s.short_venue,
        "spread_apr_pct": s.spread_apr_pct,
        "base_ev_per_dollar_per_day": s.base_ev_per_dollar_per_day,
        "long_funding_apr_pct": s.long_funding_apr_pct,
        "short_funding_apr_pct": s.short_funding_apr_pct,
        "round_trip_cost_pct": s.round_trip_cost_pct,
        "price_spread_pct": s.price_spread_pct,
        "min_profitable_hours": _finite_or_none(s.min_profitable_hours),
        "min_volume_24h_usd": s.min_volume_24h_usd,
    }


def persist_setups(ts: int) -> int:
    """Считает связки на снапшоте `ts` единым detect_setups() и пишет в setup_snapshot.

    Идемпотентно (ON CONFLICT DO NOTHING по PK (ts, candidate_id)). Возвращает число
    связок. Та же каденция и тот же ts, что у сырья — снапшот атомарен по смыслу.
    """
    with SessionLocal() as session:
        setups = detect_setups(session, ts)
        if not setups:
            return 0
        rows = [_setup_to_persist_row(s) for s in setups]
        dialect = engine.dialect.name
        if dialect == "sqlite":
            stmt = sqlite_insert(SetupSnapshot).values(rows).prefix_with("OR IGNORE")
        elif dialect in ("postgresql", "postgres"):
            stmt = pg_insert(SetupSnapshot).values(rows).on_conflict_do_nothing()
        else:
            raise RuntimeError(f"Unsupported DB dialect for upsert: {dialect}")
        session.execute(stmt)
        session.commit()
    return len(setups)


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

    # Персист вычисленных связок на тот же ts. Изолируем от записи сырья: если детектор
    # упадёт, сырьё уже сохранено и watchdog не считает снапшот потерянным.
    try:
        setups_count = persist_setups(ts)
        log.info("snapshot_written", ts=ts, setups=setups_count, **counts)
    except Exception as e:
        log.error("persist_setups_failed", ts=ts, error=str(e), exc_info=True)
        log.info("snapshot_written", ts=ts, setups=0, **counts)

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
