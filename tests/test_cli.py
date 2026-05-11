"""End-to-end тесты CLI команд через click.testing.CliRunner."""

from __future__ import annotations

from click.testing import CliRunner
from sqlalchemy import select

from funding_scout import connectors as connectors_pkg
from funding_scout.cli import cli
from funding_scout.connectors.base import Connector, FundingTick
from funding_scout.storage import SessionLocal
from funding_scout.storage.models import FundingSnapshot


class _StubConnector(Connector):
    venue = "stub"

    async def fetch_snapshot(self) -> list[FundingTick]:
        return [
            FundingTick(venue="stub", ticker="BTC", funding_rate_1h=0.0001, mark_price=60000),
            FundingTick(venue="stub", ticker="ETH", funding_rate_1h=-0.0001, mark_price=3000),
        ]


def test_cli_init_creates_schema_idempotently():
    runner = CliRunner()
    r = runner.invoke(cli, ["init"])
    assert r.exit_code == 0, r.output
    assert "initialized" in r.output.lower()

    # Повторный init не должен падать
    r = runner.invoke(cli, ["init"])
    assert r.exit_code == 0


def test_cli_snapshot_inserts_ticks(monkeypatch):
    import funding_scout.snapshot.runner as runner_mod

    monkeypatch.setattr(connectors_pkg, "ALL_CONNECTORS", [_StubConnector()])
    monkeypatch.setattr(runner_mod, "ALL_CONNECTORS", [_StubConnector()])

    runner = CliRunner()
    r = runner.invoke(cli, ["snapshot"])
    assert r.exit_code == 0, r.output
    assert "Inserted 2 ticks" in r.output

    with SessionLocal() as s:
        rows = s.execute(select(FundingSnapshot)).scalars().all()
        assert len(rows) == 2


def test_cli_snapshot_loop_rejects_too_short_interval():
    runner = CliRunner()
    r = runner.invoke(cli, ["snapshot", "--loop", "30"])
    assert r.exit_code != 0
    assert "≥ 60" in r.output or "BadParameter" in r.output or "loop interval" in r.output.lower()


def test_cli_status_on_empty_db():
    runner = CliRunner()
    r = runner.invoke(cli, ["status"])
    assert r.exit_code == 0, r.output
    assert "Total funding_snapshot rows: 0" in r.output


def test_cli_status_after_snapshot(monkeypatch):
    import funding_scout.snapshot.runner as runner_mod

    monkeypatch.setattr(connectors_pkg, "ALL_CONNECTORS", [_StubConnector()])
    monkeypatch.setattr(runner_mod, "ALL_CONNECTORS", [_StubConnector()])

    runner = CliRunner()
    runner.invoke(cli, ["snapshot"])
    r = runner.invoke(cli, ["status"])
    assert r.exit_code == 0, r.output
    assert "Total funding_snapshot rows: 2" in r.output
    assert "stub" in r.output
    assert "tickers=2" in r.output


def test_cli_help():
    runner = CliRunner()
    r = runner.invoke(cli, ["--help"])
    assert r.exit_code == 0
    assert "init" in r.output
    assert "snapshot" in r.output
    assert "status" in r.output
