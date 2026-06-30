# Final Report — Candle-Features Publisher + Indicator Conventions (code-only PR)
**WO-HELM-HERMES-CANDLE-FEATURES-PUBLISHER-AND-INDICATOR-CONVENTIONS-0001 · HELM/HERMES**
**Expected verdict: GREEN_PR_OPEN_HERMES_CANDLE_FEATURES_PUBLISHER_INDICATOR_CONVENTIONS_CODE_ONLY**

## Delivered (code-only, base 7a69a8b5)
- utils/hermes_indicators_v1.py (M) — indicator method metadata (ema/rsi=CUTLER_SMA_14/atr=SMA_14...); values unchanged.
- utils/hermes_candle_features_v1.py (new) — per-TF deterministic candle-feature contract + validator + DISABLED publisher.
- utils/hermes_control_plane_v1.py (M) — health candle_features_built/active (BUILT_NOT_ACTIVE/ACTIVE/NOT_IMPLEMENTED).
- tests/test_hermes_candle_features_v1.py (new, 24 tests).

## Tests
24 new pass; control-plane+indicators+candle+features suite 411 passed (no regression). No Redis server; no auth.

## Exclusions honoured
No deploy/restart/activation/Redis write/SQL write/auth/ACL/NOAUTH/D1 history/D1 candle_features/consumer cutover/
legacy deletion/regime/risk/decision/trade/ARES interpretation/opportunistic refactor/cross-app edits.

## Risks / notes for R2D2
1. Indicator method metadata is added to the builder (no value change) but NOT published live — a future redeploy
   carries it into live payloads. Candle-feature publisher has no publish path yet (separate activate WO).
2. Candle-features compute wiring (from candle_features.py + candle geometry) is the activate WO's job; this WO is the contract + foundation.
3. D1 candle_features gated until D1 latest GREEN. Deterministic LEVELS = separate future family.
