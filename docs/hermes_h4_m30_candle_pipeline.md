# HERMES H4 / M30 Durable Candle Pipeline

**WO:** `WO-HELM-HERMES-CANDLE-H4-M30-PIPELINE-BUILD-0001`
**Status:** PR-ready (code/migration/test/design only — NO live apply in this WO).
**Architect ruling:** H4 and M30 are IN v1 HERMES scope; both derived from canonical M1 via
the existing `DERIVE_FROM_CANONICAL_M1` pattern. **All timestamps UTC.**

## What this adds

| Artefact | File | Purpose |
|---|---|---|
| `candles_M30` table | `migrations/019_candles_m30.sql` | durable M30 OHLCV (mirror of candles_H1) |
| `candles_H4` table | `migrations/020_candles_h4.sql` | durable H4 OHLCV (mirror of candles_H1) |
| recovery registration | `migrations/021_recovery_library_h4_m30.sql` | `CANDLE_M30` (order 33), `CANDLE_H4` (order 43), `DERIVE_FROM_CANONICAL_M1` |
| derivation | `utils/m1_deriver.py` | registers `M30`=1800s, `H4`=14400s in `TIMEFRAME_SECONDS` (else-branch handles them) |
| backfill | `scripts/backfill_candles_h4_m30.py` | idempotent historical backfill (dry-run default) |
| tests | `tests/test_candles_h4_m30.py` | aggregation/boundary/idempotency proofs |

## Derivation contract

- **Source:** `canonical_m1` only (canonical M1 truth), per-instrument, deterministic.
- **Bucketing (UTC-aligned, epoch-truncated):** M30 → `:00`/`:30`; H4 → `00/04/08/12/16/20` UTC.
- **Aggregation:** `open` = first M1 open, `high` = max M1 high, `low` = min M1 low,
  `close` = last M1 close, `volume` = sum of constituent M1 volumes.
- **Expected counts:** M30 = 30 M1, H4 = 240 M1. `complete = (m1_count == expected)`.
- **Partial windows** (e.g. the 2026-06-10/11 outage) are written `complete=0` and surfaced
  in the backfill summary — **never silently published as complete**.
- **Current forming bucket** is excluded (`end = last_closed_bucket_end(now, tf)`).
- **Idempotency:** upsert on `UNIQUE(instrument, timestamp)`; a re-run is safe (deterministic
  values). A **complete** derivation refreshes the row; a **partial** derivation never
  downgrades or clobbers an existing complete candle
  (`complete = GREATEST(complete, VALUES(complete))`, OHLC only updated when `VALUES(complete)=1`).

## Boundary note — OPEN ARCHITECT QUESTION (recorded, not a blocker)

H4 is **UTC-00:00-anchored** per the ruling, consistent with M5/M15/H1 (all UTC-anchored).
**D1 alone uses the 22:00 UTC forex-day boundary.** Consequently UTC H4 buckets (e.g.
`20:00–24:00`) straddle the 22:00 D1 boundary and do **not** tile D1 cleanly. If clean
H4→D1 nesting (a forex-22:00-anchored H4) is later required, that is a separate ruling and a
follow-up change to `TIMEFRAME_SECONDS`/bucket math. This WO implements the ruled UTC-aligned H4.

## Volume semantics (inherited, documented)

Derived volume = sum of constituent M1 volumes. Live M1 volume = tick count; repaired M1
volume = broker-reported. This is a known semantic mismatch (per `m1_deriver` WO-D); it does
not affect OHLC truth.

## Live-apply plan (Phase 2 — NOT executed here; requires explicit Architect authorisation)

1. **Preflight:** runtime SHA pinned; service active; `candles_M30`/`candles_H4` absent;
   `canonical_m1` present and current; disk safe; no `/etc`/systemd/cron writes.
2. **Backup:** fresh logical dump of `canonical_m1` (+ schema of new tables once created) to an
   approved evidence dir; gzip integrity + md5; restore-smoke where practical.
3. **Apply migrations** 019, 020 (CREATE TABLE, inert) then 021 (recovery_library INSERT).
   `SET SESSION lock_wait_timeout=30`; no `max_statement_time` on DDL; fail-loud (CREATE is cheap).
4. **Expected rows (estimate):** per instrument over the canonical_m1 span — M30 ≈ M1_minutes/30,
   H4 ≈ M1_minutes/240. Verify counts after backfill against canonical M1 coverage.
5. **Backfill:** `scripts/backfill_candles_h4_m30.py` dry-run first (per-instrument complete/partial
   counts), then `--execute --confirm`. Batched per instrument/timeframe; load-monitored.
6. **Validation queries:** OHLC consistency vs M1 windows; complete-flag correctness; no duplicate
   (instrument,timestamp); partial windows align to known outage gaps; recovery_library rows present.
7. **Rollback / stop:** migrations roll back via `DROP TABLE candles_M30/candles_H4` (additive,
   isolated) + `DELETE` the 2 recovery_library rows; backfill is re-runnable. Stop on: schema
   mismatch, duplicate, partial-where-complete-expected, load spike, lock wait, disk issue.
8. **R2D2 audit points:** post-migration schema; dry-run evidence; post-backfill complete/partial
   accounting; equivalence spot-check of derived H4/M30 vs M1.
9. **Container-ready runner:** the backfill + ongoing derivation run as an application-owned
   runner (no host cron/systemd final dependency); scheduling via the HERMES container-internal
   scheduler when productionised.

## Scope guard

This pipeline publishes candle **facts** only. It does not decide what a signal candle is
(HELIOS), nor render trader cockpit output (FALCON). No Falcon/SOLO/NEO/Matt-shaped keys or
logic; no risk/strategy/signal-meaning logic.
