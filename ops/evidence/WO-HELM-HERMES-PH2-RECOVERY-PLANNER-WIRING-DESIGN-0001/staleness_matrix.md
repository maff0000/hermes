# Staleness / Freshness Matrix (DESIGN-ONLY)

| Input | Max accepted age | Stale blocks planning? | Fault code / status | Stale last-known-good usable? | Output on stale |
|-------|------------------|------------------------|---------------------|-------------------------------|-----------------|
| gaps snapshot | 2x gaps cadence (120s) | YES | BLOCKED_STALE_GAPS | NO | suppress; status only |
| coverage snapshot | policy coverage_max_age | YES | BLOCKED_STALE_COVERAGE | NO | suppress |
| closure snapshot | policy closure_max_age | YES (governed) / warn (uncertain) | BLOCKED_CLOSURE_TRUTH / warning | NO | block or UNCLASSIFIED |
| policy version | on change | YES if missing/invalid | BLOCKED_POLICY | NO | suppress |
| instrument catalogue | catalogue freshness | context only | warning | last-good context only | continue with warning |
| D1 history floor | policy version | YES if absent | BLOCKED_POLICY | NO | suppress |
| input consistency (instrument/version mismatch across inputs) | n/a | YES | BLOCKED_INPUT_INCONSISTENCY | NO | suppress |

Rule: NO stale-as-live. A proposal from stale/incomplete inputs is NEVER PROPOSAL_READY; it takes a BLOCKED_* status.
