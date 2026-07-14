# Failure-Mode Matrix + Required Future Tests (DESIGN-ONLY)

| Failure | Containment | Required future test |
|---------|-------------|----------------------|
| Redis unavailable | component fault + backoff; never blocks other runners | redis-unavailable containment |
| malformed gaps payload | BLOCKED_INVALID_INPUT; skip | invalid gaps payload |
| stale gaps payload | BLOCKED_STALE_GAPS | stale gaps key |
| missing/partial coverage | RETENTION_BOUNDED; do not overclaim | partial/stale coverage |
| unresolved exceptional closure | BLOCKED_UNCLASSIFIED_MARKET_STATE; affected scope not PROPOSAL_READY | suspected holiday no governed evidence -> unclassified/blocking |
| policy file missing | POLICY_FILE_MISSING; component blocked (default-deny) | missing policy blocks |
| policy JSON invalid | POLICY_JSON_INVALID; blocked | malformed JSON blocks |
| policy schema invalid | POLICY_SCHEMA_INVALID; blocked | schema-invalid blocks |
| policy version unsupported | POLICY_VERSION_UNSUPPORTED; blocked | unsupported version blocks |
| policy instrument != XAU_USD | POLICY_INSTRUMENT_INVALID; blocked | wrong instrument blocks |
| policy invalid replacement | keeps blocking new cycles; reported in health | invalid replacement blocks new planning |
| planner exception | caught at runner; fault state | planner exception containment |
| serialization failure | fault state; suppress | serialization failure |
| repeated identical failure | anti-spam dedupe; alert threshold | repeated failure anti-spam |
| runner crash | supervisor restarts only that runner | runner crash + restart |
| gate enabled-without-authorised | terminal component fault 105; runner blocked; others live | component 105 fail-closed |

Caller MUST NOT: block tick/candle/quote/feed-health; alter gaps/backfill truth; trigger repair; delete keys; write SQL; call
a vendor; import ARES; invoke an executor; write any planner-health/proposal Redis key.

## Full required future-test list (wiring WO)
Exceptional closures: regular weekend excluded; suspected holiday w/o governed evidence -> unclassified; affected proposal cannot
become PROPOSAL_READY; no ARES import; no vendor call.
Policy: valid mounted policy loads; missing blocks; malformed JSON blocks; schema-invalid blocks; unsupported version blocks;
wrong instrument blocks; invalid replacement blocks new planning; no code-default fallback; digest stable; atomic replacement handled.
Health: no Redis health write; no alternative health key; structured logs emitted; supervisor reports runner state; proposal
detail not dumped into logs; backfill-status key not mutated.
Core: caller disabled; authorised-without-enabled disabled; enabled-without-authorised component-105 fail-closed; enabled+authorised
invokes planner only; unchanged digest -> no dup work; changed gaps digest -> recompute; timeout containment; no blocking of existing
runners; no Redis write; no SQL; no vendor; no old-subsystem import; no proposal publication; no executor invocation; clean
shutdown; restart; duplicate prevention; UTC; deterministic proposal; all execution flags false.
