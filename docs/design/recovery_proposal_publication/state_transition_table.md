# Proposal-Publication State Machine (§7)

Two distinct state spaces are modelled and kept separate:
- **External/publication state** (what the *contract* exposes to consumers) — the authoritative state carried in the envelope
  `status` field and the current-pointer/status keys.
- **Internal publisher lifecycle** (`ABSENT → CANDIDATE → VALIDATING → ELIGIBLE/…`) — how the publisher *arrives* at a
  decision each cycle; never externally visible except through its result.

Law: **no transition may silently change a FAILED/STALE/EXPIRED/REVOKED proposal back to current.** A fresh planner result is
always a *new generation* (new `proposal_generation`), never an in-place mutation of historical truth.

## Internal publisher lifecycle (per revalidation cycle)
| State | Entry | Permitted → | Forbidden → | External visibility |
|---|---|---|---|---|
| `ABSENT` | no holder proposal | `CANDIDATE` | direct→`PUBLISHED` | none |
| `CANDIDATE` | holder proposal present this cycle | `VALIDATING` | skip validation | none |
| `VALIDATING` | eligibility predicate running | `ELIGIBLE`, `REFUSED(code)` | `PUBLISHED` without full pass | none |
| `ELIGIBLE` | all matrix rows passed | `PUBLISHED` (atomic CAS) | mutate historical generation | none until write |
| `REFUSED(code)` | any matrix row failed | `ABSENT`/`CANDIDATE` next cycle; may emit revoke/tombstone | become current | status record only |

## External/publication states (contract `status`)
| State | Entry condition | Permitted transitions | Forbidden transitions | Redis representation | TTL | Restart behaviour | Execution may consume? |
|---|---|---|---|---|---|---|---|
| `ABSENT` | no current pointer (never published / expired / neutralised) | ←from any on expiry/revoke; →`PUBLISHED` on eligible write | — | current key **absent** | n/a | default cold state | **No** (nothing) |
| `PUBLISHED` | eligible envelope written atomically as current | →`HELD_CURRENT` (revalidated), →`SUPERSEDED`, →`STALE`, →`REVOKED`, →`EXPIRED` | →current after any failure | current key present, fresh | `proposal_ttl_seconds` | **not** trusted; must revalidate | **No** (advisory only) |
| `HELD_CURRENT` | prior PUBLISHED re-passed full eligibility this cycle; TTL renewed | same as PUBLISHED | renew TTL without revalidation | current key, TTL renewed | renewed on revalidation | not trusted on restart | **No** |
| `SUPERSEDED` | a newer generation became current | terminal for that generation; new gen is `PUBLISHED` | resurrect as current | overwritten by new gen (CAS) | — | — | **No** |
| `STALE` | validated_at age > `revalidation_max_age_seconds` and not yet expired | →`EXPIRED`, →`REVOKED`; →`PUBLISHED` only via **new** eligible generation | STALE→current silently | status record marks stale; pointer may still exist until expiry but consumer MUST treat as not-current | pointer TTL still counts down | neutralised | **No** |
| `REVOKED` | explicit revocation (input drift/eligibility lost) | terminal; consumers must drop | REVOKED→current | tombstone in status key (short TTL) | tombstone TTL | tombstone visible until TTL | **No** |
| `BLOCKED` | planner/inputs yielded a governed block (`BLOCKED_*`) | →`REVOKED`/`ABSENT` | BLOCKED→`PUBLISHED` | status record; no current pointer | — | — | **No** |
| `FAILED` | planner invocation failed / generation error | →`REVOKED`/`ABSENT` | FAILED→current | status record; no current pointer | — | — | **No** |
| `EXPIRED` | `now ≥ expires_at_utc` | →`ABSENT` | EXPIRED→current | current key gone by TTL | — | cold default | **No** |
| `WITHDRAWN` | operator/governed withdrawal (e.g. contract retirement) | terminal | WITHDRAWN→current | tombstone | tombstone TTL | — | **No** |

Notes:
- `SUPERSEDED` vs `WITHDRAWN`: supersession is *replacement* by a newer valid generation; withdrawal is *removal* with no
  successor.
- No external state permits execution consumption — the whole space is advisory (§8).
- A consumer resolves currency by: current key present ∧ schema-valid ∧ `status==PUBLISHED|HELD_CURRENT` ∧ not expired ∧ no
  matching tombstone in the status key. Anything else = not current (§16).
