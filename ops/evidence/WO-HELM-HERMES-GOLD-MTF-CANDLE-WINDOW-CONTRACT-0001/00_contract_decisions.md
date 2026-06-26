# History/Window Contract — Decisions
**WO-HELM-HERMES-GOLD-MTF-CANDLE-WINDOW-CONTRACT-0001 · code/design only · no Redis I/O**

Module: `utils/candle_history_v1.py` (additive; no existing files changed).

## Key schema (ratified)
- Per-candle immutable: `hermes:candles:XAU_USD:{TF}:history:v1:{open_epoch}` (string = governed v1 envelope + history block)
- Ordered index (ZSET): `hermes:candles:XAU_USD:{TF}:history:v1:index` (score = open_epoch, member = open_epoch)

## R2D2-required amendments — addressed
1. **Retention/TTL:** explicit bounded policy `HISTORY_RETENTION_DAYS=35` → `HISTORY_TTL_SECONDS=3,024,000`
   (4 weeks + operational buffer). Per-candle keys carry this TTL; index trimmed by score to
   `history_retention_cutoff_epoch(now)` by the (separate) writer. Never implicit; **no deletes here**.
2. **Dry-run per-day/per-TF gap profile:** `gap_profile(tf, day_utc, actual_opens)` returns
   expected/actual/missing counts, coverage %, first/last actual ts, `gap_explanation`
   (FULL_COVERAGE / WEEKEND_MARKET_CLOSED[_OPEN_SUN_2200Z] / FRI_PARTIAL_CLOSE_2100Z /
   WEEKDAY_GAP_INVESTIGATE), and sampled missing intervals. See `02_…samples.txt`.
3. **Target assertion proven** (`02_…samples.txt`): `assert_history_target` rejects LIVE `:latest:v1`
   (TGT-002), XAUUSD alias (TGT-004), non-XAU (TGT-005), H4/D1 (TGT-006), unversioned/non-history
   (TGT-003), bad open_epoch (TGT-007); accepts only a governed history key/index.

## 1. Retention — DECIDED: window-bound, explicit 35-day TTL (not permanent, not implicit).

## 2. Payload — DECIDED: latest v1 payload PLUS a `history` block (no `data`-schema change):
`history_contract_version`, `backfill_run_id`, `backfill_inserted_at_utc`, `source_table`,
`source_timestamp_utc`. **No regime/strategy fields** (validator scans the history block too).
Each historical candle is `generated_at = its own close time` → a self-consistent point-in-time `OK`
record (not stale-vs-now); backfill insert time lives in `backfill_inserted_at_utc`.

## 3. Indexing — DECIDED: ZSET score = member = `open_epoch` (UTC seconds). Idempotent: same candle →
same key + same member/score (re-run = no-op via SET + ZADD).

## 4. Target assertions — `assert_history_target` is the single mandatory pre-write guard (see above).

## Hard separation from live truth
History code path can target ONLY `:history:v1:*`. The four live keys
(`hermes:candles:XAU_USD:{M1,M5,M15,H1}:latest:v1`) are unreachable by any builder/plan here, and the
`:latest:` guard fails loud. The validator (`validate_candle_contract`) is STILL required before write.

## No execution
No backfill run, no Redis history/latest writes, no deploy/restart/activation, no SQL writes, no deletes.
This WO delivers the contract + tests only; execution is a separate, authorised WO.
