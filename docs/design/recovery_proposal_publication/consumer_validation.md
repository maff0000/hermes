# Consumer Validation Rules (§23)

There is **no currently-authorised consumer** (confirmed by HERMES source search). Falcon is **not** a consumer and remains
untouched. A future authorised consumer MUST, before treating a proposal as current:

1. Read `hermes:recovery_proposal:XAU_USD:v1`; if absent/expired ⇒ **no current proposal** (fail closed, not an error).
2. Validate `contract == "hermes.recovery_proposal.publication"` and `contract_version` ∈ its supported set; else reject.
3. Validate against the JSON Schema (schema_version); reject on failure.
4. Validate `instrument == canonical_instrument == "XAU_USD"`; reject any `XAUUSD`.
5. Check `status ∈ {PUBLISHED, HELD_CURRENT}`; any other status ⇒ not current.
6. Check `publication_eligibility == "ELIGIBLE"`.
7. Check `now < expires_at_utc` AND `validated_at_utc` age ≤ the consumer's own freshness bound.
8. Read `hermes:recovery_proposal:status:XAU_USD:v1`; if it carries a tombstone/`REVOKED`/`SUPERSEDED`/`WITHDRAWN` with
   generation ≥ the pointer's ⇒ not current.
9. Verify safety flags: `execution_authorised==false`, `execution_started==false`, `publication_only==true`,
   `executor_bound==false`, `consumer_live==false`; if any differs ⇒ reject (contract breach).
10. **Never infer execution authority.** A proposal is planning truth only.
11. Fail closed on unknown governed fields; tolerate ONLY additive, backward-compatible schema evolution under explicit
    version rules (a minor `schema_version` bump may add optional fields; a `contract_version` bump requires opt-in).

A consumer that cannot satisfy all of the above MUST behave as if there is no current proposal.
