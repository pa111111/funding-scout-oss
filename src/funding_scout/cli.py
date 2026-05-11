"""CLI entry point. Все команды — read/write only по БД, никаких ордеров."""

from __future__ import annotations

import click
import structlog

from .config import configure_logging
from .snapshot import run_loop, run_once
from .storage import SessionLocal, init_db
from .storage.models import FundingSnapshot

log = structlog.get_logger()


@click.group()
def cli() -> None:
    """funding-scout — DEX-only funding-rate arbitrage scout."""
    configure_logging()


@cli.command("init")
def init_cmd() -> None:
    """Создать схему БД (idempotent)."""
    init_db()
    click.echo("DB schema initialized.")


@cli.command("snapshot")
@click.option(
    "--loop",
    "loop_seconds",
    type=int,
    default=None,
    help="Если задано — крутиться в цикле с интервалом N секунд (для systemd).",
)
def snapshot_cmd(loop_seconds: int | None) -> None:
    """Снять снапшот со всех коннекторов и записать в БД."""
    if loop_seconds is None:
        counts = run_once()
        total = sum(counts.values())
        click.echo(f"Snapshot done. Inserted {total} ticks: {counts}")
    else:
        if loop_seconds < 60:
            raise click.BadParameter("loop interval must be ≥ 60s")
        run_loop(loop_seconds)


@cli.command("web")
@click.option("--host", default="127.0.0.1", help="bind адрес (для VPS — 0.0.0.0)")
@click.option("--port", default=8050, type=int)
@click.option("--debug", is_flag=True, help="Dash debug-mode (auto-reload, ошибки в браузере)")
def web_cmd(host: str, port: int, debug: bool) -> None:
    """Запустить web UI (Dash + AG-Grid)."""
    from .web.app import run

    click.echo(f"Starting funding-scout web at http://{host}:{port}/ ...")
    run(host=host, port=port, debug=debug)


@cli.command("scan")
def scan_cmd() -> None:
    """Прогнать детекторы по последнему снапшоту и вывести топ-20 setups в терминал."""
    from .web.data import get_latest_setups

    meta, rows = get_latest_setups()
    if not rows:
        click.echo("No setups (DB empty or no overlapping tickers).")
        return

    click.echo(
        f"Snapshot @ {meta['snapshot_iso']} ({meta['age_seconds']}s ago) | "
        f"venues: {meta['venue_counts']} | setups: {meta['setups_count']}"
    )
    click.echo("")
    rows = sorted(rows, key=lambda r: r["spread_apr_pct"] or 0, reverse=True)[:20]
    fmt = "{:<10}{:<24}{:>12}{:>10}{:>12}{:>12}"
    click.echo(
        fmt.format(
            "Ticker",
            "Long -> Short",
            "Spread%APR",
            "EV $/day",
            "RT cost%",
            "MinVol $M",
        )
    )
    click.echo("-" * 80)
    for r in rows:
        click.echo(
            fmt.format(
                r["ticker"][:9],
                f"{r['long_venue'][:10]} -> {r['short_venue'][:10]}",
                f"{r['spread_apr_pct']:+.1f}",
                f"${r['base_ev_usd_per_day']:+.2f}",
                f"{r['round_trip_cost_pct']:.2f}",
                f"{r['min_volume_24h_m_usd']:.2f}" if r["min_volume_24h_m_usd"] is not None else "—",
            )
        )


@cli.command("daily-report")
@click.option("--top", "top_n", default=10, type=int, help="Кол-во связок в отчёте")
def daily_report_cmd(top_n: int) -> None:
    """Отправить дневной отчёт в Telegram (топ-N связок по spread APR)."""
    from .reporting import send_daily_report

    sent = send_daily_report(top_n=top_n)
    if sent:
        click.echo(f"Daily report sent ({top_n} setups).")
    else:
        click.echo("Daily report NOT sent (Telegram не сконфигурирован или ошибка).")
        raise click.exceptions.Exit(1)


@cli.command("status")
def status_cmd() -> None:
    """Показать сколько строк в БД и последний ts по каждому venue."""
    from sqlalchemy import func, select

    with SessionLocal() as session:
        total = session.scalar(select(func.count()).select_from(FundingSnapshot)) or 0
        click.echo(f"Total funding_snapshot rows: {total}")

        if total == 0:
            return

        stmt = (
            select(
                FundingSnapshot.venue,
                func.max(FundingSnapshot.ts).label("last_ts"),
                func.count().label("rows"),
                func.count(func.distinct(FundingSnapshot.ticker)).label("tickers"),
            )
            .group_by(FundingSnapshot.venue)
            .order_by(FundingSnapshot.venue)
        )
        for venue, last_ts, rows, tickers in session.execute(stmt):
            click.echo(
                f"  {venue:<16} rows={rows:<8} tickers={tickers:<5} last_ts={last_ts}"
            )


if __name__ == "__main__":
    cli()
