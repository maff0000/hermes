# 04 — Hydration source chosen and why

## Chosen: governed Redis H4 history surface (canonical runtime surface)
`HERMES_D1_WARMSTART_SOURCE` (config, default `redis_h4_history`). Reads:
- index ZSET `hermes:candles:XAU_USD:H4:history:v1:index`
- per-candle `hermes:candles:XAU_USD:H4:history:v1:{open_epoch}`

via `utils/candle_history_v1.history_index_key` / `history_key` (the governed keyspace the H4 producer itself
writes). This is the **canonical runtime surface** already consumed by the indicator/feature/sessions-levels
publishers, so it is the live source of truth for sealed H4 — exactly what the D1 buffer needs to reconstruct.

## Bounded + deterministic read
`read_block_h4_children_from_redis` does a single `ZRANGEBYSCORE(index, d1_open_epoch, d1_open_epoch+86399)` —
only the current D1 block's epochs (≤6 in practice), hard-capped at `_MAX_BLOCK_MEMBERS=64`. No full scan, no
unbounded iteration, no blocking. Each in-range epoch is `GET`-read and adapted to a `HydratedH4Child` (same
attribute surface as the live `_SealedH4View`). Malformed records are recorded in `malformed_history_epochs`,
never accepted.

## No hard-coded target
The read client is **injected**, or resolved from the producer's existing canonical writer (`producer.writer.
redis_client`) — the same Redis the H4 history is written to. **No host/IP/port/db/DSN/password is hard-coded in
the new module** (proven by `test_no_hardcoded_redis_target_in_module`). Any future SQL/MariaDB fallback must use
the existing governed external config (`env_config.get_db_config`) — it is not wired in this WO (an unknown source
returns `UNSUPPORTED_WARMSTART_SOURCE`, fail-loud, never a silent cold-start into false-GREEN).

## Pre-existing hard-coded values flagged (NOT spread, NOT changed)
The **detached `/tmp` dev-loop publishers** (control_plane/indicator/candle_feature/sessions_levels) hard-code
`redis.Redis(host="192.168.11.10", port=6379, db=0)`. Those are operational dev scaffolding outside this lane and
outside this diff — flagged here per the WO instruction; this WO neither touches nor replicates them. The governed
in-process path (PR #63 + this WO) uses injected/env config only.
