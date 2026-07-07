# WO-HELM-HERMES-LIVE-TICK-PUBLISHER-V1-BUILD-0001 — HELM evidence

## Result: GREEN_LIVE_TICK_EMITTER_V1_PR_READY_FOR_R2D2_AUDIT

## Architecture decision: Type-A REJECTED, Type-B (emitter) ACCEPTED
Type-A (supervised runner) rejected with code evidence: (1) state.latest_ticks is in-process stream state in
main.py (main.py:152; set main.py:768), unreachable from a supervisor step(client); (2) supervisor 60s cadence
cannot serve a 5s-TTL/10s-EX hot key; (3) proven tick pattern is emitter-based (tick_runtime_shadow_adapter_v1,
no tick runner in default_runner_specs). Architect approved Type-B emitter.

## PR / branch / SHA
- branch: wo/WO-HELM-HERMES-LIVE-TICK-PUBLISHER-V1-BUILD-0001
- base SHA: 8ec5a83898251d65b5c89414e103c100ae40eaec (origin/main)
- code head SHA: 40889260857ed8af6501d8fd8186a08c810d6ca3

## Source discovery (confirmed)
- state.latest_ticks : Dict[str,SignalTick] module-global main.py:152; set in OANDA stream loop main.py:768; lineage OANDA->TickSource.OANDA->SignalTick.
- Existing shadow emitter (tick_runtime_shadow_adapter_v1): emit_tick(tick)/emit_tick_observed(tick) per-tick from stream loop; writes hermes:shadow:*; REFUSES canonical writes. Mirrored for LIVE.
- tick_contract_v1 design-complete: canonical_key=hermes:ticks:XAU_USD:latest:v1, build_tick_contract/build_unavailable/validate/classify_freshness, TTL=5, REDIS_EX_SECONDS=10. Reused verbatim.

## Files changed
- NEW utils/tick_live_emitter_v1.py — LIVE canonical emitter (gated dark; writes only hermes:ticks:XAU_USD:latest:v1 EX=10; canonical/XAUUSD/shadow guards; fail-loud; emit_tick_observed never raises; tick_live_gate_enabled() env-only)
- utils/hermes_instrument_catalog_v1.py — tick added to RUNTIME_PUBLISHABLE_SURFACES; builder generalises RUNTIME_PUBLISHED to tick
- utils/hermes_runtime_publisher_steps_v1.py — _catalog_runtime_published_surfaces() includes tick when tle.tick_live_gate_enabled() (same gate; no split-brain; env-only)
- main.py — import + state.live_tick_emitter + boot build (fail-loud) + per-tick emit_tick_observed in OANDA stream loop (DARK by default)
- NEW tests/test_tick_live_emitter_v1.py (+ updated 3 catalog guard tests)

## Exact LIVE gate names
- HERMES_TICK_PUBLISH_ENABLED / HERMES_TICK_PUBLISH_AUTHORISED / HERMES_TICK_PUBLISH_INSTRUMENTS(=XAU_USD)
- gates absent -> DisabledTickEmitter (no client/no I/O, DARK); enabled-without-authorised -> SystemExit(101); missing/invalid/XAUUSD scope -> fail loud (GOV-HERMES-TICK-020/021)

## Canonical key + no XAUUSD
- writes ONLY hermes:ticks:XAU_USD:latest:v1 (EX=10); _assert_canonical_live_key refuses shadow-prefix/XAUUSD/aggregate/non-canonical; no XAUUSD dual-publish

## Catalog keystone / split-brain equality
- tick_contract runtime_published tracks the LIVE tick gate; tick RUNTIME_PUBLISHED only when emitter gate enabled; consumer_live always false; dropped from dark_surfaces / added to runtime_published_surfaces only when gate on
- proven in tests: tick in runtime_published_surfaces == live tick emitter enabled == LIVE tick gate enabled
- tick is emitter-based -> NEVER in default_runner_specs (supervisor unchanged at 6)

## Contract/envelope + validation (fail-loud)
- reuses tick_contract_v1: bid/ask numeric, ask>=bid, non-negative spread, UTC ms timestamps, source_received present, seq=null, source oanda, publisher HERMES; malformed -> fail loud; absent -> explicit UNAVAILABLE (never fake); honest stale (STALE/WARN, not fake FRESH)

## Tests
21 passed in 0.04s
affected+regression (catalog/feed-health/quote/tick-contract/reseal): 222 pass. Full suite 1062 pass; zero new failures vs pristine main.

## Runtime untouched
runtime HEAD 8ec5a838; container 416ce84b58f0; supervisor 6 runners; feed-health live; quote dark; live canonical tick key ABSENT; market_map untouched; no cross-app touch. No deploy/build/restart; tick NOT activated; quote remains blocked.
