# Explicit Absence Semantics
Consumers never guess. Every HERMES family carries an explicit status:
- ACTIVE                         — live and publishing
- PENDING / PENDING_FIRST_DAILY_SEAL — armed/gated, awaiting a runtime condition (e.g. D1 latest first seal)
- BLOCKED / BLOCKED_UNTIL_D1_LATEST_GREEN — gated by another gate (D1 history needs D1 latest GREEN)
- NOT_IMPLEMENTED                — HERMES-owned but not yet built (indicators, candle_features)
- INVENTORY_PENDING              — legacy producer/SQL exists; no v1 Redis surface yet (feed_health)
- OWNERSHIP_PENDING              — HERMES-ownership ruling pending (sessions)
- PARTIAL / LEGACY_OR_PARTIAL_CATALOG — partial legacy surface (instrument catalog)
- LEGACY_DEPRECATED / FROZEN_PENDING_CONSUMER_CUTOVER — deprecated, retire after authorised cutover
- FAULT                          — present but failing
Health additionally separates: missing_but_expected_families vs intentionally_absent_families (ARES-owned) vs legacy_deprecated_families.
