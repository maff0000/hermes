# Supersession, Revocation & Atomicity (§15, §16)

## Generation & sequence
- `proposal_id` — deterministic hash of proposal *semantics* (from the pure planner; identical inputs ⇒ identical id).
- `proposal_generation` — a **monotonic integer** minted by the publisher at each *successful publication*, strictly
  increasing per governed scope (`XAU_USD`). It is the **publication** sequence, distinct from `proposal_id`.
- Relationship: the **same** `proposal_id` MAY be published under a **later** `proposal_generation` if re-validated after a
  drift-and-recovery (e.g. inputs changed then reverted). Consumers order by `proposal_generation`, not by `proposal_id`.

## Supersession
- A new eligible proposal supersedes the current one by writing K1 with a **strictly greater** `proposal_generation`, carrying
  `supersedes_proposal_id` + `supersedes_generation`.
- The write is **atomic compare-and-set**: publish only if the currently-stored generation is `< new` (and no revocation
  tombstone is newer). This guarantees a consumer never sees two current proposals for the same scope.
- The prior generation is *not* separately retained in Redis (no history); its audit trail is the SQL sink (§22) when authorised.

## Revocation
- Revocation = write a **tombstone** to K2 `{status: REVOKED, proposal_id, proposal_generation, revocation_reason,
  revoked_at_utc}` with `tombstone_ttl_seconds`, and neutralise K1 (DELETE or let TTL expire).
- A tombstone with generation ≥ the current pointer's generation makes the pointer not-current even if K1 lingers until TTL.
- Withdrawal (`WITHDRAWN`) is the same mechanism with `reason=WITHDRAWN` and no successor.

## Atomicity / concurrency protections (§16)
| Threat | Mitigation |
|---|---|
| duplicate publisher runners | **single-writer doctrine** + `publisher_instance_id` fence; a second writer detects a live fence and refuses (`PUB_DUPLICATE_PUBLISHER`) |
| concurrent proposals | monotonic `proposal_generation` + CAS: only strictly-greater wins |
| out-of-order / stale-writer overwrite | CAS on generation; lower/equal generation ⇒ `PUB_STALE_WRITER`, no write |
| Redis reconnect replay | idempotency key = `(proposal_id, proposal_generation)`; re-applying the same generation is a no-op |
| publication retry | bounded; retry re-runs eligibility (not a blind rewrite) and re-CAS |
| partial multi-key update | write **K1 then K2** with the SAME generation; a consumer trusts K1 only if K2 is absent-or-consistent; recommend a Lua/`MULTI` script to make the pair atomic (assessed, **not implemented** here) |
| leader change / container restart during publish | fence + generation CAS make a resumed/second writer safe; worst case the pointer expires by TTL |

### Locking stance
No distributed lock is introduced. Single-writer + monotonic-generation CAS + writer-identity fence is sufficient and cheaper
than a lock. A lease/fencing token is justified **only** if a future multi-instance HERMES deployment allows two publisher
processes; that is out of scope here and must be separately architected. The atomic K1+K2 update is best done with a server-side
script (`EVAL`) or `WATCH/MULTI/EXEC`; the design specifies the semantics, the implementation WO chooses the mechanism.
