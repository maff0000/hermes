# WO-HELM-HERMES-CATALOG-FEED-HEALTH-RUNTIME-PUBLISHED-SEMANTICS-0001 — HELM evidence

## Result: GREEN_CATALOG_FEED_HEALTH_RUNTIME_PUBLISHED_SEMANTICS_PR_READY_FOR_R2D2_AUDIT

## Context
Prerequisite discovered during WO-HELM-HERMES-ACTIVATE-FEED-HEALTH-0001 (returned AMBER, zero runtime mutation):
gate-only feed-health activation would leave the catalog self-contradicting. This code-only PR provides the
catalog runtime-published semantics for feed_health so activation can be truthful. Mirrors PR #73 (instrument_catalog).

## PR / branch / SHA
- branch: wo/WO-HELM-HERMES-CATALOG-FEED-HEALTH-RUNTIME-PUBLISHED-SEMANTICS-0001
- base SHA: 5938dfe9d698f3bf22c3af2bee19639852315a97 (origin/main)
- code head SHA: edf874ce86469751572719cf48f19bd9f497a68c

## Changed files (2 source + 3 tests)
- utils/hermes_instrument_catalog_v1.py — RUNTIME_PUBLISHABLE_SURFACES -> {instrument_catalog, feed_health};
  builder generalises RUNTIME_PUBLISHED treatment to feed_health (status/runtime_published/dark_surfaces/runtime_published_surfaces)
- utils/hermes_runtime_publisher_steps_v1.py — _catalog_runtime_published_surfaces(): includes feed_health when its
  runtime-publisher gate is enabled (same gate default_runner_specs uses); env-read only, no Redis I/O; fail-loud on mis-gate
- tests: updated PR #74/#75 guard tests (feed_health permitted; quote/tick still guarded); added builder + step tests

## Key properties
- quote/tick STILL guarded (fail GOV-HERMES-IC-030) — no active runtime publisher
- DEPLOY-DARK SAFE: feed-health gate absent -> catalog step marks only instrument_catalog -> feed_health stays dark
- ACTIVATION: feed-health gate enabled -> catalog marks feed_health RUNTIME_PUBLISHED atomically with the runner
- consumer_live stays False (no consumer cutover implied); pure/pre-activation path unchanged

## Tests
19 passed in 0.04s
affected+regression (catalog/feed-health/quote/reseal): 177 pass. Full suite 1035 pass; zero new failures vs pristine main.

## Zero-runtime-mutation certification
CODE-ONLY. No deploy/restart/build; no runtime env/config change; no Redis writes; no Redis I/O added (env-gate detection only);
no SQL; feed-health NOT activated by this PR; quote/tick untouched; market_map untouched; no candle/H4/D1 change; no secrets.
