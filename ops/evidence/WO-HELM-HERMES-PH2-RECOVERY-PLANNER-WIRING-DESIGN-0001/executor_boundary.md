# Executor Boundary (DESIGN-ONLY)

Hard future pipeline (each stage a SEPARATE governed WO):
  1. Planner (this) -> 2. Proposal publication -> 3. Operator approval -> 4. Executor -> 5. Verification -> 6. Recovery-state update.

A future executor MUST:
- NOT execute solely because a proposal exists;
- require SEPARATE execution gates (distinct from planner gates);
- require explicit job identity;
- require approved source/provenance;
- require bounded segments;
- require idempotency;
- require rollback/abort doctrine;
- record all writes;
- validate results;
- update status honestly;
- NEVER mutate gaps truth directly to hide failures.

No executor code is authorised in this or the wiring WO. Proposal existence conveys ZERO execution authorisation. (ADR-0010.)
