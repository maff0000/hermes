# 10 — Sample mock validation payloads (UTC)

Active H4 block under test: open `2026-07-01T02:00:00.000Z` → end `2026-07-01T06:00:00.000Z`.

## (a) Governed H1 history envelope read by the warm-start
```json
{ "status": "OK",
  "data": { "instrument": "XAU_USD", "timeframe": "H1", "timestamp_utc": "2026-07-01T02:00:00.000Z",
    "open": 2000.0, "high": 2010.0, "low": 1990.0, "close": 2005.0, "volume": 100,
    "is_closed": true, "source_count": 1, "expected_source_count": 1, "source_coverage": 1.0, "gap_state": "NONE" } }
```

## (b) Partial hydration report (2/4) — buffer seeded, awaiting live
```json
{ "attempted": true, "succeeded": true, "instrument": "XAU_USD",
  "h4_block_start_utc": "2026-07-01T02:00:00.000Z", "h4_block_end_utc": "2026-07-01T06:00:00.000Z",
  "candidate_count": 2, "accepted_count": 2, "accepted_child_open_epochs": [1782756000, 1782759600],
  "rejected_count": 0, "rejected": [], "buffer_length": 2, "remaining_children_required": 2,
  "h4_complete_4of4": false, "h4_published_by_hydration": false, "h4_status_after_hydration": "AWAITING_LIVE_H1",
  "source_selected": "redis_h1_history" }
```

## (c) Rejections (cross-bucket + off-grid + non-OK)
```json
{ "buffer_length": 1, "accepted_count": 1, "rejected_count": 3,
  "rejected": [ {"open_epoch": 1782752400, "reason": "OUTSIDE_ACTIVE_H4_BLOCK"},
                {"open_epoch": 1782757800, "reason": "WRONG_BOUNDARY_OFF_FIXED_GRID"},
                {"open_epoch": 1782759600, "reason": "NOT_OK_OR_INCOMPLETE_H1"} ] }
```

## (d) Complete 4/4 — READY, not published by hydration
```json
{ "buffer_length": 4, "h4_complete_4of4": true, "h4_published_by_hydration": false,
  "h4_status_after_hydration": "READY_PENDING_LIVE_ROLLOVER" }
```

## (e) Cold-start gate no-op (ENABLED=false)
```json
{ "attempted": false, "skipped": true, "reason": "COLD_START_STRATEGY_ACTIVE", "buffer_length": 0 }
```
log: `H4 warmstart skipped: cold-start strategy active`
