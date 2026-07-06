# WO-HELM-HERMES-CATALOG-QUOTE-RUNTIME-PUBLISHED-SEMANTICS-0001 — HELM evidence

## Result: GREEN_CATALOG_QUOTE_RUNTIME_PUBLISHED_SEMANTICS_PR_READY_FOR_R2D2_AUDIT

## Context
Prerequisite before quote activation (mirrors PR #76 for feed_health). Deployed RUNTIME_PUBLISHABLE_SURFACES
excluded quote; gate-only quote activation would leave the catalog self-contradicting. Code-only; quote NOT activated.

## PR / branch / SHA
- branch: wo/WO-HELM-HERMES-CATALOG-QUOTE-RUNTIME-PUBLISHED-SEMANTICS-0001
- base SHA: 0e04033fa8aafd0a3a12414f4a0df00566ee4bc5 (origin/main)
- code head SHA: 69bbde4c94d25d19311689ce542239668ecb74cf

## Exact quote gate discovery (match PR #75 pattern — confirmed)
- HERMES_QUOTE_PUBLISH_ENABLED / HERMES_QUOTE_PUBLISH_AUTHORISED / HERMES_QUOTE_PUBLISH_INSTRUMENTS  (== PR#75 expected)

## Changed files (2 source + 3 tests)
- utils/hermes_instrument_catalog_v1.py — RUNTIME_PUBLISHABLE_SURFACES -> {instrument_catalog, feed_health, quote};
  builder generalises RUNTIME_PUBLISHED treatment to quote (status/runtime_published/dark_surfaces/runtime_published_surfaces)
- utils/hermes_runtime_publisher_steps_v1.py — _catalog_runtime_published_surfaces() includes quote when its
  runtime-publisher gate is enabled (SAME gate default_runner_specs uses); env-read only, no Redis I/O; fail-loud on mis-gate/scope
- tests: updated PR #74/#75/#76 guard tests (quote permitted; tick still guarded); added builder + step tests + split-brain equality

## Same-condition (no split-brain) proof
catalog uses qt.build_quote_publisher_from_env().enabled — the EXACT same call default_runner_specs uses for the
quote runner. Test asserts: quote in runtime_published_surfaces == quote runner selected == quote gate enabled.

## Key properties
- tick STILL guarded (fails GOV-HERMES-IC-030) — no active runtime publisher; NOT made runtime-published in this PR
- quote consumer_live stays False (no consumer cutover implied)
- DEPLOY-DARK SAFE: quote gate absent -> catalog marks only active surfaces -> quote stays CODE_PRESENT_DARK
- quote does NOT activate by default; mis-gated quote fails loud (SystemExit 101 / GOV-HERMES-QT-020)
- feed_health + instrument_catalog regression-safe (unchanged)

## Quote-activation dependency (recorded, NOT solved here)
Future quote activation ALSO requires a live governed tick source hermes:ticks:XAU_USD:latest:v1 and must fail
closed/honestly: tick absent -> RED_MISSING/null prices; tick stale -> AMBER_STALE; market closed -> honest
stale/closed status, not fake GREEN; no fabricated quote. (This PR does NOT solve or bypass that dependency.)

## Tests
48 passed in 0.05s
affected+regression: 182 pass. Full suite 1040 pass; zero new failures vs pristine main (51 pre-existing async/env).

## Runtime untouched (read-only, no mutation by this WO)
runtime HEAD 0e04033f; container 5c8fa43c3d0c; supervisor 6 runners; quote dark (0 keys); tick dark/shadow; feed-health live.

## Zero-runtime-mutation certification
CODE-ONLY. No deploy/restart/recreate; no runtime env/config change; no Redis writes; NO Redis I/O added; no SQL;
quote NOT activated; tick/feed-health/market_map untouched; no candle/H4/D1 change; no cross-app; no secrets; no config-in-code.
