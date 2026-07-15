# Restart & Cold-Start Sequence (§13, §14)

**Doctrine: a retained external proposal must NOT automatically become current after a publisher/HERMES restart. Eligibility
is re-established from live inputs, or the stale key expires.** No prior in-memory proposal survives a process restart
(planner holder is process-local by frozen decision 8). No startup path republishes from Redis alone.

## Revalidation triggers (§13) — recompute the full eligibility predicate on any of:
planner runner observation · policy file change · gaps semantic change · coverage semantic change · closure-model change ·
planner-version change · source-contract-version change · publisher restart · process restart · Redis reconnect · TTL renewal ·
explicit revocation · superseding proposal.

Revalidation uses **semantic inputs** (digests) for the match, **and independently checks freshness timestamps** — semantic
idempotency must never hide a stale input (a digest can match while the underlying data is stale; both are checked).

## Cold-start sequence (publisher process start)
```
1. Read gates. If not ENABLED∧AUTHORISED -> PUB_DISABLED/PUB_GATE_MISMATCH; do nothing further (dark). END.
2. Do NOT read/trust K1 as current. (A retained pointer from a prior process is suspect.)
3. Planner holder is empty at cold start (process-local). -> no CANDIDATE yet.
4. Until the planner regenerates a holder AND full eligibility passes:
     - the retained K1 (if any) is left to expire by its TTL (bounded by proposal_ttl_seconds), OR
     - the publisher proactively writes a REVOKED tombstone to K2 marking any prior pointer not-current,
       and (optionally) DELETEs a stale K1 whose validated_at is older than revalidation_max_age_seconds.
5. First cycle with holder present -> CANDIDATE -> VALIDATING -> (ELIGIBLE -> PUBLISHED new generation) | REFUSED(code).
```

## Neutralising retained stale external state (exact)
- The current pointer K1 is **TTL-bounded**, so at worst it self-expires within `proposal_ttl_seconds`.
- Additionally, on cold start the publisher SHOULD write K2 status `= {status: REVOKED, reason: PUBLISHER_RESTART}` if it
  cannot immediately revalidate, so a consumer reading K2 sees not-current *before* K1's TTL lapses.
- A consumer independently rejects any K1 whose `validated_at_utc` age exceeds `revalidation_max_age_seconds`, so even a
  not-yet-expired retained K1 is not treated as current after a stall.
- **No** startup path copies K1 forward as current. **No** in-memory proposal is reconstructed from Redis.

## Failure matrix at start
| Condition at start | Result |
|---|---|
| policy absent | `PUB_POLICY_ABSENT`; no publish; revoke prior pointer |
| gates absent | dark; no publish; prior pointer expires |
| source inputs missing/stale | refuse (`PUB_GAPS_*`/`PUB_COVERAGE_*`); no publish |
| holder not yet regenerated | `PUB_NO_CURRENT_PROPOSAL`; no publish |
| Redis retains old K1 | not trusted; revalidated or expired/revoked |
