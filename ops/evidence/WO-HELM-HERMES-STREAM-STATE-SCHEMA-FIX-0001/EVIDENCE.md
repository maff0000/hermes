# Evidence — WO-HELM-HERMES-STREAM-STATE-SCHEMA-FIX-0001

**Author:** Helm (DevOps Lead) — HELM-HERMES lane
**Date (UTC):** 2026-06-01T08:42:50Z
**Branch:** `wo/WO-HELM-HERMES-STREAM-STATE-SCHEMA-FIX-0001` (off `main` @ 5a36050)
**Worktree:** `/srv-dev/worktrees/wo-WO-HELM-HERMES-STREAM-STATE-SCHEMA-FIX-0001`
**Self-verdict:** `GREEN_HERMES_STREAM_STATE_SCHEMA_FIX_READY_FOR_R2D2_AUDIT`

## Purpose
Fix the `stream_state` truncation defect so `hermes_service_health` can function as the
process-level HERMES health contract.

## Defect (confirmed live, read-only)
- `utils/watchdog.py` `StreamState` defines **8** values: `DISCONNECTED, CONNECTING,
  CONNECTED_UNPROVEN, FLOWING, PARTIAL_FLOWING, STALE, RECOVERING, FAILED`.
- The live `tradingSignals.hermes_service_health.stream_state` ENUM (defined in
  `migrations/001_hermes_health_and_incidents.sql`) has only **7** — missing
  `PARTIAL_FLOWING`. See `enum_before_live_db.txt`.
- Write path `utils/watchdog.py:160` `UPDATE hermes_service_health SET stream_state=%s …`.
  The watchdog sets `PARTIAL_FLOWING` at `utils/watchdog.py:635` on the
  FLOWING→PARTIAL_FLOWING transition. MariaDB rejects it with `(1265) Data truncated for
  column stream_state` → every such UPDATE fails → the process-level health contract is
  silently non-functional.
- Repo migration 001 ENUM == live ENUM (no drift).

## Fix
`migrations/013_stream_state_partial_flowing.sql` — `ALTER TABLE hermes_service_health
MODIFY COLUMN stream_state ENUM(…8 values…)`:
- **Append-only:** new migration file 013; migrations 001–012 untouched.
- **Ordinal-safe:** the 7 original values keep their original order (ordinals 1..7);
  `PARTIAL_FLOWING` is appended as ordinal 8. No existing stored row changes meaning.
- **Idempotent:** re-applying the `MODIFY` sets the same definition — no error, no data change.
- **Scope-guarded:** touches only `hermes_service_health.stream_state`.

## Scope checklist (WO items 1–11)
1. **Schema inspected** — `hermes_service_health` defined in migration 001; live def captured.
2. **stream_state type** — `ENUM(...7...)` NOT NULL DEFAULT 'DISCONNECTED' (live + repo match).
3. **Emitted values** — 8 from `StreamState` (`streamstate_emitted_values.txt`).
4. **Safe migration produced** — `migrations/013_stream_state_partial_flowing.sql`.
5. **Append-only** — yes (new file 013).
6. **Old migrations not edited** — confirmed (001–012 unchanged).
7. **Schema test** — `tests/test_stream_state_schema.py` proves all 8 states fit, PARTIAL_FLOWING
   present, append-only, ordinal preservation, no destructive SQL, single-table scope.
8. **Runtime health-write test** — `TestRuntimeWritePathCoverage` proves the runtime-emitted
   `PARTIAL_FLOWING` constant is enum-covered (DB-free; a full DB round-trip write requires the
   migration applied, which is gated to R2D2 + Architect — see "Not done" below).
9. **No change to market data / candles / backfill / feed mode / strategy / ARES / Falcon /
   Redis** — migration is single-`ALTER` on `hermes_service_health.stream_state`; only two new
   files added (migration + test) + this evidence; no existing code modified.
10. **Tests run** — 9 passed (`test_output.txt`); sibling `test_stream_silent_stall_recovery.py`
    20 passed (no regression).
11. **Evidence produced** — this directory + fabric key
    `helm:audit:hermes_stream_state_schema_fix:20260601T0842Z:v1`.

## Observations (transparent, non-blocking)
- **Row count now = 1, not 0.** The 2026-05-31 R2D2 audit reported `hermes_service_health` = 0
  rows; live count is now **1** (the migration-001 seed row, `stream_state='DISCONNECTED'`).
  Point-in-time difference; does not change the fix. The seed value is among the original 7,
  so appending PARTIAL_FLOWING preserves it exactly.
- **`schema.sql` not updated.** `schema.sql` does not define `hermes_service_health` (migrations
  are the DDL source). Consolidating `schema.sql` + `migrations/` into one canonical DDL is a
  separate future WO (blueprint section 11). Out of scope here.

## Not done (by design — boundary)
- **Migration NOT applied** to any live or scratch DB. Applying it is a runtime action, gated
  behind R2D2-HERMES audit + Architect authorisation. No service restart. No live DB write.
- A full DB round-trip write of `PARTIAL_FLOWING` (proving no truncation post-apply) belongs to
  the apply step after R2D2 audit; recommended as the verification gate at apply time.

## Boundaries honoured
no repo detach · no Redis namespace cleanup · no canary relocation · no Docker · no feed-mode
change · no Dolos resurrection · no backfill · no live backup · no strategy work · no runtime
restart · no deletion/pruning · no secret exposure (DB queried via env creds, never printed).

## Git governance
one WO · one branch · one worktree · one PR · commit prefix
`WO-HELM-HERMES-STREAM-STATE-SCHEMA-FIX-0001:` · no force/rebase/squash.
