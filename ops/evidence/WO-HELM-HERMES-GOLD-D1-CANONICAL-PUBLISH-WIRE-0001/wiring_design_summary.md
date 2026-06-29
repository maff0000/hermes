# Wiring Design Summary — D1 Canonical Publish (6xH4)
**WO-HELM-HERMES-GOLD-D1-CANONICAL-PUBLISH-WIRE-0001 · code-only · HELM/HERMES**

## Producer
`utils/candle_d1_publish_wire_v1.py` adds `CanonicalD1Producer` (mirrors the H4 producer). Feed it SEALED H4
candles via `on_h4_close`; it groups six H4 into a fixed 22:00Z->22:00Z D1 bucket and, on day roll-over (the
next D1 day's first H4 arrives), derives the prior bucket via merged `candle_d1_derivation_v1.derive_d1` and
routes the governed D1 envelope through the existing `SerializingCandleCanonicalWriter`.

## SAFEST publication rule
A D1 latest is published ONLY for a COMPLETE closed 6/6 bucket (status OK). A sealed <6 bucket is
**never published** (not even as SOURCE_INCOMPLETE) — honestly skipped (`D1_INCOMPLETE_NOT_PUBLISHED`,
counter `d1_skipped_incomplete`). No synthesis of H4 children.

## D1 publishable ONLY via the governed path
D1 added to `CANONICAL_PUBLISH_TIMEFRAMES` so the writer's `assert_canonical_key` accepts the D1 latest key.
The DIRECT seam still refuses D1 (not in SUPPORTED_TF/DERIVED_TF) and the H4 producer only emits H4 — so the
D1 producer is the ONLY component that can ever publish a D1 latest key. Generic/direct D1 publication is
impossible. The legacy "D" token remains never published. D1 history stays blocked (assert_history_target).

## No latest behaviour change
M1/M5/M15/H1/H4 latest + the forward-history writer are untouched. No Redis I/O at import; disabled -> no-op.
