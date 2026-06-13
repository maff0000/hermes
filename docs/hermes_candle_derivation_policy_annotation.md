# HERMES Candle Derivation-Policy Annotation (B-DIV reconciliation)

**WO:** `WO-HELM-HERMES-CANDLE-DERIVATION-POLICY-ANNOTATION-0001`
**Status:** PR-ready (migration/runner/tests/docs/evidence). **No live apply, no forward `--execute`,
no Redis, no restart.** All timestamps UTC. HERMES-only.

## Problem (R2D2 B-DIV)

- **Historical** Phase-2 M30/H4 backfill counted *all* constituent M1 rows regardless of each
  M1's `complete` flag → `HISTORICAL_ALL_M1_COUNTED`.
- **Forward** runner counts **only `complete=1`** M1 → `FORWARD_COMPLETE_M1_ONLY` (strict).
- Without metadata, `complete=1` means different things across historical vs forward rows.

This WO makes the policy **machine-visible per row** so no M30/H4 row is semantically ambiguous —
**by annotation, not re-derive, not silent cutover**. Historical OHLC is never rewritten.

## Parts

| Part | Artefact |
|---|---|
| Policy columns | `migrations/023_candle_derivation_policy_columns.sql` (ALTER ADD COLUMN ×11 on candles_M30/H4, INSTANT, append-only, **not applied live in this PR**) |
| Historical annotation | `scripts/annotate_candle_derivation_policy.py` (dry-run default; sets HISTORICAL policy on `derivation_policy IS NULL` rows; never touches OHLC/complete/timestamp; fail-loud if columns absent) |
| Forward write | `utils/forward_derivation_runner.py` upsert now writes the policy columns for forward rows |
| Gate | `GOV-FWD-BDIV-001/002` (below) |
| Payload | `utils/candle_features.build_candle_feature(derivation_policy_meta=…)` exposes policy fields |

### Policy column set (both tables)
`derivation_policy, source_complete_policy, source_policy_epoch, source_expected_candle_count,
source_actual_candle_count, source_complete_candle_count, source_incomplete_candle_count,
source_missing_candle_count, derivation_run_id, derivation_generated_at_utc, derivation_policy_note`
(all NULLABLE; existing rows NULL until annotated).

### Policy values
- **Historical** (existing rows, via annotation runner): `derivation_policy=HISTORICAL_ALL_M1_COUNTED`,
  `source_complete_policy=ALL_M1_COUNTED`, `source_policy_epoch=PHASE2_BACKFILL_PRE_STRICT_COMPLETE_POLICY`,
  note explaining the all-M1-counted origin.
- **Forward** (new rows, via runner): `derivation_policy=FORWARD_COMPLETE_M1_ONLY`,
  `source_complete_policy=COMPLETE_ONLY`, `source_policy_epoch=FORWARD_STRICT_COMPLETE_POLICY_V1`,
  + expected/actual/complete/incomplete/missing counts + run_id + generated_at_utc.

## Historical annotation plan (gated; NOT executed here)

```sql
-- per table; sets ONLY policy columns where derivation_policy IS NULL; OHLC/complete/timestamp untouched
UPDATE candles_M30 SET derivation_policy='HISTORICAL_ALL_M1_COUNTED',
  source_complete_policy='ALL_M1_COUNTED', source_policy_epoch='PHASE2_BACKFILL_PRE_STRICT_COMPLETE_POLICY',
  derivation_policy_note='…', derivation_generated_at_utc=UTC_TIMESTAMP(3)
  WHERE derivation_policy IS NULL;   -- expected ~36,377 rows
-- candles_H4: expected ~4,737 rows
```
Dry-run reports `would_annotate` per table. Reversible/auditable: a row's policy can be re-set;
no OHLC/complete change to undo. Run via `annotate_candle_derivation_policy.py --execute --confirm`
(Phase-2, after migration 023 applied + R2D2 audit).

## GOV-FWD-BDIV gate (updated)

- **`GOV-FWD-BDIV-001`** — live `--execute` blocked unless `reconciliation_ack=True`.
- **`GOV-FWD-BDIV-002`** — `reconciliation_ack=True` is **rejected** unless `verify_reconciliation()`
  confirms: policy columns present on both tables **and** zero rows with `derivation_policy IS NULL`
  (i.e. historical rows annotated). **No silent ack** — ack without verified annotation fails loud.
- Forward write also `GOV-FWD-POLICY-001` fails loud if policy columns are missing.

Dry-run is always allowed. This WO does **not** supply `reconciliation_ack` and does not execute.

## Boundaries

No live apply (023), no annotation execute, no forward `--execute`, no Redis/publisher, no restart,
no `/etc`/systemd/cron, no cross-service edits, no OHLC rewrite, no hidden defaults, no hardcoded
DB target (`env_config`).

## Next (gated, after R2D2 audit + merge)

1. apply migration 023 → 2. run annotation `--execute --confirm` (historical → annotated) →
3. verify (`verify_reconciliation` true) → 4. *then* consider forward `--execute` with
`reconciliation_ack` (which is now machine-gated to require the above).
