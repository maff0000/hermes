# H4 Derivation — Anchor Decision & Policies
**WO-HELM-HERMES-GOLD-H4-DERIVATION-FROM-H1-0001 · code/design only · no Redis I/O**

New module `utils/candle_h4_derivation_v1.py` + small extensions to `candle_contract_v1.py` (policy) and
`candle_history_v1.py` (H4 timeframe). No live publication; no Redis writes.

## Anchor decision (ratified)
**Fixed NY-5PM-aligned: day boundary 22:00 UTC**, H4 buckets OPEN at **22:00, 02:00, 06:00, 10:00, 14:00,
18:00 UTC**. Implemented as a fixed UTC grid (no intraday DST shift), per the architect's explicit bucket
list. `h4_bucket_open(dt)` maps any time to its bucket via a +2h epoch shift onto the 4h grid; the 22:00
bucket spans into the next UTC day. 6 H4 buckets nest into the (separate, deferred) NY-5PM D1.

## Source policy
Derive H4 from **H1 only** (`expected_source_count=4`). The dead/stale `candles_H4` table and Proteus-era
sources are NOT used. M15 fallback is out of scope (a later repair WO). Provenance:
`derivation=DERIVED_FROM_LOWER_TIMEFRAME`, `source_timeframe=H1`,
`derivation_policy=DERIVED_H4_FROM_H1` (new governed policy), `source_policy_epoch=H4_FROM_H1_NY1700_V1`.

## OHLCV derivation
open=first child open, high=max child high, low=min child low, close=last child close, volume=sum — via
the contract's deterministic `aggregate_ohlc` + `build_derived_candle_contract` + `validate_candle_contract`.

## Completeness / gap policy (honest; proof in 02)
| children | closed | status | coverage | gap_state |
|---------|--------|--------|----------|-----------|
| 4/4 | yes | **OK** | 1.0 | NONE |
| 3/4 | yes | SOURCE_INCOMPLETE (never OK) | 0.75 | INCOMPLETE |
| 2 (partial) | no | FORMING | 0.5 | INCOMPLETE |
| 0 | yes | NO_SOURCE_DATA | 0.0 | GAP_DETECTED |
Missing H1 children are **never synthesised**; benign no-tick gaps are surfaced via gap_state/coverage,
never laundered into OK.

## Key topology / guards (proof in 02)
Future keys: `hermes:candles:XAU_USD:H4:{latest:v1| history:v1:{open_epoch} | history:v1:index}` — NOT
written here. History grid now includes H4 (`expected_opens_for_day` NY-5PM aligned: 02/06/10/14/18/22).
`assert_history_target` still rejects: `:latest:v1` (TGT-002), XAUUSD (TGT-004), non-XAU (TGT-005),
**D1/D (TGT-006)**, unversioned (TGT-003). No regime fields (scan GOV-CANDLE-CONTRACT-029).

## Excluded (this WO): no D1, no live H4 publication (seam SUPPORTED_TF unchanged = M1/M5/M15/H1),
no Redis writes, no deploy/activation, no shadow, no XAUUSD output, no Proteus/structure_engine.
