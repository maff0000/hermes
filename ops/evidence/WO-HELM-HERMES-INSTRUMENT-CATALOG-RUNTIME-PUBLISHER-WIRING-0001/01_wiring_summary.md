# Instrument-catalog runtime publisher wiring (code-only, disabled/dark by default)

WO: WO-HELM-HERMES-INSTRUMENT-CATALOG-RUNTIME-PUBLISHER-WIRING-0001 · Persona: HELM (HERMES) · Mode: CODE-ONLY
Base: 65a7eaed8aed555641c933a1914a92e9f6f7e804 (current main/runtime, PR #63-#70)

## Changes (3 files, additive; no main.py change)
- utils/hermes_runtime_publisher_steps_v1.py: +instrument_catalog_step(client) — PR #63 supervisor-compatible,
  gate-first: builds ic.build_instrument_catalog_publisher_from_env() (disabled -> no-op, no client touch; enabled-
  without-authorised -> SystemExit(101)); when enabled, builds the governed catalog contract (PR #67/#69/#70 model),
  adds published_at_utc, re-validates (forbidden-key + no :XAUUSD: scan), writes ONLY
  hermes:instrument_catalog:XAU_USD:v1 with TTL. +lazy import of hermes_instrument_catalog_v1 (import-safe).
- utils/hermes_publisher_runtime_v1.py: default_runner_specs() appends the instrument_catalog runner ONLY when its
  gate is enabled+authorised; default remains exactly the four families (control_plane/indicators/candle_features/
  sessions_levels). Enabled-without-authorised -> SystemExit(101) (fail-closed). No Redis I/O (env read only).
- tests/test_instrument_catalog_runtime_wiring_v1.py: +9 tests.

## Behaviour
- DISABLED by default: step no-op (no Redis client/thread/I/O, no write); default_runner_specs = exactly 4;
  supervisor unchanged. Activation requires explicit future runtime env (ENABLED+AUTHORISED+INSTRUMENTS) — no env
  flip is enough on its own without this wiring, which is now present but dark.
- ENABLED+AUTHORISED: 5th runner (instrument_catalog) appended; publishes deterministic HERMES discovery facts to
  hermes:instrument_catalog:XAU_USD:v1 only (canonical XAU_USD; XAUUSD alias-only; dark/pending-runtime markers;
  D1 default PENDING — never inferred ACTIVE; no regime/risk/signal/decision fields; no duplicate hermes:tick:*).
- ENABLED-without-AUTHORISED: SystemExit(101) at both the contract gate and default_runner_specs (fail-closed).

## Tests
203 passed (9 new wiring + PR#63 supervisor suite incl. runners=4-default + instrument_catalog/quote_tick/feed_health/
control_plane/indicators/candle_features/sessions/levels). PR #69/#70 catalog behaviour preserved.

## Zero-runtime-mutation certification
No deploy; no restart; no image build/pull; no runtime env/config change; no Redis write; no SQL write; no runtime-
process touch; no instrument-catalog/feed-health/quote/tick activation; no feed-health/quote runner wiring added;
no market_map touch; no auth/security change; no config in code; no secrets; no duplicate hermes:tick:*; no :XAUUSD:
output. Pure code + tests. The runner is DARK until a separate runtime activation WO sets its env gate.
