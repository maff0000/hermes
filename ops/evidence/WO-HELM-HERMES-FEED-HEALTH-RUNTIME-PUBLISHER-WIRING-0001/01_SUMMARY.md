# WO-HELM-HERMES-FEED-HEALTH-RUNTIME-PUBLISHER-WIRING-0001 — HELM evidence

## Result: GREEN_FEED_HEALTH_RUNTIME_PUBLISHER_WIRING_PR_READY_FOR_R2D2_AUDIT

## PR / branch / SHA
- PR: (see 02_pr.txt)
- branch: wo/WO-HELM-HERMES-FEED-HEALTH-RUNTIME-PUBLISHER-WIRING-0001
- base SHA: 72ae2d472f0407eeff4a6f2fb643a3f871e48eeb (origin/main)
- head SHA (code): 400f04b7b1d44e10f5f20ea819aaf68941a47d02
- worktree: /srv/trading/hermes-worktrees/wo-feedhealth-runtime

## Contract / key discovery (followed existing PR #66 — nothing invented)
- key: hermes:feed_health:XAU_USD:v1 (feed_health_key) — EXISTING
- builder: build_feed_health_contract(...) — EXISTING; read-only snapshot: collect_feed_health_snapshot(...) — EXISTING
- publisher foundation: build_feed_health_publisher_from_env() — EXISTING (disabled default, SystemExit(101), FH-020/021 fail-closed)

## Selected gate names (existing PR #66 names — matched WO exactly)
- HERMES_FEED_HEALTH_PUBLISH_ENABLED
- HERMES_FEED_HEALTH_PUBLISH_AUTHORISED
- HERMES_FEED_HEALTH_PUBLISH_INSTRUMENTS  (canonical XAU_USD only; XAUUSD/non-XAU/multiple rejected -> GOV-HERMES-FH-021)
  (+ optional HERMES_FEED_HEALTH_SOURCE_NAME, governed default UNKNOWN)

## Changed files
- utils/hermes_runtime_publisher_steps_v1.py — + fh import + feed_health_step (gate-first, read-only snapshot, writes only feed_health key)
- utils/hermes_publisher_runtime_v1.py — default_runner_specs appends feed_health after instrument_catalog when gates enabled
- tests/test_feed_health_runtime_wiring_v1.py (NEW, 21 tests)

## Design decision (documented)
Wiring only. Did NOT extend the catalog RUNTIME_PUBLISHABLE_SURFACES allow-list to feed_health -> PR #73 guard stays
intact (feed_health fails GOV-HERMES-IC-030) and feed-health stays fully dark. Runtime-published catalog semantics
for feed-health are deferred to its future activation WO (mirrors catalog wiring PR #71 preceding semantics PR #73).

## Supervisor behaviour
- default (no gates): 4 runners. With catalog gates only: exactly 5. With catalog+feed-health gates: exactly 6
  [control_plane, indicators, candle_features, sessions_levels, instrument_catalog, feed_health]. No quote/tick/market_map runner.

## Tests
new: .....................                                                    [100%]
21 passed in 0.04s
regression (#66/#69/#70/#71/#72/#73): ..........................                                               [100%]
98 passed in 0.08s
full code-only suite: 1007 passed; failures identical to pristine main (51, all pre-existing async-plugin/env) — ZERO new failures introduced.

## Zero-runtime-mutation certification
CODE-ONLY. No deploy/restart/build/pull; no runtime env/config change; no Redis writes; no SQL; no runtime-process
touch; feed-health NOT activated (inert); no quote/tick activation; market_map.py/market-map-dev.service untouched;
no auth/ACL/security change; no consumer cutover; no ARES/HELIOS/Falcon/NEO/SOLO/PLUTUS touch; no candle/H4/D1 logic change; no secrets; no config-in-code.
