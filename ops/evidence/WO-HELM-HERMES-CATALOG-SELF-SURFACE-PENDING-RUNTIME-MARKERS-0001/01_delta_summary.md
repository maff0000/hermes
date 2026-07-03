# Catalog self-surface + pending-runtime markers (delta on merged PR #69)

WO: WO-HELM-HERMES-CATALOG-SELF-SURFACE-PENDING-RUNTIME-MARKERS-0001 · Persona: HELM (HERMES) · Mode: CODE-ONLY
Base: 4589591ed193429fd155dbf2fc7f9e00a1b6e1b5 (main, PR #69 merged)
Additive delta ONLY — PR #69 quote/tick work untouched except one consistency marker (feed_health live:false).

## Changes (2 files)
utils/hermes_instrument_catalog_v1.py:
- default_catalog_snapshot: +"instrument_catalog": CODE_PRESENT_DARK (PR #67 merged inert -> code present, not runtime-live).
- _aggregate_status: include instrument_catalog (backward-compat .get) in the UNKNOWN fail-loud scan; it is EXPECTED
  non-active and does NOT degrade core GREEN.
- build: validate the self-surface status (ic_status = snap.get(..., CODE_PRESENT_DARK)); add instrument_catalog_contract
  {key hermes:instrument_catalog:XAU_USD:v1, status CODE_PRESENT_DARK, live:False}; add "instrument_catalog" to surfaces;
  feed_health_contract +live:False (marker consistency); NEW payload field pending_runtime_deployment
  {note, dark_surfaces:[feed_health,instrument_catalog,quote,tick], runtime_live:False} computed from snapshot.
tests/test_hermes_instrument_catalog_v1.py: +8 delta tests.

## Truth after delta
- instrument_catalog self-surface = CODE_PRESENT_DARK, key hermes:instrument_catalog:XAU_USD:v1, live=False.
- pending_runtime_deployment.dark_surfaces = {feed_health, instrument_catalog, quote, tick}; runtime_live=False;
  note: "code/catalog presence is NOT the same as live publication."
- PR #69 preserved: quote/tick CODE_PRESENT_DARK; tick references EXISTING hermes:ticks:XAU_USD:latest:v1; no
  duplicate hermes:tick:*; no :XAUUSD: output key; D1 default PENDING (not hard-coded ACTIVE; no liveness inferred
  from the D1 GREEN seal). No signal/regime/risk/decision/ARES semantics.

## Tests
173 passed (37 catalog incl 8 new + 136 quote_tick/feed_health/control_plane/indicators/candle_features/sessions/levels).

## Zero-runtime-mutation certification
No deploy; no restart; no image build/pull; no env/config change; no Redis write; no SQL write; no runtime-process
touch; no feed-health/catalog/quote/tick activation; no market_map touch; no auth/security change; no new tick
contract; no hermes:tick:*; no :XAUUSD: output; no ARES/HELIOS/Falcon/NEO/SOLO/PLUTUS touch. Pure code + tests.
