# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-05-12

Initial public release.

### Added
- 4 DEX connectors: Hyperliquid, Lighter, Pacifica, EdgeX.
- Storage layer on SQLAlchemy 2 with SQLite/Postgres support; composite PK `(ts, venue, ticker)`, JSON `raw` column for backfill, dialect-aware UPSERT.
- Snapshot loop (`funding-scout snapshot --loop`), systemd-friendly, per-connector failure isolation.
- Cross-DEX same-ticker detector (taxonomy type 1).
- EV engine: spread APR, round-trip cost model per venue, `min_profitable_hours`. No risk penalty inside EV (transparent risk disclosure paradigm).
- Dash + dash-ag-grid web UI with sortable/filterable setup table.
- Telegram daily-report bot.
- Watchdog with 4 health checks (Postgres reachable, snapshot freshness, per-venue freshness, web alive), Telegram alerts only on state change.
- CLI: `init`, `snapshot`, `scan`, `web`, `status`, `daily-report`.
- 119 mocked tests + 5 E2E (gated by `FUNDING_SCOUT_E2E=1`).
- Reference systemd units in `deploy/systemd/`.
- Framework docs: concept, strategies (6 types), risk framework, execution model, position lifecycle, exchanges, tech stack.

[0.1.0]: https://github.com/pa111111/funding-scout/releases/tag/v0.1.0
