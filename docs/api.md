# JSON API & setup history

funding-scout is read-only by design: it computes EV-ranked setups and shows them.
This page documents the two machine-facing surfaces added for programmatic
consumers (e.g. an operator/agent that joins "what scout proposes" with "what is
actually open"):

1. **`GET /api/setups`** — the live verdict as JSON.
2. **`setup_snapshot`** — the persisted history of that verdict, one row per setup
   per snapshot.

Both reuse the exact computation behind the Dash UI. There is no second engine:
the UI, the JSON endpoint and the persisted history all call
`detectors.detect_setups()`, so the set of setups can never drift between the
live view and the stored history.

## Trust perimeter

The endpoint is **read-only** and holds **no keys**. It carries the same perimeter
as the Dash UI — bound to the Tailscale/localhost interface, no auth layer on top.
It only exposes market analysis (the same numbers a human sees in the UI); it
cannot place, sign, or move anything. Keep it off the public internet (the
production `ufw` already blocks `8050` on the public NIC; only `tailscale0` is
allowed).

## `GET /api/setups`

Served by the Dash Flask server, same host/port as the UI (`8050` by default).

```bash
curl -s http://<host>:8050/api/setups
```

### Response envelope

```jsonc
{
  "computed_at": "2026-06-07T09:53:36+00:00", // when THIS verdict was computed
  "capital_usd": 5000,                        // capital the EV figures are scaled to
  "meta": {
    "snapshot_ts": 1700000000,                // unix ts of the raw snapshot used
    "snapshot_iso": "2023-11-14T22:13:20+00:00",
    "age_seconds": 142,                       // how stale the underlying raw data is
    "venue_counts": { "hyperliquid": 210, "lighter": 180 },
    "setups_count": 37
  },
  "setups": [ /* array of setup objects, see below */ ]
}
```

`computed_at` is the verdict time and is distinct from `meta.age_seconds`, which
is the age of the **raw** funding data the verdict was built on. A consumer that
wants freshness should look at `age_seconds`: if the snapshot loop stalls, the
endpoint still answers, but with stale `meta`.

Empty database returns a valid envelope with `setups: []` and
`setups_count: 0` — never a 500.

### Setup object

Every numeric field is raw (no formatting); `inf`/`nan` are emitted as `null` so
the payload is always strict JSON.

| Field | Type | Meaning |
|---|---|---|
| `candidate_id` | string | Stable id `TICKER:LONG_VENUE:SHORT_VENUE`. See below. |
| `type` | string | Detector type, e.g. `cross-dex-same-ticker`. |
| `ticker` | string | Base symbol, no `-PERP` suffix. |
| `long_venue` / `short_venue` | string | Legs. Long = the venue you pay/receive the *lower* funding on. |
| `spread_apr_pct` | float | Annualised carry premium, %. Can be 0 or negative (still emitted). |
| `delta_spread_apr_pct_1h` | float \| null | Change in spread APR vs the previous (~1h) snapshot. `null` if no prior. |
| `spread_sparkline` | string | Unicode block sparkline of spread over the last 24h (display aid). |
| `window_age_hours` | int | Consecutive hours the spread has held above the window threshold. |
| `base_ev_usd_per_day` | float | Average $/day at `capital_usd`, no risk penalty (transparent risk disclosure). |
| `min_profitable_hours` | float \| null | Hours to clear round-trip cost. `null` if spread ≤ 0 (was `inf`). |
| `long_funding_apr_pct` / `short_funding_apr_pct` | float | Per-leg funding, annualised %. |
| `round_trip_cost_pct` | float | Combined round-trip friction of both legs, %. |
| `min_volume_24h_m_usd` | float \| null | Min 24h volume across legs, in $M. `null` if a venue omits volume. |
| `long_mark_price` / `short_mark_price` | float | Mark prices used. |
| `price_spread_pct` | float | `(long_mark − short_mark) / short_mark × 100`. +ve = longing the dearer leg. |
| `snapshot_ts` | int | Unix ts of the snapshot this row was computed from. |

### `capital_usd`

The endpoint scales `base_ev_usd_per_day` to a fixed capital (default `5000`,
configurable per process). It is the same value the UI uses. EV scales linearly,
so a consumer can rescale to any size: `ev_at_X = base_ev_usd_per_day * X / capital_usd`.

## `candidate_id`

`candidate_id` is `TICKER:LONG_VENUE:SHORT_VENUE` — a deterministic, stable key for
one setup across time. It is built on the same natural key
`(ticker, long_venue, short_venue)` that the spread-delta and sparkline-history
matching already use, so it is consistent with everything else in the system.

It lets a consumer:
- reference "the same setup" across snapshots (needed for the staleness/decay
  signal built on top of `setup_snapshot`), and
- reconcile a scout candidate with a position actually opened downstream.

If funding flips direction and the legs swap, the id changes — that is **a
different trade** (you are now long the other venue), and the id honestly reflects
it.

## `setup_snapshot` table

Raw funding (`funding_snapshot`) was always persisted, but the *verdict* of the
detectors used to be computed on the fly and never stored. Without setup history
there is no way to say "setup X went stale" — the staleness/decay signal needs
past values to compare against.

`setup_snapshot` stores the computed setups, written by the **same snapshot runner**
on the **same `ts`** as the raw rows, so one snapshot is one consistent pair of
`(funding + setups)`. The persist step is isolated from the raw write: if a
detector throws, the raw rows are already committed and the watchdog does not see
a lost snapshot.

| Column | Type | Notes |
|---|---|---|
| `ts` | int | Unix ts UTC, equals `funding_snapshot.ts`. Part of PK. |
| `candidate_id` | string | `TICKER:LONG:SHORT`. Part of PK. |
| `type`, `ticker`, `long_venue`, `short_venue` | string | Identity. |
| `spread_apr_pct` | float | Annualised carry premium, %. |
| `base_ev_per_dollar_per_day` | float | EV per $1 (capital-independent; scale by capital downstream). |
| `long_funding_apr_pct`, `short_funding_apr_pct` | float | Per-leg funding, annualised %. |
| `round_trip_cost_pct` | float | Combined round-trip friction, %. |
| `price_spread_pct` | float | Price divergence between legs, %. |
| `min_profitable_hours` | float \| null | `null` when spread ≤ 0 (`inf`/`nan` are stored as `NULL`, never as `inf`). |
| `min_volume_24h_usd` | float \| null | Min 24h volume across legs; `null` if a venue omits it. |

Composite primary key `(ts, candidate_id)` gives the same idempotency as the raw
table: re-running the runner on the same second does not duplicate rows
(`ON CONFLICT DO NOTHING` / `OR IGNORE`). Indexed on `(candidate_id, ts)` for
"history of one setup over time" decay queries, and on `ts`.

## Operational notes

- The table is created by `funding-scout init` (idempotent `create_all`); it only
  adds `setup_snapshot` and leaves `funding_snapshot` untouched. On an existing
  deployment: `git pull && funding-scout init && systemctl restart funding-scout-snapshot funding-scout-web`.
- The snapshot loop populates `setup_snapshot` on its first pass after restart
  (the loop snapshots immediately, then sleeps).
- To add a new setup type, add the detector to `detectors.ALL_DETECTORS` — it then
  appears in the UI, the JSON endpoint, and the persisted history at once.
