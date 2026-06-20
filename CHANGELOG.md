# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Survival signal on every setup — the predictive complement to decay. Kaplan–Meier over the distribution of historical window lifetimes (45d) answers "how many more hours will this window last", *before* the spread moves: `survival_median_remaining_h`, `survival_median_lifetime_h`, `survival_p_survive_min_hold`, `survival_curve`, plus honest `survival_confidence`/`survival_sample_size`/`survival_pooled` (thin-history pairs fall back to a flagged global pooled curve). Inline on `GET /api/setups` and as three UI columns (Est. left h / Median life h / Survival sparkline). Self-contained `survival/` engine (pure window extraction + KM, no numpy), cached per snapshot. See [`docs/api.md`](docs/api.md#survival-signal).
- Decay / staleness signal on every setup — `staleness` (`fresh`/`cooling`/`stale`/`gone`/`unknown`), `peak_spread_apr_pct`, `decay_from_peak_pct`, `hours_since_peak`, computed from the trailing 24h spread series (same data as the sparkline, no second engine). Answers "is this window opening or closing" — feeds the "close X" half of a downstream join. See [`docs/api.md`](docs/api.md#decay--staleness-signal).
- `GET /api/setups/<candidate_id>` — per-candidate decay verdict, reconstructed from raw `funding_snapshot` so it can speak to a setup that has already vanished from the live verdict (`present: false`). Malformed id → 400; empty DB → valid `unknown` envelope, never 500.
- Read-only `GET /api/setups` JSON endpoint — the live EV verdict as JSON for programmatic consumers, served on the same host/port as the Dash UI. Reuses `get_latest_setups()`, so UI and JSON share one computation. Envelope carries `computed_at`, `capital_usd`, `meta`, `setups[]`. See [`docs/api.md`](docs/api.md).
- Stable `candidate_id` (`TICKER:LONG:SHORT`) on every setup — deterministic key to reference the same setup over time and reconcile it with a downstream position.
- `setup_snapshot` table — persists the computed detector verdict each cadence (previously computed on the fly and never stored), enabling setup history for a future decay/staleness signal. Written by the snapshot runner on the same `ts` as raw funding; composite PK `(ts, candidate_id)` for idempotency; persist isolated from the raw write.
- `detectors.detect_setups()` — single source of truth for "which setups exist"; the UI, the JSON endpoint and the persisted history all call it.

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

[0.1.0]: https://github.com/pa111111/funding-scout-oss/releases/tag/v0.1.0
