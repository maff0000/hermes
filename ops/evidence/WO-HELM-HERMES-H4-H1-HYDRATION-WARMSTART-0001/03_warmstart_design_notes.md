# 03 — H4 warm-start design notes

## Boot path
```
[boot] main.py lifespan builds H4 producer
 → warmstart_h4_from_env(state.candle_h4_producer, now, logger)
     → gate: ENABLED? AUTHORISED? (else cold-start no-op / SystemExit(104))
     → [1] read current UTC clock
     → [2] h4_open = h4_bucket_open(now); block=[h4_open, h4_open+4h)
     → [3] read governed H1 history for the block (bounded ZRANGEBYSCORE)
     → [4] classify + filter to complete-OK on-grid H1 of the block
     → [5] seed producer._buf[XAU_USD][h4_open_epoch] with 0..4 children; set _current
 → [6] live on_h1_close hooks continue
 → [7] H4 seals ONLY on genuine live roll-over (unchanged semantics)
```
Runs immediately before the existing D1 warm-start (same producer object; H4 buffer seeded, then D1 buffer seeded).

## `CanonicalH4Producer.hydrate(children, *, now, instrument)`
Pure (seeds memory only — no I/O, no publish); deterministic + **idempotent** (REPLACES the current block buffer);
bounded by the 4-child block. Per-child classification via `h1_hydration_reject_reason` (order: timeframe →
instrument → malformed/non-UTC → outside-block → wrong-boundary(off the hour) → duplicate → not-OK/incomplete).
Report: attempted/succeeded, source, block start/end UTC, candidate/accepted/rejected(+reasons), buffer_length,
remaining_children_required, h4_complete_4of4, h4_published_by_hydration=false, h4_status_after_hydration.

## Why hydration, not backfill
Seeds ONLY the current unsealed block; writes nothing; publishes nothing; never synthesises a missing H1; rejects
non-OK/missing/off-grid loud. The genuine H4 seal still comes from a live roll-over → the sealed OK H4 then flows
to the D1 producer (D1 can reach 6/6). Import-safe: no redis import, no client at import, no socket/thread/SQL.
