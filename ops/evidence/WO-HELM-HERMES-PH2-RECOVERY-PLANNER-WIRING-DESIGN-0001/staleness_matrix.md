# Staleness / Freshness Matrix (DESIGN-ONLY)

| Input | Max accepted age | Stale blocks planning? | Fault code / status | Stale last-known-good usable? | Output on stale |
|-------|------------------|------------------------|---------------------|-------------------------------|-----------------|
| gaps snapshot | 2x gaps cadence (120s) | YES | BLOCKED_STALE_GAPS | NO | suppress; status only |
| coverage snapshot | policy staleness_policy.coverage_max_age | YES | BLOCKED_STALE_COVERAGE | NO | suppress |
| regular closure schedule | policy version | YES if policy invalid | POLICY_SCHEMA_INVALID | NO | block |
| exceptional closure (Option A) | n/a (not consumed) | unresolved -> block | BLOCKED_UNCLASSIFIED_MARKET_STATE | NO | block affected scope |
| mounted policy file | on atomic replacement | YES if missing/invalid | POLICY_FILE_MISSING / POLICY_JSON_INVALID / POLICY_SCHEMA_INVALID / POLICY_VERSION_UNSUPPORTED / POLICY_INSTRUMENT_INVALID / POLICY_DIGEST_FAILED | NO (present-but-invalid blocks; last-good only if separately governed) | suppress |
| instrument catalogue | catalogue freshness | context only | warning | last-good context only | continue with warning |
| input consistency | n/a | YES | BLOCKED_INPUT_INCONSISTENCY | NO | suppress |

Rule: NO stale-as-live. Stale/incomplete inputs -> BLOCKED_* / POLICY_*, NEVER PROPOSAL_READY. A present-but-invalid mounted
policy blocks NEW planning cycles until corrected; stale content is never represented as newly valid.
