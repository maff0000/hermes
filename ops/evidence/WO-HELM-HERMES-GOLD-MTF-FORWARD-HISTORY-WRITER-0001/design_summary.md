# Design Summary — Forward History Writer
**WO-HELM-HERMES-GOLD-MTF-FORWARD-HISTORY-WRITER-0001 · code-only · HELM/HERMES**

## Problem
History (M1/M5/M15/H1=38,903 keys, H4=158 keys) is a one-time 35-day snapshot. Under the 35-day TTL it
decays unless HERMES appends closed candles forward. Architect ruling: forward-history writer before D1.

## Approach — snapshot, don't re-derive
`utils/candle_history_forward_writer_v1.py` adds `CandleHistoryForwardWriter`. It does NOT query SQL or
re-derive: it **snapshots the already-built, already-validated canonical envelope** (the exact payload the
service writes to `:latest:v1`) into the immutable history key, adding only a `history` provenance block.
The block schema is IDENTICAL to the backfill writer's 5 fields, so backfill + forward records are uniform;
the FORWARD origin is carried in `backfill_run_id` and `source_table` (no new/forbidden fields).

This means latest and history agree exactly at each open_epoch, and every existing governance guard runs:
`validate_candle_contract` (forbidden-token scan) -> `build_history_write_plan` (re-validate + derive key/
index/TTL) -> `assert_history_target` on key AND index -> idempotent `SET(ex=TTL)` + `ZADD(score=member=open_epoch)`.

## Triggers (hooks; runtime wiring is a SEPARATE integrate WO)
- M1/M5/M15/H1: `on_canonical_close(envelope)` — when the canonical latest close event is emitted.
- H4: `on_h4_sealed(envelope)` — when the derived H4 producer seals a COMPLETE 4/4 bucket.
Only CLOSED, status-OK candles are written. Forming -> skip. H4 incomplete (status != OK) -> skip, never
written as OK. No synthesis of missing children. This PR does NOT wire the hooks into the live seam/producer
or change latest behaviour — wiring + activation is the next WO (mirrors the H4 build->wire->activate cadence).

## Idempotency / protection
key = `:history:v1:{open_epoch}`, ZSET score=member=open_epoch. Re-write of the SAME candle is a safe
idempotent overwrite (refreshes TTL; single index member; duplicate counted separately). A DIFFERENT candle
truth at the same open_epoch FAILS LOUD (GOV-CANDLE-HIST-FWD-020) — history is never silently rewritten.
The caller's latest envelope is deep-copied, never mutated. No deletes, no backfill, no D1, no latest writes.
