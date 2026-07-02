# 06 — Behaviour on zero / partial / complete 4×H1

- **Zero**: empty buffer, `remaining=4`, `_current` set, live tracking continues, no crash. `test_empty_history_boundary_buffer_zero`.
- **Partial (0–3)**: eligible H1 seeded chronologically (order + values preserved), `remaining=4−n`. `test_partial_hydration_seeds_two_in_order`, `test_warmstart_reads_redis_h1_history_no_writes`.
- **Complete (4)**: `h4_complete_4of4:true`, `h4_status_after_hydration:READY_PENDING_LIVE_ROLLOVER`, **`h4_published_by_hydration:false`** — existing semantics seal only on a live roll-over; no fake seal. `test_complete_four_ready_but_not_published_by_hydration`.
- **Rejections** (fail-loud, never silent): OUTSIDE_ACTIVE_H4_BLOCK (cross-bucket before/after), WRONG_BOUNDARY_OFF_FIXED_GRID (off the hour), NOT_OK_OR_INCOMPLETE_H1, DUPLICATE_CHILD, WRONG_TIMEFRAME, INSTRUMENT_NOT_ALLOWLISTED, MALFORMED/NON_UTC. Tests 3,4,5,6,7 + `test_warmstart_rejects_non_ok_from_history`.

## End-to-end restart-spanning fix (test 13)
`test_restart_spanning_h4_then_live_rollover_seals_complete_and_feeds_d1`: boot mid-bucket with 2 H1 already in
history → hydrate 2/4 (writes nothing) → live 04:00 + 05:00 H1 closes → 4/4 → next-bucket 06:00 H1 triggers a
GENUINE live roll-over seal → H4 published `status=OK source_count=4` (the ONLY Redis write) → offered to the D1
producer as a complete OK H4 child (`{status:OK, source_count:4}`). This is exactly the 06:00-H4-2/4 trap fixed.
