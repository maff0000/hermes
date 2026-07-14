# Failure-Mode Matrix + Required Future Tests (DESIGN-ONLY)

| Failure | Containment | Required future test |
|---------|-------------|----------------------|
| Redis unavailable | component fault + backoff; never blocks other runners | redis-unavailable containment |
| malformed gaps payload | BLOCKED_INVALID_INPUT; skip | invalid gaps payload |
| stale gaps payload | BLOCKED_STALE_GAPS | stale gaps key |
| missing coverage source | BLOCKED_STALE_COVERAGE; bound window | partial coverage |
| partial coverage | RETENTION_BOUNDED; do not overclaim | stale coverage |
| closure data conflict | BLOCKED_CLOSURE_TRUTH | closure conflict |
| invalid planning policy | BLOCKED_POLICY (default-deny) | invalid policy |
| planner exception | caught at runner; fault state; no propagation | planner exception containment |
| workload bound exceeded | PARTIAL_BOUNDED_PROPOSAL / BLOCKED_POLICY (planner already handles) | (covered by planner tests) |
| serialization failure | fault state; suppress output | serialization failure |
| repeated identical failure | anti-spam dedupe; alert threshold | repeated failure anti-spam |
| runner crash | supervisor restarts only that runner | runner crash + restart |
| container restart | recompute from live inputs | restart recompute |

The caller MUST NOT: block ticks/candles/quotes/feed-health; alter gaps/backfill truth; trigger repair; delete keys; write SQL;
call a vendor; invoke an executor.

## Full required future-test list (wiring WO)
caller disabled; authorised-without-enabled disabled; enabled-without-authorised fail-closed (component 105); enabled+authorised
invokes planner only; missing gaps; stale gaps; invalid gaps; partial coverage; stale coverage; closure conflict; invalid policy;
unchanged digest -> no duplicate work; changed gaps digest -> recompute; planner exception containment; timeout containment;
no blocking of existing runners; no Redis write; no SQL; no vendor; no old-subsystem import; no proposal publication; no executor
invocation; clean shutdown; restart; duplicate prevention; UTC everywhere; deterministic proposal; all execution flags false.
