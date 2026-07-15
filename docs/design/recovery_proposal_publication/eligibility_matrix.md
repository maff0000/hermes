# Publication Eligibility Matrix (§5)

The publisher computes `eligible = AND(all rows)` **fresh every revalidation cycle** against **live** inputs. First failing
row (in evaluation order) yields the refusal code and short-circuits — no partial publish. All timestamps timezone-aware UTC.
"Digest match" = the recomputed live digest equals the digest the proposal was generated under (carried on the holder).

Evaluation order is layered so cheap/authority checks fail before expensive input recomputation.

## Layer G — publication gates (evaluated first; separate from planner gates, §17)
| # | Condition | True when | Refusal code if false |
|---|---|---|---|
| G1 | Publisher feature enabled | `HERMES_RECOVERY_PUBLISHER_ENABLED` truthy | `PUB_GATES_DISABLED` |
| G2 | Publication authorised | `HERMES_RECOVERY_PUBLISHER_AUTHORISED` truthy | `PUB_GATE_MISMATCH` (enabled∧¬authorised) |
| G3 | Contract version authorised | proposal/env contract_version ∈ supported set | `PUB_UNSUPPORTED_CONTRACT_VERSION` |

## Layer H — holder currency
| # | Condition | Refusal code |
|---|---|---|
| H1 | A proposal object exists in the live holder | `PUB_NO_CURRENT_PROPOSAL` |
| H2 | Holder proposal generated successfully (planner status READY/held, not FAILED) | `PUB_PROPOSAL_STATUS_INELIGIBLE` |
| H3 | Holder proposal is *the current* holder (id+generation == pointer target) | `PUB_SUPERSEDED` |
| H4 | Not superseded by a newer generation | `PUB_SUPERSEDED` |
| H5 | Not revoked | `PUB_REVOKED` |
| H6 | Not marked blocked | `PUB_BLOCKED` |

## Layer P — provenance / policy
| # | Condition | Refusal code |
|---|---|---|
| P1 | Policy present | `PUB_POLICY_ABSENT` |
| P2 | Policy schema valid | `PUB_POLICY_INVALID` |
| P3 | Policy version matches proposal | `PUB_POLICY_CHANGED` |
| P4 | Policy **digest** matches proposal | `PUB_POLICY_CHANGED` |
| P5 | Planner version authorised | `PUB_UNSUPPORTED_PLANNER_VERSION` |

## Layer S — source freshness & semantic match
| # | Condition | Refusal code |
|---|---|---|
| S1 | Gaps contract present | `PUB_GAPS_MISSING` |
| S2 | Gaps contract version authorised | `PUB_UNSUPPORTED_CONTRACT_VERSION` |
| S3 | Gaps data fresh (age ≤ `max_gaps_age_seconds`) | `PUB_GAPS_STALE` |
| S4 | Gaps **semantic digest** matches proposal | `PUB_GAPS_DIGEST_MISMATCH` |
| S5 | Coverage state present | `PUB_COVERAGE_MISSING` |
| S6 | Coverage fresh (age ≤ `max_coverage_age_seconds`) | `PUB_COVERAGE_STALE` |
| S7 | **Recovery-relevant** coverage digest matches proposal | `PUB_COVERAGE_DIGEST_MISMATCH` |
| S8 | Closure model complete for proposal scope | `PUB_CLOSURE_INCOMPLETE` |

## Layer I — integrity & scope
| # | Condition | Refusal code |
|---|---|---|
| I1 | Snapshot pre/post consistency passed | `PUB_INCONSISTENT_SNAPSHOT` |
| I2 | No `BLOCKED_INPUT_INCONSISTENCY` and no exhausted/failed retry | `PUB_INCONSISTENT_SNAPSHOT` |
| I3 | Canonical instrument `XAU_USD`, no `XAUUSD` alias anywhere in envelope | `PUB_NON_CANONICAL_INSTRUMENT` |
| I4 | Within retention limits (M1–H4 ≤35d, D1 ≤120d) | `PUB_OUT_OF_RETENTION` |
| I5 | Gap end=start+period invariant holds for every scoped gap (§21) | `PUB_GAP_INVARIANT_VIOLATION` |
| I6 | No unresolved exceptional closure intersects scope (§20) | `PUB_UNRESOLVED_EXCEPTIONAL_CLOSURE` |
| I7 | No unclassified segment (unless `allow_unclassified_publish` governed-true) | `PUB_UNCLASSIFIED_PRESENT` |

## Layer C — currency & envelope
| # | Condition | Refusal code |
|---|---|---|
| C1 | Not stale (validated within `revalidation_max_age_seconds`) | `PUB_PROPOSAL_STALE` |
| C2 | Not expired (`now < expires_at_utc`) | `PUB_PROPOSAL_EXPIRED` |
| C3 | Publication sequence/generation strictly monotonic vs last published | `PUB_STALE_WRITER` |
| C4 | No newer proposal or revocation exists at write time (CAS) | `PUB_ATOMIC_PUBLICATION_FAILED` |
| C5 | Envelope validates against JSON Schema; payload ≤ size cap | `PUB_SCHEMA_VALIDATION_FAILED` / `PUB_PAYLOAD_TOO_LARGE` |

**Ruling:** all of G,H,P,S,I,C must pass. Any single failure ⇒ **no publish** and a stable refusal code recorded in the
status record (Layer C keeps the *previous* current pointer only if it *independently* still passes — otherwise it is
revoked/expired, never left apparently-current). Holder existence alone (H1) is necessary but the predicate is the authority.
