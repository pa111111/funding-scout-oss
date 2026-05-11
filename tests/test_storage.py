"""Тесты схемы и идемпотентности БД."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from funding_scout.storage import SessionLocal, engine
from funding_scout.storage.models import FundingSnapshot


def _row(**overrides):
    base = dict(
        ts=1700000000,
        venue="hl",
        ticker="BTC",
        funding_rate_1h=0.0001,
        mark_price=60000,
        index_price=60001,
        oi_long=100.0,
        oi_short=None,
        volume_24h=1_000_000,
        raw={"foo": "bar"},
    )
    base.update(overrides)
    return base


def test_insert_and_query_roundtrip():
    with SessionLocal() as s:
        s.execute(sqlite_insert(FundingSnapshot).values(_row()))
        s.commit()

    with SessionLocal() as s:
        rows = s.execute(select(FundingSnapshot)).scalars().all()
        assert len(rows) == 1
        r = rows[0]
        assert r.ticker == "BTC"
        assert r.venue == "hl"
        assert r.funding_rate_1h == pytest.approx(0.0001)
        assert r.raw == {"foo": "bar"}


def test_composite_pk_blocks_duplicate_via_plain_insert():
    """Без OR IGNORE два инсёрта с тем же (ts, venue, ticker) → IntegrityError."""
    with SessionLocal() as s:
        s.execute(sqlite_insert(FundingSnapshot).values(_row()))
        s.commit()

    with SessionLocal() as s, pytest.raises(IntegrityError):
        s.execute(sqlite_insert(FundingSnapshot).values(_row()))
        s.commit()


def test_or_ignore_makes_duplicate_silent():
    """Со OR IGNORE дубликат не вставляется и не падает — то что использует runner."""
    with SessionLocal() as s:
        s.execute(sqlite_insert(FundingSnapshot).values(_row()).prefix_with("OR IGNORE"))
        s.commit()
        s.execute(sqlite_insert(FundingSnapshot).values(_row()).prefix_with("OR IGNORE"))
        s.commit()

    with SessionLocal() as s:
        count = len(s.execute(select(FundingSnapshot)).scalars().all())
        assert count == 1


def test_different_ts_or_venue_or_ticker_inserts_separately():
    """Все три ключа должны участвовать — поменялся любой → новая строка."""
    with SessionLocal() as s:
        s.execute(sqlite_insert(FundingSnapshot).values(_row(ts=1)))
        s.execute(sqlite_insert(FundingSnapshot).values(_row(ts=2)))  # другой ts
        s.execute(sqlite_insert(FundingSnapshot).values(_row(venue="lighter")))  # другой venue
        s.execute(sqlite_insert(FundingSnapshot).values(_row(ticker="ETH")))  # другой ticker
        s.commit()

    with SessionLocal() as s:
        assert len(s.execute(select(FundingSnapshot)).scalars().all()) == 4


def test_engine_dialect_is_sqlite_in_tests():
    """Sanity: тесты крутятся на SQLite (на Postgres был бы другой UPSERT)."""
    assert engine.dialect.name == "sqlite"


def test_nullable_optional_fields():
    """index_price/oi_long/oi_short/volume_24h могут быть NULL."""
    with SessionLocal() as s:
        s.execute(
            sqlite_insert(FundingSnapshot).values(
                _row(index_price=None, oi_long=None, oi_short=None, volume_24h=None)
            )
        )
        s.commit()

    with SessionLocal() as s:
        r = s.execute(select(FundingSnapshot)).scalar_one()
        assert r.index_price is None
        assert r.oi_long is None
        assert r.volume_24h is None
