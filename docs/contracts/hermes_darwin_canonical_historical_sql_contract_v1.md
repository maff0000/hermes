# HERMES → DARWIN Canonical Historical Candle SQL Contract v1

**WO:** WO-HELM-HERMES-DEV-DARWIN-DURABLE-CANONICAL-HISTORICAL-AUTHORITY-0001
**Status:** DEV, live-verified, PROD promotion not yet performed (separate gated step)
**Owner:** HERMES (this repository). DARWIN consumes read-only; DARWIN never writes here and never derives
its own H4/D1.

## 1. Purpose and boundary

HERMES owns market truth. This document is the versioned, durable, SELECT-only SQL contract by which
DARWIN (and any other durable-historical-candle consumer) reads that truth — historical past through every
newly-completed live candle, for one continuous, gap-honest, non-fabricated series per timeframe.

```
HERMES durable historical authority  →  DARWIN SELECT-only SQL adapter  →  immutable MarketDataset  →  ATHENA/APOLLO
```

There is **no operational boundary** of the form "backfill ends here / live data lives somewhere else". A
consumer range query issued today, next week, or next year returns both historically-reconstructed and
subsequently live-persisted rows in the same table/view, in the same shape, with no seam.

## 2. Database / schema

- **Database:** `tradingSignals` (MariaDB, same instance/schema HERMES already uses for M1/M5/M15/H1/tick
  durable storage).
- **Migration:** `migrations/027_darwin_canonical_historical_authority.sql` (idempotent: `CREATE TABLE IF
  NOT EXISTS`, `CREATE OR REPLACE VIEW`). Rollback: `migrations/027_darwin_canonical_historical_authority_rollback.sql`.

## 3. Objects

| Object | Kind | Timeframe | Backing |
|---|---|---|---|
| `canonical_candles_m1`  | VIEW  | M1  | `candles_M1` (existing live table, `complete=1` only) |
| `canonical_candles_m5`  | VIEW  | M5  | `candles_M5` (existing live table, `complete=1` only) |
| `canonical_candles_m15` | VIEW  | M15 | `candles_M15` (existing live table, `complete=1` only) |
| `canonical_candles_h1`  | VIEW  | H1  | `candles_H1` (existing live table, `complete=1` only) |
| `canonical_candles_h4`  | TABLE | H4  | New durable storage — see §7 |
| `canonical_candles_d1`  | TABLE | D1  | New durable storage — see §7 |
| `canonical_candles`     | VIEW  | all 6 | `UNION ALL` of the six objects above, uniform shape |

M1/M5/M15/H1 are exposed via a **thin view over the already-trustworthy live tables** — no data is
duplicated, and a row written by the existing live collection path is visible to this contract the instant
it commits (there is no separate ingestion step for those four timeframes). H4/D1 have no pre-existing
trustworthy durable table, so this contract introduces two new physical tables, populated exclusively by
the governed derivation (see §7) via both a one-time historical backfill and the live seal-path hook.

## 4. Row shape (uniform across all 7 objects)

