# Final Report — Forward History Writer (code-only PR)
**WO-HELM-HERMES-GOLD-MTF-FORWARD-HISTORY-WRITER-0001 · HELM/HERMES**
**Expected verdict: GREEN_PR_OPEN_GOLD_MTF_FORWARD_HISTORY_WRITER_CODE_ONLY**

## Delivered (code-only)
- `utils/candle_history_forward_writer_v1.py` — CandleHistoryForwardWriter (snapshot closed canonical
  envelope -> immutable history key + ZSET index, all guards), DisabledHistoryForwardWriter (no-op),
  parse_forward_timeframes / parse_forward_instruments (fail-closed), build_history_forward_writer_from_env.
- `tests/test_candle_history_forward_writer_v1.py` — 27 tests, all green.

## Trigger policy encoded
on_canonical_close (M1/M5/M15/H1) + on_h4_sealed (H4 complete 4/4). Closed + status-OK only; forming and
H4-incomplete skipped (never written as OK); no synthesis. Hooks NOT wired into runtime here (separate WO).

## Guards (every write)
instrument(XAU_USD, alias canonicalised, allowlist) -> tf(grid, no D1) -> closed -> status OK ->
history block -> validate_candle_contract -> build_history_write_plan -> assert_history_target(key+index)
-> idempotent SET(TTL 3,024,000s) + ZADD(score=member=open_epoch). Conflict at same epoch -> FAIL LOUD.

## Tests prove
disabled no-op · enabled-unauthorised fail loud · missing/empty allowlist+timeframes fail loud · XAUUSD alias
never an output key · non-XAU rejected · D1 rejected (parse + write) · latest/unversioned rejected by guard ·
M1/M5/M15/H1 + H4-complete written to history shape · H4 forming/incomplete not written as OK · validate &
assert_history_target before write · TTL applied · index score/member=open_epoch · idempotent re-write safe ·
caller latest envelope not mutated · no regime fields. Full candle suite: 267 passed (no regression).

## Exclusions honoured
No deploy/restart/activation/Redis write/SQL write/backfill/D1/regime/XAUUSD output/shadow/consumer cutover/
Proteus/structure_engine/opportunistic refactor. No existing file modified.

## Risks / notes for R2D2
1. Hooks are not yet wired into the live seam/producer — intentional (build->wire->activate cadence). The
   actual forward-write begins only after a future integrate+activate WO sets the gates. Until then history
   remains the snapshot from the backfill WO.
2. History block reuses backfill field names (backfill_run_id / source_table) for schema uniformity; FORWARD
   origin is encoded in their VALUES, not a new field. Flag if a dedicated write_path field is preferred.
3. Conflict policy is fail-loud (no silent overwrite); a genuine candle correction would need an explicit WO.
