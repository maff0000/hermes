# Input Authority Matrix (DESIGN-ONLY)

| Input | Authoritative source (v1) | Access | Contract/validation | Freshness | Completeness | Failure -> status |
|-------|---------------------------|--------|---------------------|-----------|--------------|-------------------|
| GapSurfaceSnapshot | hermes:gaps:XAU_USD:v1 (governed live) | GET read-only | v1; instrument XAU_USD; vocab; d1_boundary | age <= 2x gaps cadence (120s) via generated_at_utc | full per-tf gaps truth | absent->BLOCKED_INPUT; invalid->BLOCKED_INVALID_INPUT; stale->BLOCKED_STALE_GAPS |
| ExistingCoverageSnapshot | governed candle history index hermes:candles:XAU_USD:{tf}:history:v1:index | GET/ZRANGE read-only | v1; RETENTION_BOUNDED marker; provenance=governed_redis_history_index | index freshness | RETENTION-BOUNDED ONLY (M1-H4 35d, D1 120d) — NOT full history | missing/partial->BLOCKED_STALE_COVERAGE / bound window |
| MarketClosureSnapshot | HERMES regular schedule ONLY (Option A) | in-memory (policy) | regular Fri22->Sun22 UTC | policy version | regular deterministic; exceptional = UNCLASSIFIED (no governed evidence in first impl) | unresolved exceptional -> BLOCKED_UNCLASSIFIED_MARKET_STATE |
| RecoveryPlanningPolicy | governed READ-ONLY mounted JSON /app/config/recovery_planner_policy.v1.json | file read-only | JSON Schema; version=1; instrument XAU_USD; digest | policy digest/version | full | missing/invalid -> POLICY_* fault (default-deny) |
| D1 history floor | policy d1_history_floor (mounted JSON) | policy | governed | policy version | governed floor | missing/invalid -> POLICY_SCHEMA_INVALID |

Honesty rule: retention-bounded index is authoritative for presence within retention only; beyond retention is OUT_OF_RETENTION
(excluded). Full-history completeness needs a SEPARATE coverage-truth surface WO. Exceptional closures need a SEPARATE contract WO.
