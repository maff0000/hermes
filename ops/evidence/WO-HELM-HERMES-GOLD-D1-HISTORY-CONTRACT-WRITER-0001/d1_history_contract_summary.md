# D1 History Contract + Writer Support
**WO-HELM-HERMES-GOLD-D1-HISTORY-CONTRACT-WRITER-0001 · code-only · HELM/HERMES**

## Target contract (candle_history_v1.py)
- HISTORY_TIMEFRAMES now ("M1","M5","M15","H1","H4","D1"); legacy "D" remains excluded.
- assert_history_target ACCEPTS hermes:candles:XAU_USD:D1:history:v1:{open_epoch} + :index; still rejects
  latest (TGT-002), alias XAUUSD (TGT-004), non-XAU (TGT-005), unversioned/malformed (TGT-003/007), legacy D (TGT-006).
- NEW assert_d1_history_payload (D1-specific, fail-loud, called by build_history_write_plan for D1):
  status OK (002), 22:00 UTC anchor (003), source_timeframe H4 (004), expected 6 (005), source_count 6 (006),
  coverage 1.0 (007), no direct candles_D1 / 24xH1 provenance token anywhere (008). validate_candle_contract
  (regime/forbidden scan) runs first.

## Writer support (candle_history_forward_writer_v1.py)
- D1 now a valid forward-history timeframe BUT DENIED BY DEFAULT: parse_forward_timeframes rejects D1 unless
  allow_d1=True; build_history_forward_writer_from_env sets allow_d1 only from HERMES_CANDLE_D1_HISTORY_FORWARD_AUTHORISED
  (default false) -> listing D1 in TIMEFRAMES without it FAILS LOUD (GOV-CANDLE-HIST-FWD-D1-001). Legacy "D" always denied.
- on_d1_sealed hook added (source_table canonical_latest_forward:DERIVED_D1_FROM_H4); D1 write path inherits the
  D1 payload guard via build_history_write_plan + the generic status-OK gate (incomplete D1 never written).
- Retention/index/idempotency unchanged: TTL 3,024,000s; ZSET score=member=open_epoch; idempotent same payload;
  divergent payload at same epoch -> GOV-CANDLE-HIST-FWD-020 fail loud; no deletes; no silent overwrite.

## Child-completeness linkage
A D1 with source_count=6/coverage=1.0/status OK is produced ONLY by CanonicalD1Producer, which (per the ratified
completeness rule, PR #57) builds D1-OK from six status-OK COMPLETE H4 children. assert_d1_history_payload
re-asserts count=6/coverage=1.0/status OK at the history layer; per-child completeness is enforced upstream at production.

## INERT
No D1 Redis writes, no backfill, no activation in this WO. D1 forward-history disabled by default.
