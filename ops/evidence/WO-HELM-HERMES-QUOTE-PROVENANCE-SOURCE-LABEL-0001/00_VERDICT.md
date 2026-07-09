# WO-HELM-HERMES-QUOTE-PROVENANCE-SOURCE-LABEL-0001 — HELM verdict (CODE-ONLY PR)

## GREEN_QUOTE_PROVENANCE_SOURCE_LABEL_PR_READY_FOR_R2D2_AUDIT
Tracking: HELM_HERMES_QUOTE_PROVENANCE_SOURCE_LABEL_BUILD::2026-07-09T07:20Z::GREEN_QUOTE_PROVENANCE_SOURCE_LABEL_PR_READY_FOR_R2D2_AUDIT

R2D2 activation-audit finding consumed:
R2D2_HERMES_QUOTE_ACTIVATION_AUDIT::2026-07-09T07:07:38Z::GREEN_QUOTE_PUBLISHER_V1_ACTIVATION_AUDIT_PASSED_CONTINUOUS_TTL_REFRESH_CONSUMER_DARK
(non-blocking: quote source.name=UNKNOWN while upstream tick provenance source=oanda; cosmetic only — bid/ask track tick in lockstep, fresh, source_dependency correct, deterministic)

## Branch / base / head
- branch: wo/WO-HELM-HERMES-QUOTE-PROVENANCE-SOURCE-LABEL-0001
- base:   ede438ad1a2c6365fe30c1dea8dc08e56fc638de (clean main after PR#80 + quote activation audit)
- head:   12f39d18f1eed3c6ec435ef96ab9dda5d533eed0 (code+tests) ; evidence commit follows

## Root cause
quote_step -> reconcile_quote_from_tick_envelope(source_name=pub.source_name); pub.source_name defaults to the
QUOTE_SOURCE_NAME_ENV default "UNKNOWN". Old mapping `source_name or d.get("source") or "UNKNOWN"` — the truthy
"UNKNOWN" sentinel short-circuited and shadowed the tick's real data.source/provenance.source ("oanda").

## Implemented provenance mapping (governed precedence)
- New tick_provenance_source(tick_env): provenance.source first, then data.source, else None (never invents).
- New resolve_quote_source_label(caller, tick_env):
  1. EXPLICIT caller override (real label, not None/empty, not the UNKNOWN sentinel) wins;
  2. else propagate upstream tick provenance source (oanda);
  3. else UNKNOWN fallback (tick carried no source — safe, never fabricated).
- reconcile_quote_from_tick_envelope now uses resolve_quote_source_label(source_name, tick_env) for source.name only.

## Fallback behaviour
Tick source absent (UNAVAILABLE-style) + caller default -> UNKNOWN sentinel preserved. Explicit override still
respected even with a source-less tick. No hidden/fake provenance.

## Files changed (2)
- utils/hermes_quote_tick_contract_v1.py  (+SOURCE_NAME_UNKNOWN, tick_provenance_source, resolve_quote_source_label; reconcile source-label line)
- tests/test_hermes_quote_tick_contract_v1.py (+9 focused tests)

## Proof — unchanged except source.name
- pricing deterministic: bid/ask copied from tick, mid=(bid+ask)/2, spread=ask-bid, ask>=bid (test)
- quote key unchanged: hermes:quote:XAU_USD:v1 ; schema_version v1 ; instrument XAU_USD ; no XAUUSD
- freshness/status semantics unchanged: FRESH/GREEN ; source_dependencies=[hermes:ticks:XAU_USD:latest:v1] ; notes unchanged
- cadence unchanged: QUOTE_PUBLISH_INTERVAL_SECONDS=5, QUOTE_TTL_SECONDS=15, 5<15, GOV-HERMES-PUBRT-003 present
- gates unchanged: quote dark when gates absent (quote_runner=False, publisher enabled=False)
- consumer_live unchanged: reconciler asserts no consumer_live (None); no consumer cutover
- tick contract untouched

## Tests
- focused provenance/source-label: 9 passed
- quote contract + runtime wiring: 68 passed
- FULL SUITE: branch = main = 51 failed + 4 collection errors (identical pre-existing env/plugin noise);
  NEW failures on branch = 0 ; branch adds 9 passing tests (1077 passed vs 1068)

## Runtime untouched (CODE-ONLY)
No deploy, no restart/recreate, no gate change, no Redis writes (except HELM fabric), no Redis deletes, no SQL,
no market_map/candle/cross-app edits, no consumer_live change, no secrets.
