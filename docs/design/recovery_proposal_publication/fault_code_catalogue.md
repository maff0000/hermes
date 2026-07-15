# Publication Fault / Refusal Code Catalogue (§6, §18)

Stable, non-collapsing codes. Refusals are **never** flattened into a generic `INVALID`. Each code has a defined **effect**.
All codes are `PUB_*`; the numeric `component_code` is reserved for a future implementation (kept distinct from the planner's
`105`). Every fault is **component-level only** — never `SystemExit`, never process-wide shutdown (§17).

Effect legend: **no-key** = do not create/keep a current pointer; **revoke** = write tombstone in status key; **stale** = mark
status stale (pointer may exist until TTL but is not-current); **degrade** = publication-component health degraded;
**alert** = raise governed alert; **retry** = bounded retry permitted next cycle.

| Code | Meaning | Effect |
|---|---|---|
| `PUB_GATES_DISABLED` | publisher feature not enabled | no-key (silent, expected dark state) |
| `PUB_GATE_MISMATCH` | enabled without authorised (or malformed gate) | no-key · degrade (component-level, no SystemExit) |
| `PUB_UNSUPPORTED_CONTRACT_VERSION` | contract version not in supported set | no-key · alert |
| `PUB_UNSUPPORTED_PLANNER_VERSION` | planner version not authorised | no-key · alert |
| `PUB_NO_CURRENT_PROPOSAL` | holder empty | no-key (expected when nothing to recover) |
| `PUB_PROPOSAL_STATUS_INELIGIBLE` | holder status not READY/held (e.g. FAILED/BLOCKED) | no-key · revoke-if-was-current |
| `PUB_PROPOSAL_STALE` | validated_at too old (missed revalidation) | stale · revoke-if-persist |
| `PUB_PROPOSAL_EXPIRED` | now ≥ expires_at_utc | no-key (TTL already neutralising) |
| `PUB_SUPERSEDED` | newer generation is current | superseded (replaced atomically) |
| `PUB_REVOKED` | explicit revocation | revoke (tombstone) |
| `PUB_BLOCKED` | governed planner block in scope | no-key · revoke-if-was-current |
| `PUB_POLICY_ABSENT` | policy not present | no-key · revoke-if-was-current · alert |
| `PUB_POLICY_INVALID` | policy schema invalid | no-key · alert |
| `PUB_POLICY_CHANGED` | policy version/digest ≠ proposal | revoke-if-was-current (input drift) |
| `PUB_GAPS_MISSING` | gaps contract absent | no-key · revoke-if-was-current |
| `PUB_GAPS_STALE` | gaps age > max | stale/revoke |
| `PUB_GAPS_DIGEST_MISMATCH` | gaps semantic digest ≠ proposal | revoke-if-was-current (drift) |
| `PUB_COVERAGE_MISSING` | coverage state absent | no-key · revoke-if-was-current |
| `PUB_COVERAGE_STALE` | coverage age > max | stale/revoke |
| `PUB_COVERAGE_DIGEST_MISMATCH` | recovery-relevant coverage digest ≠ proposal | revoke-if-was-current (drift) |
| `PUB_CLOSURE_INCOMPLETE` | closure model incomplete for scope | no-key |
| `PUB_UNRESOLVED_EXCEPTIONAL_CLOSURE` | scope intersects unresolved/unknown/conflicting closure | no-key (fail-closed, §20) |
| `PUB_INCONSISTENT_SNAPSHOT` | pre/post inconsistency / exhausted retry | no-key · retry-next-cycle |
| `PUB_NON_CANONICAL_INSTRUMENT` | instrument ≠ XAU_USD or `XAUUSD` alias present | no-key · alert (abuse signal, §24) |
| `PUB_OUT_OF_RETENTION` | scope beyond retention | no-key |
| `PUB_UNCLASSIFIED_PRESENT` | unclassified segment and not governed-allowed | no-key |
| `PUB_GAP_INVARIANT_VIOLATION` | some gap end ≠ start+period (§21) | no-key · alert |
| `PUB_DUPLICATE_PUBLISHER` | another writer identity active | no-key · degrade · alert (§16) |
| `PUB_STALE_WRITER` | generation/sequence not monotonic vs current | no-key (CAS reject) |
| `PUB_REDIS_UNAVAILABLE` | Redis read/write failed | no-key · degrade · retry (pointer expires by TTL) |
| `PUB_ATOMIC_PUBLICATION_FAILED` | CAS/transaction failed (race) | no-key · retry-next-cycle |
| `PUB_PAYLOAD_TOO_LARGE` | envelope exceeds size cap | no-key · alert (never truncate silently, §11) |
| `PUB_SCHEMA_VALIDATION_FAILED` | envelope fails JSON Schema | no-key · alert |

**Universal invariant:** whenever eligibility is *lost* for a proposal that was current, the publisher MUST either write a
newer valid generation (supersede) or actively neutralise the old pointer (revoke/expire). It must **never** leave a prior
proposal apparently-current after eligibility is lost.
