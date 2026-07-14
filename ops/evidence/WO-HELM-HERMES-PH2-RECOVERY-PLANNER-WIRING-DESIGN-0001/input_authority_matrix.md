# Input Authority Matrix (DESIGN-ONLY)

| Input | Authoritative source (v1) | Access | Contract/validation | Freshness | Completeness | Failure -> status |
|-------|---------------------------|--------|---------------------|-----------|--------------|-------------------|
| GapSurfaceSnapshot | hermes:gaps:XAU_USD:v1 (governed live) | GET read-only | v1; instrument XAU_USD; vocab; d1_boundary | age <= 2x gaps cadence (120s) via generated_at_utc | full per-tf gaps truth | absent->BLOCKED_INPUT; invalid->BLOCKED_INVALID_INPUT; stale->BLOCKED_STALE_GAPS |
| ExistingCoverageSnapshot | governed candle history index hermes:candles:XAU_USD:{tf}:history:v1:index | GET/ZRANGE read-only | v1; RETENTION_BOUNDED marker; provenance=governed_redis_history_index | index freshness | RETENTION-BOUNDED ONLY (M1-H4 35d, D1 120d) — NOT full history | missing/partial->BLOCKED_STALE_COVERAGE / bound window |
| MarketClosureSnapshot | HERMES regular schedule (policy) + governed exceptional-closure evidence | in-memory / governed contract | authority in accepted set; provenance; version | effective_at_utc | regular=deterministic; exceptional=governed only | unverified->UNCLASSIFIED_MARKET_STATE (warn); conflict->BLOCKED_CLOSURE_TRUTH |
| RecoveryPlanningPolicy | governed external policy contract (no config in code) | read-only load | v1 schema; digest; secret-free | policy version | full | missing/invalid->BLOCKED_POLICY (default-deny) |
| D1 history floor | policy d1_history_floor_seconds | policy | governed | policy version | governed floor | missing->BLOCKED_POLICY |

**Honesty rule:** the retention-bounded history index is authoritative for *presence within retention only*. Beyond retention
is OUT_OF_RETENTION (excluded, not "missing"). Full-history completeness needs a SEPARATE coverage-truth surface WO.
