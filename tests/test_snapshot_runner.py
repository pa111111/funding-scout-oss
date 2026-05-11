"""Тесты snapshot runner'а с подменой реестра коннекторов."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from sqlalchemy import select

from funding_scout import connectors as connectors_pkg
from funding_scout.connectors.base import Connector, FundingTick
from funding_scout.snapshot.runner import take_snapshot
from funding_scout.storage import SessionLocal
from funding_scout.storage.models import FundingSnapshot


class _FakeOk(Connector):
    """Возвращает пред-заданные тики."""

    def __init__(self, venue: str, ticks: list[FundingTick]):
        self.venue = venue
        self._ticks = ticks

    async def fetch_snapshot(self) -> list[FundingTick]:
        return self._ticks


class _FakeBoom(Connector):
    """Кидает исключение — проверяем изоляцию."""

    venue = "boom"

    async def fetch_snapshot(self) -> list[FundingTick]:
        raise RuntimeError("simulated outage")


class _FakeEmpty(Connector):
    venue = "empty"

    async def fetch_snapshot(self) -> list[FundingTick]:
        return []


def _patch_connectors(monkeypatch, conns):
    monkeypatch.setattr(connectors_pkg, "ALL_CONNECTORS", conns)
    # runner импортирует ALL_CONNECTORS из пакета — patch'им и в его namespace тоже,
    # потому что `from ..connectors import ALL_CONNECTORS` создал локальный bind.
    import funding_scout.snapshot.runner as runner_mod

    monkeypatch.setattr(runner_mod, "ALL_CONNECTORS", conns)


def test_snapshot_writes_ticks_to_db(monkeypatch):
    fake_ticks = [
        FundingTick(venue="hl", ticker="BTC", funding_rate_1h=0.0001, mark_price=60000),
        FundingTick(venue="hl", ticker="ETH", funding_rate_1h=-0.0002, mark_price=3000),
    ]
    _patch_connectors(monkeypatch, [_FakeOk("hl", fake_ticks)])

    counts = asyncio.run(take_snapshot())
    assert counts == {"hl": 2}

    with SessionLocal() as s:
        rows = s.execute(select(FundingSnapshot).order_by(FundingSnapshot.ticker)).scalars().all()
        assert len(rows) == 2
        assert {r.ticker for r in rows} == {"BTC", "ETH"}


def test_failing_connector_does_not_kill_others(monkeypatch):
    """Один коннектор валится — остальные доходят до БД, runner не падает."""
    ok_ticks = [FundingTick(venue="hl", ticker="BTC", funding_rate_1h=0.0001, mark_price=60000)]
    _patch_connectors(monkeypatch, [_FakeOk("hl", ok_ticks), _FakeBoom()])

    counts = asyncio.run(take_snapshot())
    assert counts == {"hl": 1, "boom": 0}

    with SessionLocal() as s:
        rows = s.execute(select(FundingSnapshot)).scalars().all()
        assert len(rows) == 1
        assert rows[0].ticker == "BTC"


def test_all_connectors_failing_does_not_crash(monkeypatch):
    _patch_connectors(monkeypatch, [_FakeBoom(), _FakeBoom()])
    counts = asyncio.run(take_snapshot())
    assert all(v == 0 for v in counts.values())

    with SessionLocal() as s:
        assert len(s.execute(select(FundingSnapshot)).scalars().all()) == 0


def test_empty_connector_does_not_crash(monkeypatch):
    _patch_connectors(monkeypatch, [_FakeEmpty()])
    counts = asyncio.run(take_snapshot())
    assert counts == {"empty": 0}


def test_idempotent_within_same_second(monkeypatch):
    """Если по какой-то причине runner запустился дважды на тот же ts —
    OR IGNORE / ON CONFLICT DO NOTHING предотвращает дубликаты."""
    import funding_scout.snapshot.runner as runner_mod

    fake_ticks = [
        FundingTick(venue="hl", ticker="BTC", funding_rate_1h=0.0001, mark_price=60000),
    ]
    _patch_connectors(monkeypatch, [_FakeOk("hl", fake_ticks)])

    # Замораживаем time.time, чтобы оба запуска получили один и тот же ts
    fixed_ts = 1_700_000_000
    monkeypatch.setattr(runner_mod.time, "time", lambda: fixed_ts)

    asyncio.run(take_snapshot())
    asyncio.run(take_snapshot())  # дубликат

    with SessionLocal() as s:
        rows = s.execute(select(FundingSnapshot)).scalars().all()
        assert len(rows) == 1
        assert rows[0].ts == fixed_ts


def test_multi_venue_one_call(monkeypatch):
    """Несколько коннекторов в одном снапшоте — все ноги пишутся."""
    _patch_connectors(
        monkeypatch,
        [
            _FakeOk(
                "hl",
                [FundingTick(venue="hl", ticker="BTC", funding_rate_1h=0.0001, mark_price=60000)],
            ),
            _FakeOk(
                "lighter",
                [FundingTick(venue="lighter", ticker="BTC", funding_rate_1h=-0.0001, mark_price=60010)],
            ),
        ],
    )
    counts = asyncio.run(take_snapshot())
    assert counts == {"hl": 1, "lighter": 1}

    with SessionLocal() as s:
        rows = s.execute(select(FundingSnapshot).order_by(FundingSnapshot.venue)).scalars().all()
        assert [(r.venue, r.ticker) for r in rows] == [("hl", "BTC"), ("lighter", "BTC")]
