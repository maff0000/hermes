# Final Report — HERMES Indicator Publisher + Control-Plane Health Fix (code-only PR)
**WO-HELM-HERMES-INDICATOR-PUBLISHER-AND-CONTROL-PLANE-FIX-0001 · HELM/HERMES**
**Expected verdict: GREEN_PR_OPEN_HERMES_INDICATOR_PUBLISHER_CONTROL_PLANE_FIX_CODE_ONLY**

## Delivered (code-only, base d08b48f4)
- utils/hermes_control_plane_v1.py (M) — health self-listing fix (control_plane_active/indicators_built) +
  BUILT_NOT_ACTIVE status + heartbeat_ttl_policy (refresh 60 / TTL 180; manifest/catalog/health persistent).
- utils/hermes_indicators_v1.py (new) — per-TF versioned indicator contract (hermes:indicators:XAU_USD:{TF}:v1)
  + validator + DISABLED publisher foundation (gated, no Redis I/O, D1 gated until D1 GREEN).
- tests/test_hermes_indicators_v1.py (new, 18 tests incl health-fix).

## Tests
18 new pass; control-plane+candle+indicators suite 387 passed (no regression). No Redis server; no auth.

## Exclusions honoured
No deploy/restart/activation/Redis write/SQL write/auth/ACL/NOAUTH/D1 history/consumer cutover/legacy deletion/
regime/regime_detector expansion/risk/decision outputs/opportunistic refactor/cross-app edits.

## Risks / notes for R2D2
1. Health fix is code-only — the live hermes:health:v1 still self-lists until the control-plane publisher is
   redeployed passing control_plane_active=True (next deploy WO). indicators stay BUILT_NOT_ACTIVE (never ACTIVE) until activation.
2. Indicator publisher has NO publish path yet (separate authorised WO); D1 indicators gated until D1 latest GREEN.
3. Per-TF key chosen (hermes:indicators:XAU_USD:{TF}:v1) as cleaner; one-blob alternative not used.
4. Deterministic LEVELS (PDH/PDL, swings) classified HERMES-owned but are a SEPARATE future family, not in this WO.
