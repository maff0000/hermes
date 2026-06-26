# H4 Canonical Publish Wiring — Implementation Summary
**WO-HELM-HERMES-GOLD-H4-CANONICAL-PUBLISH-WIRE-0001 · code-only · no Redis I/O, no deploy/activation**

Closes the activation halt (`RED_BLOCKED_H4_PUBLISH_PATH_NOT_BUILT`). The three verified blockers are fixed:

## Blocker fixes
1. **Writer grid** (`candle_publisher_v1.py`): `CANONICAL_PUBLISH_TIMEFRAMES += "H4"` → `assert_canonical_key`
   now ACCEPTS `hermes:candles:XAU_USD:H4:latest:v1`. Guards intact: XAUUSD→KEY-004, D1→KEY-005,
   unversioned→KEY-002 (see 01).
2. **Seam derived-only** (`candle_runtime_seam_v1.py`): `DERIVED_TF=("H4",)`; the DIRECT canonical seam emit
   refuses H4 with reason `H4_DERIVED_PATH_ONLY` (never a stale direct candle). M1/M5/M15/H1 unchanged.
3. **Producer wiring** (`candle_h4_publish_wire_v1.py`, new): `CanonicalH4Producer.on_h1_close` buffers H1,
   rolls the NY-5PM (fixed 22:00 UTC) bucket, and on roll-over `derive_h4`s the sealed bucket and routes it
   through the governed `SerializingCandleCanonicalWriter`. Derived from H1 only — no `candles_H4`, no
   Proteus, no M15 fallback, no synthesis. XAU_USD allowlist (XAUUSD→XAU_USD, no alias key).
4. **Live wiring** (`main.py`): boots `build_h4_producer_from_env()` (gated) and feeds completed H1 candles
   to it. Default **disabled** (`DisabledH4Producer` no-op) — deploying this does NOT activate H4.

## Completeness (honest; proof in 01)
Sealed 4/4 bucket → status **OK**; sealed <4 → **SOURCE_INCOMPLETE** (never OK), coverage<1, gap INCOMPLETE.
Published H4 open aligns to a ratified anchor (02:00 in the proof; ∈ {22,2,6,10,14,18}). Provenance
DERIVED_FROM_LOWER_TIMEFRAME / source_timeframe=H1 / policy DERIVED_H4_FROM_H1.

## Config governance / gating
H4 gated by the SAME canonical controls (publish_enabled+authorised, XAU_USD allowlist, explicit bus) PLUS
`HERMES_CANDLE_H4_PUBLISH_ENABLED` (default false). Missing governed config when H4-enabled fails loud. No
hidden defaults. `build_h4_producer_from_env()` returns `DisabledH4Producer` unless forwarding=on +
SINK=canonical + H4 flag=true.

## Tests — candle lane 264 pass
New `tests/test_candle_h4_publish_wire_v1.py` (14): key-grid accept/reject, producer 4/4-OK publish, anchor,
3/4-incomplete-never-OK, no-publish-until-roll, non-H1 ignored, non-allowlisted skip, XAUUSD→XAU_USD,
disabled no-op, from-env gating, no-SQL/no-stale-table/no-M15/no-regime. Updated 3 existing tests (H4 now a
governed publish TF). M1/M5/M15/H1 + history-guard tests still pass.

## NOT done (this WO)
No deploy/restart/activation, no Redis writes, no SQL, no H4 live publication, no H4 history backfill,
no D1, no regime, no XAUUSD output, no shadow, no consumer cutover, no Proteus/structure_engine.
