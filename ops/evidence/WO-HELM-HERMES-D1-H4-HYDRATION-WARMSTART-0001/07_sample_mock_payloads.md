# 07 — Sample mock validation payloads (UTC)

All timestamps UTC. D1 block under test: open `2026-06-25T22:00:00.000Z` → end `2026-06-26T22:00:00.000Z`.

## (a) Governed H4 history envelope read by the warm-start (mock)
```json
{
  "status": "OK",
  "data": {
    "instrument": "XAU_USD", "timeframe": "H4",
    "timestamp_utc": "2026-06-25T22:00:00.000Z",
    "open": 2000.0, "high": 2010.0, "low": 1990.0, "close": 2005.0, "volume": 100,
    "is_closed": true, "source_count": 4, "expected_source_count": 4,
    "source_coverage": 1.0, "gap_state": "NONE"
  }
}
```

## (b) Partial hydration report (3/6) — buffer seeded, AMBER
```json
{
  "attempted": true, "succeeded": true, "instrument": "XAU_USD",
  "d1_block_start_utc": "2026-06-25T22:00:00.000Z",
  "d1_block_end_utc":   "2026-06-26T22:00:00.000Z",
  "candidate_count": 3, "accepted_count": 3,
  "accepted_child_open_epochs": [1782424800, 1782439200, 1782453600],
  "rejected_count": 0, "rejected": [],
  "buffer_length": 3, "remaining_children_required": 3,
  "d1_complete_6of6": false, "d1_published_by_hydration": false,
  "d1_remains_gated_amber": true,
  "d1_status_after_hydration": "AMBER_AWAITING_LIVE_H4"
}
```

## (c) Rejections report (cross-day + off-grid + non-OK)
```json
{
  "buffer_length": 1, "accepted_count": 1, "rejected_count": 3,
  "rejected": [
    {"open_epoch": 1782410400, "reason": "OUTSIDE_ACTIVE_D1_BLOCK"},
    {"open_epoch": 1782428400, "reason": "WRONG_BOUNDARY_OFF_FIXED_GRID"},
    {"open_epoch": 1782439200, "reason": "NOT_OK_OR_INCOMPLETE_H4"}
  ],
  "d1_remains_gated_amber": true, "d1_published_by_hydration": false
}
```

## (d) Complete 6/6 — READY, but NOT published by hydration
```json
{
  "buffer_length": 6, "d1_complete_6of6": true,
  "d1_published_by_hydration": false, "d1_remains_gated_amber": true,
  "d1_status_after_hydration": "READY_PENDING_LIVE_ROLLOVER",
  "publication_note": "complete 6/6 present -> producer READY; existing D1 semantics publish ONLY on the next LIVE H4 roll-over (no retroactive/fake seal) -> D1 stays AMBER until that genuine live seal"
}
```

## (e) Cold-start gate no-op (ENABLED=false)
```json
{ "attempted": false, "skipped": true, "reason": "COLD_START_STRATEGY_ACTIVE", "buffer_length": 0, "d1_remains_gated_amber": true }
```
log: `D1 warmstart skipped: cold-start strategy active`
