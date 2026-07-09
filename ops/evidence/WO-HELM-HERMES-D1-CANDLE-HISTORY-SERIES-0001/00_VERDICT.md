# WO-HELM-HERMES-D1-CANDLE-HISTORY-SERIES-0001 — HELM verdict (CODE-ONLY PR)

## GREEN_D1_CANDLE_HISTORY_SERIES_PR_READY_FOR_R2D2_AUDIT
Tracking: HELM_HERMES_D1_CANDLE_HISTORY_SERIES_BUILD::2026-07-09T09:10Z::GREEN_D1_CANDLE_HISTORY_SERIES_PR_READY_FOR_R2D2_AUDIT

Authorities consumed:
- R2D2 inventory: R2D2_HERMES_INDICATOR_HISTORY_BACKFILL_INVENTORY::2026-07-09T08:51:10Z::GREEN_HERMES_INDICATOR_HISTORY_BACKFILL_INVENTORY_COMPLETE_READY_FOR_HELM_IMPLEMENTATION
- HELM AMBER blocker: HELM_HERMES_D1_INDICATORS_FEATURES_LEVELS_BUILD::2026-07-09T08:20Z::AMBER_D1_INDICATORS_FEATURES_LEVELS_BLOCKED_NO_D1_HISTORY_SERIES

Branch: wo/WO-HELM-HERMES-D1-CANDLE-HISTORY-SERIES-0001   Base: d110ab2fbb43d37909582dafe26195ac27f1603a
Files changed (3): utils/candle_d1_history_v1.py (NEW), utils/candle_d1_publish_wire_v1.py (+18), tests/test_candle_d1_history_v1.py (NEW)

## Blocker resolved
D1 latest-only: runtime published hermes:candles:XAU_USD:D1:latest:v1 but NO hermes:candles:XAU_USD:D1:history:v1:index.
indicator_step/candle_feature_step read ONLY from the {tf}:history:v1:index ZSET -> D1 could not be sourced.
(Read-only proof pre-build: D1 latest exists ts=2026-07-07T22:00Z 6/6; D1 history depth=0; H4 history depth=217.)

## Implemented D1 history support (dark by default)
- NEW utils/candle_d1_history_v1.py — dedicated D1 history lane reusing the EXACT M1-H4 key convention:
    per-candle: hermes:candles:XAU_USD:D1:history:v1:{open_epoch} ; index ZSET: ...:D1:history:v1:index (score=member=open_epoch)
  without loosening the intentional M1-H4-only guards (candle_history_v1 GOV-CANDLE-HIST-003 / forward-writer _D1_DENY stay).
- D1 SOURCE PATH: the APPROVED D1 6/6 seal path ONLY (candle_d1_publish_wire_v1._seal_and_publish -> the exact sealed
  D1 latest env). SNAPSHOT model (mirrors forward writer): the derived env (source_count=6) is snapshotted verbatim +
  an identical 5-field `history` provenance block appended, then cc.validate_candle_contract re-runs. NEVER rebuilt as
  DIRECT/source_count=1 (that would falsify D1 provenance). NO SQL H4/M30, NO 00:00-UTC anchor, NO market_map, NO fabrication.
- D1 SEAL/COMPLETION RULE: assert_sealed_complete_d1 admits ONLY status-OK + is_closed + source_count==expected==6 +
  source_coverage==1.0 + gap_state in {None,NONE}. Unsealed/incomplete/<6/non-OK/gapped -> REJECTED (never enters history).
- DEPTH VALIDATOR: D1_MIN_DEPTH_FOR_INDICATORS=26 (ema_26); assert_sufficient_d1_history_depth fails loud below 26;
  d1_history_depth_sufficient(depth) bool. Downstream D1 indicators must call this before reading the series.
- RETENTION: count-based (D1 is daily) — keep newest 35; per-candle TTL 120d safety cap (> ~49d span of 35 trading days);
  INERT trim plan only (no delete in this WO).
- GATED WRITER: DisabledD1HistoryWriter default; HERMES_CANDLE_D1_HISTORY_ENABLED/AUTHORISED gate; enabled-without-
  authorised -> SystemExit(101). Fault-isolated on_d1_sealed (never raises).
- WIRING: candle_d1_publish_wire_v1._seal_and_publish calls self._d1_history_writer.on_d1_sealed(env) AFTER a successful
  6/6 latest publish; writer built dark in build_d1_producer_from_env. Deploy with the D1-history gate unset ->
  DisabledD1HistoryWriter no-op -> D1 history dark (NO auto-activation). D1 latest publisher preserved exactly.

## Catalog semantics
NONE added — D1 history must NOT be marked RUNTIME_PUBLISHED until it is actually published after a future gated
deploy/activation. This PR does not touch catalog/manifest D1 semantics (they already read "gated until D1 latest GREEN").

## Tests (15 focused, all pass)
key convention matches M1-H4 / target guard rejects latest+non-D1+alias / canonical XAU_USD required / sealed-6/6 admitted /
unsealed+incomplete+<6+non-OK+gapped rejected / UTC idempotent write-plan targets history-only / depth<26 fail-loud, >=26 pass /
count-based retention / writer disabled-by-default / enabled-without-authorised halts / enabled writer writes ONLY D1 history
keyspace+index (never :latest:v1, no XAUUSD) / fault-isolated never raises / producer history DARK by default (latest preserved) /
producer with enabled writer appends series additively / no forbidden interpretive tokens in code.
Touched-area non-regression: D1 wire + candle history + derivation + forward writer = 83 passed.
FULL SUITE: branch == pristine main d110ab2 (51 failed + 4 collection errors, identical pre-existing env/plugin noise) -> 0 NEW failures; +15 passing (1092 vs 1077).

## Forbidden-token scan (explained)
regime/risk/strategy/signal/trade x1 each -> module NEGATIVE-declaration docstring line 19 (not logic).
score x5 -> index_score / ZSET score=open_epoch (deterministic ordering). entry -> "Boot entrypoint". exit -> Python SystemExit halt.
XAUUSD x10 -> alias-DENY guards (mostly pre-existing wire code) that REJECT XAUUSD, never produce it. No interpretive logic entered HERMES.

## Future activation/backfill plan (DESIGN ONLY — NOT executed here; requirement 12)
Forward accumulation is 1 D1/day, so seeding depth>=26 forward takes ~26 trading days. Recommended follow-on (separate WO):
1. Governed D1 history BACKFILL of prior COMPLETED D1 buckets from the approved derivation path (6xH4 per NY-5PM day) —
   reconstruct sealed 6/6 D1 envelopes for the last >=35 completed days, write via build_d1_history_write_plan (same guards),
   ZADD to index; idempotent; count-trim to 35. NO SQL H4/M30 until the anchor-divergence ruling completes.
2. Set HERMES_CANDLE_D1_HISTORY_ENABLED/AUTHORISED (deploy-dark then activate) so forward seals also append.
3. Only after depth>=26 (assert_sufficient_d1_history_depth GREEN) RE-ISSUE the D1 indicators/features/levels WO.

## Boundaries (CODE-ONLY)
No runtime mutation. No deploy. No activation. No backfill executed. No SQL writes. No Redis writes (except HELM fabric) or
deletes. No market_map/cross-app edits. No consumer-live. No secrets. D1 latest + M1-H4 history + tick/quote/feed-health/catalog untouched.