| Column | Type | Meaning |
|---|---|---|
| `instrument` | VARCHAR(20) | Canonical instrument id, e.g. `XAU_USD` |
| `timeframe` | VARCHAR(8) | `M1`\|`M5`\|`M15`\|`H1`\|`H4`\|`D1` |
| `open_time` | DATETIME | Candle **OPEN** time, UTC (naive column; UTC by contract, never mixed — matches every other candle table in this schema) |
| `open`,`high`,`low`,`close` | DECIMAL(12,5) | Price |
| `volume` | INT UNSIGNED | Summed/native volume |
| `is_closed` | TINYINT(1) | Always 1 for every row exposed here (no forming/open candle is ever exposed) |
| `status` | VARCHAR(24) | Always `OK` (an incomplete/short derived window is never stored/exposed — see §8) |
| `source_timeframe` | VARCHAR(8) | `M1`/`M5`/`M15`/`H1` for the direct views (= own timeframe); `H1` for H4; `H4` for D1 |
| `derivation_policy` | VARCHAR(48) | `NONE_DIRECT` for the direct views; `DERIVED_H4_FROM_H1` / `DERIVED_D1_FROM_H4` for H4/D1 |
| `source_policy_epoch` | VARCHAR(48) | `DIRECT_NATIVE_V1` for direct views; H4/D1 carry the governed derivation's own policy epoch string |
| `source_count` / `expected_source_count` | SMALLINT UNSIGNED | 1/1 for direct views; 4/4 for H4 (from H1); 6/6 for D1 (from H4) |
| `source_coverage` | DECIMAL(5,4) | Always `1.0000` for every exposed row (an incomplete window is never exposed) |
| `gap_state` | VARCHAR(24) | Always `NONE` for every exposed row |
| `derivation_run_id` | VARCHAR(64) | Provenance only (which process/run produced the row) — **never part of identity or the idempotency fingerprint** |
| `derivation_generated_at_utc` | DATETIME(3) | When the row was produced (best-available marker for the direct M1/M5/H1 views: the source row's own `created_at`; **`candles_M15` has no `created_at` column**, so this view uses the candle's own `open_time` instead — the honest best-available marker, documented here rather than silently defaulted) |
| `created_at` | TIMESTAMP / DATETIME(3) | Row creation marker (same M15 caveat as above) |

`id` (the per-table surrogate key) is available on each individual per-timeframe object but is **intentionally
omitted from the unified `canonical_candles` view** — it is table-local, not globally unique across the
6-way UNION, and is not part of the contracted identity.

## 5. Identity

**Identity = `(instrument, timeframe, open_time)`.** Enforced by a `UNIQUE KEY` on both `canonical_candles_h4`
and `canonical_candles_d1`; enforced upstream on `candles_M1/M5/M15/H1` the same way already. No two rows
in this contract ever share an identity; no row is ever silently overwritten (see §9).

## 6. Timestamp / UTC semantics

Every `open_time` is the candle's **OPEN** time, UTC, always. H4 buckets open at the fixed NY-5PM-aligned
grid (22:00, 02:00, 06:00, 10:00, 14:00, 18:00 UTC — **not** UTC-midnight-anchored). D1 buckets open at the
fixed 22:00 UTC daily boundary. There is no DST shift, no broker-local anchor, and no UTC-midnight day
anywhere in this contract.

## 7. Derivation (H4 / D1 only)

- **H4** is derived **exclusively** via `utils/candle_h4_derivation_v1.derive_h4()` from canonical H1
  (`candles_H1`, `complete=1`) — the *exact same function* the live H4 seal path uses. There is exactly one
  H4 construction implementation in this codebase; this contract's H4 storage is never fed by a second one.
- **D1** is derived **exclusively** via `utils/candle_d1_derivation_v1.derive_d1()` from canonical durable
  H4 (`canonical_candles_h4`) — never from the legacy `candles_D1`, never from a 24×H1 shortcut.
- A row enters `canonical_candles_h4`/`canonical_candles_d1` **only** when its full expected child set
  (4/4 H1 for H4; 6/6 H4 for D1) is genuinely present and status `OK`. A short/gapped window is counted and
  reported by the backfill tooling but **never stored, never fabricated, never interpolated**.
- Historical backfill: `utils/candle_h4_durable_sql_backfill_v1.py` (H4, full trustworthy H1 range, no
  arbitrary depth cap) and `utils/candle_d1_durable_sql_backfill_v1.py` (D1, full range the canonical H4
  table supports). Both dark/dry-run-by-default, idempotent (`match` on identical re-run), fail-loud on a
  genuine conflicting existing row (raises — a one-shot batch script may safely stop and be re-run).
- Live/ongoing: `utils/candle_durable_sql_writer_v1.py` is offered every freshly-sealed, status-OK H4/D1
  candle by the *existing* governed live derivation/seal path (`CanonicalH4Producer._seal_and_publish`,
  `CanonicalD1Producer._seal_and_publish`) and persists it into the **same** table the backfill writes, via
  the **same** shared row-shape/idempotency contract (`utils/candle_durable_sql_contract_v1.py`). Restart or
  a duplicate seal callback is recognised as `match` (no duplicate write); a genuine conflict is counted and
  returned visibly, never silently lost, and never raised into the live seal path (a durable-SQL fault must
  never break the H4/D1 latest publish, which has already succeeded by the time this hook runs).

