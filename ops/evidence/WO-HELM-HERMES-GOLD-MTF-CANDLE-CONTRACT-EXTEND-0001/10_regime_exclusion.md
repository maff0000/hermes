# Regime Exclusion — Architect Option A

## Ruling
HERMES must **NOT** publish a true `regime`. HERMES owns deterministic candle-state/features only; ARES
owns true regime. This PR adds **only** deterministic geometry (`body_*`, `range_size`, `wick_*`,
`candle_direction`) — no `regime`, no `regime_confidence`, no structure/CHoCH/BOS/order-block, no
confidence/permission/risk/projection.

## Enforcement (defence in depth)
1. **Forbidden-token scan** (`_scan_forbidden`, `GOV-CANDLE-CONTRACT-029`): recursively rejects any field
   name containing `regime`, `structure`, `choch`, `bos`, `order_block`, `signal`, `setup`, `confidence`,
   `permission`, `risk`, `cockpit`, `projection`, `support`, `resistance`. A `regime` field therefore
   cannot be added to the payload without failing validation.
   Proof: `test_no_forbidden_regime_field_can_leak` → `GOV-CANDLE-CONTRACT-029`.
2. The new fields were chosen to contain **no** forbidden token: `candle_direction`, `body_high`,
   `body_low`, `body_size`, `range_size`, `wick_high`, `wick_low`.
3. `candle_direction` is the pure sign of `close - open` — a deterministic fact, not a regime/trend call.

## Validation matrix
See `07_validation_failloud_matrix.txt`: tampering a payload with `regime="TREND"` is rejected with
`GOV-CANDLE-CONTRACT-029`.
