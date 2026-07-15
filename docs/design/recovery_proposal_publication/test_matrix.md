# Future Test Matrix & Acceptance Criteria (§28)

For the future eligibility-validator + publisher implementation WOs. Each row is a required test with its expected outcome /
fault code. (This design WO already ships the schema<->fixture subset in `tests/`.)

| Scenario | Expected |
|---|---|
| eligible proposal | PUBLISHED, current pointer written, `ELIGIBLE` |
| failed proposal | refused `PUB_PROPOSAL_STATUS_INELIGIBLE` |
| stale proposal (validated_at old) | refused `PUB_PROPOSAL_STALE`, not current |
| expired proposal | refused `PUB_PROPOSAL_EXPIRED` |
| superseded proposal | refused `PUB_SUPERSEDED` |
| revoked proposal | refused `PUB_REVOKED` |
| blocked proposal | refused `PUB_BLOCKED` |
| inconsistent snapshot | refused `PUB_INCONSISTENT_SNAPSHOT` |
| policy missing | `PUB_POLICY_ABSENT` |
| policy invalid | `PUB_POLICY_INVALID` |
| policy changed mid-life | `PUB_POLICY_CHANGED`, revoke |
| gaps missing | `PUB_GAPS_MISSING` |
| gaps stale | `PUB_GAPS_STALE` |
| gaps digest mismatch | `PUB_GAPS_DIGEST_MISMATCH` |
| coverage missing | `PUB_COVERAGE_MISSING` |
| coverage stale | `PUB_COVERAGE_STALE` |
| coverage digest mismatch | `PUB_COVERAGE_DIGEST_MISMATCH` |
| closure unresolved | `PUB_CLOSURE_INCOMPLETE` / `PUB_UNRESOLVED_EXCEPTIONAL_CLOSURE` |
| unsupported planner version | `PUB_UNSUPPORTED_PLANNER_VERSION` |
| unsupported contract version | `PUB_UNSUPPORTED_CONTRACT_VERSION` |
| non-canonical alias `XAUUSD` | `PUB_NON_CANONICAL_INSTRUMENT` |
| oversized payload | `PUB_PAYLOAD_TOO_LARGE` |
| duplicate publisher | `PUB_DUPLICATE_PUBLISHER` |
| stale writer (lower generation) | `PUB_STALE_WRITER` |
| Redis unavailable | `PUB_REDIS_UNAVAILABLE`, degrade, pointer expires |
| retry idempotency (same generation) | no-op, no duplicate |
| restart with old key | not trusted; revalidated or revoked/expired |
| TTL expiry | pointer gone; external ABSENT |
| TTL renewal requires full revalidation | renew only after re-pass |
| gap invariant violation | `PUB_GAP_INVARIANT_VIOLATION` |
| no executor call | assert zero executor/job/queue/backfill/repair |
| no SQL mutation (pre-audit-sink) | assert none |
| no cross-application import | AST import scan clean |
| gate truth table | absent/absent, absent/true, true/absent (mismatch, no SystemExit), true/true |
