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
| `staleness` | string | Decay verdict: `fresh` / `cooling` / `stale` / `gone` / `unknown`. See [Decay signal](#decay--staleness-signal). |
| `peak_spread_apr_pct` | float \| null | Max spread APR over the 24h window. `null` if no history. |
| `decay_from_peak_pct` | float \| null | How far spread has fallen from its peak, 0–100%. `null` if peak ≤ 0 / no history. |
| `hours_since_peak` | int \| null | Hours since the most recent peak. `null` if no history. |
| `survival_median_remaining_h` | float \| null | Predicted hours the **current** window will still last — median residual life given its age (Kaplan–Meier). `null` if no active window or it outlives the observed horizon. See [Survival signal](#survival-signal). |
| `survival_median_lifetime_h` | float \| null | Typical **full** lifetime of a window on this pair (KM median). Context for `window_age_hours`. `null` under heavy censoring. |
| `survival_p_survive_min_hold` | float \| null | `P(window lasts ≥ min_profitable_hours more)` — chance it survives long enough to clear round-trip. `null` if no active window or spread ≤ 0. |
| `survival_curve` | array | `[[k, p], …]` for k = 1..24: `P(window survives ≥ k more hours)`. Raw input for a consumer's own survival-weighted EV. `[]` if no active window. |
| `survival_sample_size` | int | Completed historical windows the estimate rests on. |
| `survival_pooled` | bool | `true` when the pair had too few own windows (<5) and a global pooled curve was used instead. |
| `survival_confidence` | string | `high` (≥12 windows) / `medium` / `low` (pooled or <5) / `none` (no data). |
| `survival_sparkline` | string | Unicode block sparkline of `survival_curve` (display aid). |
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
- reference "the same setup" across snapshots — it is the address of the decay
  lookup (`GET /api/setups/<candidate_id>`), and
- reconcile a scout candidate with a position actually opened downstream.

If funding flips direction and the legs swap, the id changes — that is **a
different trade** (you are now long the other venue), and the id honestly reflects
it.

## Decay / staleness signal

A cross-DEX same-ticker setup is usually a **window** (4–12h), not steady carry.
So the operative question on a setup you hold is not "what is the EV now" but "is
it opening or closing". The decay signal answers that by comparing the current
spread to the setup's own **peak** over the trailing 24h.

It feeds the "close X" half of a consumer's join: when a setup that was hot has
faded, scout says so. Two surfaces expose it:

1. **Inline on every setup** in `GET /api/setups` — the `staleness`,
   `peak_spread_apr_pct`, `decay_from_peak_pct`, `hours_since_peak` fields above.
2. **Per-candidate** via `GET /api/setups/<candidate_id>` — works even when the
   setup is **no longer in the list** (the loudest decay of all).

### `staleness` values

| Value | Meaning | Operative reading |
|---|---|---|
| `fresh` | Holding near its peak. | Keep. |
| `cooling` | Fallen 25–50% from peak. | Watch. |
| `stale` | Fallen ≥50% from peak, **or** spread dropped below the tradeable threshold (30% APR) after having been above it. | Exit candidate. |
| `gone` | Latest point missing (setup no longer detected) **or** spread ≤ 0 (legs flipped / carry reversed). | Close. |
| `unknown` | No history in the window to judge. | No signal. |

The verdict is pure arithmetic over the same 24h spread series that drives the
sparkline and `window_age_hours` — no second engine, no extra data. The window is
reconstructed from raw `funding_snapshot`, so the per-candidate endpoint can speak
to a setup that has already vanished from the live verdict.

## Survival signal

Decay is **descriptive and reactive**: it tells you a window is fading *after* its
spread starts falling from its own peak. Survival is the **predictive** complement:
given the window's age, how many more hours is it likely to last — *before* the
spread moves at all.

It answers the case decay cannot: a window sitting right at its peak reads
`staleness: fresh` (hold!), but if windows on this pair historically die at a
median age of 5h and this one is already 6h old, it is statistically overdue.
`survival_median_remaining_h` flags it; decay stays silent until the drop begins.

### Method

For each pair, the full trailing history (default 45 days) of the spread is split
into **windows** — maximal runs of consecutive hours with spread ≥ the tradeable
threshold (30% APR, the same one `window_age_hours` uses). The lifetimes of those
windows feed a **Kaplan–Meier** estimator of the survival function `S(t) =
P(window lives ≥ t hours)`:

- the **currently-open** window is right-censored (its true lifetime is only a
  lower bound), handled correctly by KM;
- windows already open at the start of the history are left-truncated and dropped
  (we never saw their start);
- a gap in the data ends a window (same conservative rule as `window_age_hours`).

From `S(t)` and the current age `a`:

- **`survival_median_remaining_h`** = smallest `k` with
  `P(T ≥ a+k | T ≥ a) ≤ 0.5` — the headline "how much longer".
- **`survival_median_lifetime_h`** = KM median of the full lifetime distribution.
- **`survival_p_survive_min_hold`** = `P(T ≥ a + min_profitable_hours | T ≥ a)` —
  survival tied directly to profitability.
- **`survival_curve`** = the conditional survival for k = 1..24, so a consumer can
  build its own survival-weighted EV (e.g. `ev_day` discounted by survival).

### Confidence and pooling (honest about thin history)

A pair with fewer than 5 completed windows of its own falls back to a **global
pooled** curve (KM over all pairs' windows), with `survival_pooled: true`. This is
*degraded-but-flagged*, not hidden: `survival_confidence`
(`high`/`medium`/`low`/`none`) and `survival_sample_size` always tell the consumer
how much to trust the number. A `low`/`none` value should be discounted.

Survival is exposed **inline on every setup** in `GET /api/setups` (the fields
above). The per-candidate endpoint below currently carries **decay only** — survival
for a setup that has dropped out of the verdict is a planned follow-up.

Config knobs (env, `FUNDING_SCOUT_` prefix): `survival_history_days` (45),
`survival_window_threshold_pct` (30.0), `survival_min_windows` (5).

## `GET /api/setups/<candidate_id>`

Decay verdict for one setup, addressed by its stable `candidate_id`
(`TICKER:LONG:SHORT`). Use it to ask "is the setup I opened still alive?" without
scanning the whole board.

```bash
curl -s http://<host>:8050/api/setups/SOL:lighter:hyperliquid
```

```jsonc
{
  "computed_at": "2026-06-07T09:53:36+00:00",
  "candidate_id": "SOL:lighter:hyperliquid",
  "ticker": "SOL",
  "long_venue": "lighter",
  "short_venue": "hyperliquid",
  "present": true,                 // still in the current verdict?
  "current_spread_apr_pct": 88.2,  // null if not present
  "snapshot_ts": 1700000000,       // null if DB empty
  "spread_sparkline": "▆▇█▆▅▃▂▁",
  "window_age_hours": 5,
  "peak_spread_apr_pct": 142.0,
  "hours_since_peak": 3,
  "decay_from_peak_pct": 37.9,
  "staleness": "cooling"
}
```

- `present` is `false` when the legs no longer both report at the latest snapshot
  (decayed out / flipped). The decay fields still describe the trailing window.
- A malformed id (not exactly `TICKER:LONG:SHORT`) returns **400**.
- An empty DB returns a valid envelope with `present: false`,
  `staleness: "unknown"`, `snapshot_ts: null` — never a 500.

## `setup_snapshot` table

Raw funding (`funding_snapshot`) was always persisted, but the *verdict* of the
detectors used to be computed on the fly and never stored.

> **Note on decay's data source.** The decay signal above is computed from raw
> `funding_snapshot` — for the cross-DEX same-ticker detector the spread is a pure
> function of two raw funding rates, so its history reconstructs exactly and the
> full retained window is available immediately. `setup_snapshot` exists for the
> next step: detectors whose verdict is *not* reconstructible from raw (calendar
> flags, cost-model EV, correlated pairs) need their computed value persisted to
> have any history at all.

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
