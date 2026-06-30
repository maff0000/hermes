# 06 — Behaviour on zero / partial / complete 6×H4 availability

## Zero relevant H4 children
`hydrate([], now)` → buffer_length 0, `succeeded:true`, `remaining_children_required:6`,
`_current` set to the current block. Live tracking continues; no crash; D1 AMBER.
Test: `test_empty_history_boundary_buffer_zero`.

## Partial (0–5 valid children)
Eligible children seeded in chronological order; values + order preserved; `remaining_children_required =
6 − accepted`. D1 stays AMBER/pending awaiting live H4 closes.
Tests: `test_partial_day_hydration_seeds_three_in_order` (3 in, buffer 3, remaining 3, order/values preserved),
`test_warmstart_reads_redis_h4_history_no_writes`.

## Complete (all 6 valid children)
`d1_complete_6of6:true`, `d1_status_after_hydration:"READY_PENDING_LIVE_ROLLOVER"`, **`d1_published_by_hydration:false`**,
`d1_remains_gated_amber:true`. Existing D1 producer semantics publish ONLY on a live H4 roll-over, so hydration
does **not** fake a seal — it leaves the producer READY and documents the reason.
Test: `test_complete_six_ready_but_not_published_by_hydration`.

### End-to-end fix proof (no fake seal)
`test_warmstart_then_live_rollover_publishes_genuine_seal`: hydrate the full current block (writes nothing —
`client.sets == []`), then a **live** next-day H4 `on_h4_close` rolls over and seals → `published:true`,
`status:OK`, `source_count:6`, and that live seal is the **only** Redis write. This is the reset-trap fix: the
day's early H4 children survive a restart and the first clean D1 seal is no longer pushed forward — yet D1 only
goes GREEN on a genuine live seal.

## Rejection (any bad child) — fail loud, never silent GREEN
Missing → simply absent (→ <6 → pending). Duplicate → `DUPLICATE_CHILD`. Malformed → `MALFORMED_*`. Non-UTC naive
→ `NON_UTC_NAIVE_TIMESTAMP`. Non-OK/incomplete → `NOT_OK_OR_INCOMPLETE_H4`. Wrong boundary (off fixed grid) →
`WRONG_BOUNDARY_OFF_FIXED_GRID`. Outside block → `OUTSIDE_ACTIVE_D1_BLOCK`. All recorded in `rejected[]` with the
open epoch + reason; none enter the buffer.
Tests: `test_cross_day_filter_discards_pre_anchor_h4`, `test_wrong_boundary_off_grid_rejected`,
`test_non_ok_child_rejected`, `test_duplicate_child_deduplicated_with_evidence`, `test_non_h4_and_non_xau_rejected`,
`test_warmstart_rejects_non_ok_from_history`.
