# Final Report — D1 History Contract + Writer Support (code-only PR)
**WO-HELM-HERMES-GOLD-D1-HISTORY-CONTRACT-WRITER-0001 · HELM/HERMES**
**Expected verdict: GREEN_PR_OPEN_GOLD_D1_HISTORY_CONTRACT_WRITER_CODE_ONLY**

## Delivered (code-only, base c9eb8484)
- `utils/candle_history_v1.py` — D1 added to HISTORY_TIMEFRAMES (legacy "D" still excluded); new
  `assert_d1_history_payload` (status OK, 22:00 anchor, source H4, six complete children, no candles_D1/24xH1),
  wired into `build_history_write_plan` for D1; `expected_opens_for_day` D1 case.
- `utils/candle_history_forward_writer_v1.py` — D1 forward-history DENIED by default; allowed only via
  `HERMES_CANDLE_D1_HISTORY_FORWARD_AUTHORISED=true` (parse `allow_d1`); `on_d1_sealed` hook; legacy "D" always denied;
  retention/index/idempotency/conflict policy unchanged.
- `tests/test_candle_d1_history_contract_writer_v1.py` (19 tests) + 6 existing test files updated to the new posture.

## Behaviour
D1 history keyspace + writer support exist and are FULLY GUARDED, but INERT: D1 forward-history is denied by
default and no D1 history is written, backfilled, or activated in this WO. A future D1-history-activation WO
(gated on GREEN_D1_LATEST_ACTIVE_DEV_VERIFIED_6XH4_COMPLETE_OK) owns enabling it. Incomplete D1 can never be
written as OK (status-OK + 6/6 + coverage 1.0 enforced). No direct candles_D1, no 24xH1, no regime, no shadow.

## Tests
19 new pass; full candle suite **370 passed** (no regression). 6 existing tests updated from "D1 history rejected"
to "D1 history governed/gated; legacy D rejected".

## Exclusions honoured
No deploy/restart/activation/Redis write/SQL write/D1 history backfill/D1 history execution/direct candles_D1/
24xH1 shortcut/regime/XAUUSD output/shadow/consumer cutover/opportunistic refactor.

## Risks / notes for R2D2
1. assert_history_target now ACCEPTS D1 history keys (D1 in HISTORY_TIMEFRAMES). A write is still triple-gated:
   D1-payload guard (six complete OK H4, 22:00, H4 source) + status-OK + D1-history authorisation. Six existing
   tests updated to the new posture; legacy "D" remains rejected everywhere.
2. D1 forward-history is DENIED by default; HERMES_CANDLE_D1_HISTORY_FORWARD_AUTHORISED is the enforceable proxy
   for "D1 latest GREEN" — the operator must set it only after D1 latest is verified GREEN (separate WO).
3. on_d1_sealed exists but is NOT wired into any runtime caller; wiring + activation is a separate WO.
