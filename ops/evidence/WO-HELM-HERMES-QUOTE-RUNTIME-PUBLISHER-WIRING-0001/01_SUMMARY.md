# WO-HELM-HERMES-QUOTE-RUNTIME-PUBLISHER-WIRING-0001 — HELM evidence

## Result: GREEN_QUOTE_RUNTIME_PUBLISHER_WIRING_PR_READY_FOR_R2D2_AUDIT

## PR / branch / SHA
- PR: (see 02_pr.txt)
- branch: wo/WO-HELM-HERMES-QUOTE-RUNTIME-PUBLISHER-WIRING-0001
- base SHA: 9ca0121b032505d54b8cc62f52602377fe258123 (origin/main)
- head SHA (code): 3578514733cac373d35560998bb4b5414245b3b3
- worktree: /srv/trading/hermes-worktrees/wo-quote-runtime

## Contract / key discovery (followed existing PR #68 — nothing invented)
- key: hermes:quote:XAU_USD:v1 (quote_key) — EXISTING
- builder: build_quote_contract(...); publisher: build_quote_publisher_from_env() (disabled default, SystemExit(101), QT-020/021 fail-closed)

## Selected gate names (existing PR #68 names — matched WO exactly)
- HERMES_QUOTE_PUBLISH_ENABLED / HERMES_QUOTE_PUBLISH_AUTHORISED / HERMES_QUOTE_PUBLISH_INSTRUMENTS
  (canonical XAU_USD only; XAUUSD/non-XAU/multiple rejected -> GOV-HERMES-QT-021; missing/empty -> QT-020)
  (+ optional HERMES_QUOTE_SOURCE_NAME, governed default UNKNOWN)

## Quote source / provenance decision
- Governed source of bid/ask = the EXISTING tick surface hermes:ticks:XAU_USD:latest:v1, mapped via
  qt.reconcile_quote_from_tick_envelope (deterministic bid/ask; mid/spread RECOMPUTED; interpretation dropped).
- No new source invented; no direct feed access; READ-ONLY GET of the tick key; writes ONLY the quote key.

## Weekend / stale-source handling (honest, never faked)
- tick present+fresh (age <= 10s) -> GREEN/FRESH
- tick present+stale (age > 10s, e.g. Saturday market-closed) -> AMBER_STALE/STALE
- tick absent -> empty envelope -> bid/ask null -> RED_MISSING/UNAVAILABLE
- market-closed is a STATUS, NOT converted to a runtime fault (fault.state stays NONE unless real fault_counters).
- Fixed a real bug caught in test: absent tick must pass {} (not None) to the reconciler to avoid AttributeError.

## Changed files
- utils/hermes_runtime_publisher_steps_v1.py — + qt import + quote_step (gate-first, read-only tick reconcile, writes only quote key)
- utils/hermes_publisher_runtime_v1.py — default_runner_specs appends quote after feed_health when gates enabled
- tests/test_quote_runtime_wiring_v1.py (NEW, 24 tests)

## Design decision (documented)
Wiring only. Did NOT extend catalog RUNTIME_PUBLISHABLE_SURFACES to quote (not necessary for tests) -> PR #73 guard
stays intact (quote fails GOV-HERMES-IC-030) and quote stays fully dark. No AMBER escalation needed. Catalog
runtime-published semantics for quote deferred to its future activation WO.

## Supervisor behaviour
- default (catalog only): 5. catalog+quote: 6 [..,instrument_catalog,quote]. catalog+feed_health+quote: 7
  [..,instrument_catalog,feed_health,quote]. No tick/market_map runner.

## Tests
new: ........................                                                 [100%]
24 passed in 0.04s
regression (#66/#68/#69/#70/#71/#72/#73/#74): .....                                                                    [100%]
149 passed in 0.11s
full code-only suite: 1031 passed; failures identical to pristine main (51, pre-existing async-plugin/env) — ZERO new failures.

## Zero-runtime-mutation certification
CODE-ONLY. No deploy/restart/build/pull; no runtime env/config change; no Redis writes; no SQL; no runtime-process
touch; quote NOT activated (inert); no feed-health/tick activation; market_map.py/market-map-dev.service untouched;
no auth/ACL/security change; no consumer cutover; no ARES/HELIOS/Falcon/NEO/SOLO/PLUTUS touch; no candle/H4/D1 change; no XAUUSD output; no secrets; no config-in-code.
