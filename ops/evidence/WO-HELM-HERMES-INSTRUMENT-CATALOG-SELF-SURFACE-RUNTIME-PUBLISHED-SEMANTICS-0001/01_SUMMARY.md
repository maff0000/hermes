# WO-HELM-HERMES-INSTRUMENT-CATALOG-SELF-SURFACE-RUNTIME-PUBLISHED-SEMANTICS-0001 — HELM evidence

## Result: GREEN_INSTRUMENT_CATALOG_SELF_SURFACE_RUNTIME_PUBLISHED_SEMANTICS_PR_READY_FOR_R2D2_AUDIT

## PR / branch / SHA
- PR: https://github.com/maff0000/hermes/pull/73
- branch: wo/WO-HELM-HERMES-INSTRUMENT-CATALOG-SELF-SURFACE-RUNTIME-PUBLISHED-SEMANTICS-0001
- worktree: /srv/trading/hermes-worktrees/wo-catself-runtime-published (from origin/main 615e8557)
- code head SHA: 511c3d39c4fcce46f35838e3be7cf8ffb74faa9d

## Files changed (source + tests only)
- utils/hermes_instrument_catalog_v1.py  — SURFACE_RUNTIME_PUBLISHED + RUNTIME_PUBLISHABLE_SURFACES +
  _normalise_runtime_published (GOV-HERMES-IC-030) + runtime_published_surfaces param; self-surface reports
  RUNTIME_PUBLISHED with runtime_published/consumer_live; dark_surfaces excludes runtime-published; top-level +
  marker runtime_published_surfaces; feed_health/quote/tick carry runtime_published=false/consumer_live=false.
- utils/hermes_runtime_publisher_steps_v1.py — step passes runtime_published_surfaces=["instrument_catalog"].
- tests/test_instrument_catalog_self_surface_runtime_published_v1.py (NEW, 15 tests).
- tests/test_instrument_catalog_runtime_wiring_v1.py — enabled/step path updated to runtime-published semantics.
- tests/test_hermes_instrument_catalog_v1.py — marker-note assertion updated (pure path unchanged behaviour).

## Required-outcome coverage
- catalog self-surface NOT dark when runtime-published; exposes explicit runtime_published=true
- consumer-cutover/trusted-live-plane stays false / not implied (consumer_live=false, live=false, runtime_live=false)
- feed-health/quote/tick remain dark + not runtime-published + not consumer-live; fail loud if marked runtime-published
- pending_runtime_deployment.dark_surfaces contains only genuinely dark surfaces (feed_health/quote/tick)
- no :XAUUSD: output; no duplicate hermes:tick:*; tick references hermes:ticks:XAU_USD:latest:v1
- D1 not hard-coded ACTIVE; no regime/risk/signal/decision semantics; canonical XAU_USD; XAUUSD alias-only
- pure/pre-activation builder path unchanged (PR #69/#70/#71 preserved); env/gate tests compatible

## Tests
............................................................             [100%]
60 passed in 0.07s
Broader code-only suite: 847 passed. Remaining failures are pre-existing env/async-plugin (test_config/
test_per_instrument_health/test_watchdog*) — verified identical on pristine origin/main; none import the changed modules.

## Code-only / zero-runtime-mutation certification
- CODE-ONLY: only utils/ + tests/ changed (see 03_files_changed.txt). No new I/O/write/network in source diff.
- NO deploy, NO restart, NO build/pull image, NO env/config change, NO Redis writes, NO SQL writes.
- NO feed-health/quote/tick activation (they remain dark; a runtime_published mark for them fails loud).
- market_map.py / market-map-dev.service NOT touched. No auth/ACL/security change. No consumer cutover.
- No ARES/HELIOS/Falcon/NEO/SOLO/PLUTUS touch. No secrets. No candle/H4/D1 changes.
