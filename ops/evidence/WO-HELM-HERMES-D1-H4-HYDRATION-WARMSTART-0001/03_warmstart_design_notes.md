# 03 — Warm-start design notes

## Boot path implemented
```
[Service boot]
 → main.py lifespan builds H4 producer (build_h4_producer_from_env builds the D1 producer inside it)
 → warmstart_d1_from_env(state.candle_h4_producer.d1_producer, now=datetime.now(UTC), logger=logger)
     → gate: ENABLED? AUTHORISED? (else cold-start no-op / SystemExit(103))
     → [1] read current UTC clock (now)
     → [2] d1_open = d1_bucket_open(now); assert_d1_open_anchor (22:00:00 UTC fixed)
     → [3] read_block_h4_children_from_redis(client, XAU_USD, d1_open)   # bounded, governed H4 history
     → [4] producer.hydrate(children, now): classify + filter to current block, seed buffer
     → [5] buffer seeded with 0..6 eligible OK H4 children, _current set to today's bucket
 → [6] normal live H4 close hooks (on_h4_close) continue and seal on the next live roll-over
```

## `CanonicalD1Producer.hydrate(children, *, now, instrument)` (the compilation-loop seed)
- Pure: seeds `self._buf[XAU_USD][d1_open_epoch]` + `self._current[XAU_USD]`. **No Redis I/O, no publication.**
- Deterministic + **idempotent**: REPLACES the current block buffer (re-run → identical state).
- Per-child classification via `hydration_reject_reason` (deterministic order): WRONG_TIMEFRAME → INSTRUMENT_NOT_ALLOWLISTED
  → MALFORMED/NON_UTC → OUTSIDE_ACTIVE_D1_BLOCK → WRONG_BOUNDARY_OFF_FIXED_GRID → DUPLICATE_CHILD → NOT_OK_OR_INCOMPLETE_H4.
- Report (for R2D2): attempted/succeeded, source, `d1_block_start_utc`/`d1_block_end_utc`, candidate_count,
  accepted_count + `accepted_child_open_epochs`, rejected_count + `rejected[{open_epoch, reason}]`, buffer_length,
  `remaining_children_required`, `d1_complete_6of6`, `d1_published_by_hydration=false`, `d1_remains_gated_amber=true`,
  `d1_status_after_hydration` (AMBER_AWAITING_LIVE_H4 | READY_PENDING_LIVE_ROLLOVER), publication_note.

## Why this is hydration, not backfill
- It seeds ONLY the **current unsealed** block (the one containing `now`); it never reaches back to seal a prior day.
- It writes nothing and publishes nothing; the genuine first D1 seal still comes from a **live** roll-over.
- It never synthesises a missing child and never launders a gap — non-OK/missing/off-grid children are rejected loud.

## Import safety
`utils/candle_d1_hydration_v1.py` imports no `redis`, constructs no client, opens no socket, starts no thread, runs
no SQL at import. The read client is **injected** (or taken from the producer's existing canonical writer
`redis_client` — no hard-coded host/port/db/credential anywhere).