## 8. Completeness / gap semantics

Every row exposed by this contract is **complete** (`status='OK'`, `source_coverage=1.0000`,
`gap_state='NONE'`). A missing or short period simply **has no row** at that `open_time` — the contract
never fabricates a placeholder, never interpolates, and never marks a genuine gap as anything other than an
absence. A consumer detects a gap by the absence of an expected `open_time` in a range query, exactly as it
would against the existing M1/M5/M15/H1 tables today.

## 9. Idempotency / no-overwrite guarantee

No code path in this contract ever issues an `UPDATE` against `canonical_candles_h4`/`canonical_candles_d1`.
A write is either a fresh `INSERT` (new identity) or a no-op (`match` — an identical row already exists at
that identity, judged by content fingerprint, not by provenance). A **conflict** (existing row differs from
the candidate at the same identity) is never silently resolved by overwriting either direction — it is a
fail-loud batch-script refusal (backfill) or a visibly-counted, human-repair-worthy anomaly (live writer).

## 10. Legacy — NOT authoritative

- **`candles_H4`** — UTC-00:00-anchored (00/04/08/12/16/20), a *different boundary convention entirely*
  from the NY-5PM 22:00-anchored grid this contract uses; stale since 2026-06-15 (246 rows, 2026-04-01 to
  2026-06-15 only). Never a source, never a target, of this contract.
- **`candles_D1`** — UTC-midnight-anchored, materially gapped (119 gaps >1 day, max gap 5 days). Never a
  source, never a target, of this contract.
- Both remain in the schema, untouched, for any process that still depends on them; this migration does not
  drop, rename, or write to either.

## 11. Redis is not the deep-history authority

HERMES's Redis canonical/history keys (`hermes:candles:*`) remain **bounded operational state** — live
"latest" publication and a governed-depth history cache (e.g. H4's count-based 250-candle retention). They
are never the deep-history source of truth and are never read by this SQL contract or by DARWIN. The durable
MariaDB surface described here is the sole historical authority.

## 12. Supported query pattern

The same shape works for any of the 6 timeframes against the unified view (or the equivalent per-timeframe
object):

```sql
SELECT open_time, open, high, low, close, volume
FROM canonical_candles
WHERE instrument = 'XAU_USD' AND timeframe = 'H1'
  AND open_time >= '2026-08-01 00:00:00' AND open_time < '2026-09-01 00:00:00'
ORDER BY open_time ASC;
```

or, equivalently and typically faster (see the benchmark note below), directly against the per-timeframe
object:

```sql
SELECT open_time, open, high, low, close, volume
FROM canonical_candles_h1
WHERE instrument = 'XAU_USD' AND open_time >= '2026-08-01 00:00:00' AND open_time < '2026-09-01 00:00:00'
ORDER BY open_time ASC;
```

**Indexes a consumer may rely on:** every per-timeframe physical table/underlying table carries a composite
`(instrument, open_time)` index (`idx_instrument_open` on `canonical_candles_h4`/`canonical_candles_d1`;
`idx_instrument_timestamp`/`idx_timestamp` on the M1/M5/M15/H1 base tables) — a range query against a single
per-timeframe object uses that index. The 6-way `UNION ALL` in `canonical_candles` does not itself carry an
index; MariaDB's ability to prune non-matching branches of a `timeframe='<TF>'` filter against a `UNION ALL`
view varies — see the benchmark evidence in the WO's final report for this server/version's actual behaviour.
A consumer that always knows its target timeframe should prefer the per-timeframe object directly.

## 13. Access

Read-only via the dedicated `darwin_ro` MariaDB principal — `SELECT` only, on exactly the 7 objects listed
in §3, nothing else in this schema. See `migrations/027_darwin_readonly_principal_grants.template.sql` for
the grant template (credential itself is a runtime secret, never committed).

## 14. Compatibility

This is contract **v1**. A future breaking change (column rename/removal, identity change, semantic change)
requires a new versioned object set (e.g. `canonical_candles_v2`) — this document's existing objects are
never silently redefined out from under a consumer once published.
