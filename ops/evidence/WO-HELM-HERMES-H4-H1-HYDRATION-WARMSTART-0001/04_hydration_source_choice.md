# 04 — Hydration source chosen and why

**Chosen:** governed Redis **H1** history surface (canonical runtime surface) — `HERMES_H4_WARMSTART_SOURCE`
default `redis_h1_history`. Reads `hermes:candles:XAU_USD:H1:history:v1:index` (ZSET) + per-candle
`…:H1:history:v1:{open_epoch}` via `candle_history_v1` keys. This is the same keyspace the H1 forward-history
writer populates (`HERMES_CANDLE_HISTORY_FORWARD_TIMEFRAMES` includes H1) — the live source of truth for sealed H1.

**Bounded/deterministic:** single `ZRANGEBYSCORE(index, h4_open, h4_open+4h)` — only the active block's epochs
(≤4), hard-capped `_MAX_BLOCK_MEMBERS=32`; each `GET` adapted to a `HydratedH1Child` (same attribute surface as
the live H1 view). Malformed records recorded in `malformed_history_epochs`, never accepted.

**No hard-coded target:** read client is INJECTED or resolved from the producer's existing canonical writer
(`producer.writer.redis_client`). No host/IP/port/db/DSN/password in the new module (proven by
`test_no_hardcoded_redis_target_in_module`). A SQL/MariaDB fallback is not wired here; an unknown source returns
`UNSUPPORTED_WARMSTART_SOURCE` (fail-loud), never a silent cold-start.

**Pre-existing hard-coded values flagged (NOT spread/changed):** the detached `/tmp` dev-loop publishers hard-code
`redis.Redis(host="192.168.11.10"…)` — operational dev scaffolding, out of lane, untouched, not replicated.
