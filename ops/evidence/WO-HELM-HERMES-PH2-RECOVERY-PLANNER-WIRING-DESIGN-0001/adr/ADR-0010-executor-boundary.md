# ADR-0010: Executor boundary
Context: a proposal must never imply execution.
Decision: hard 6-stage pipeline (plan -> publish -> approve -> execute -> verify -> update), each a separate governed WO; a future
executor needs its own gates/job-identity/provenance/bounds/idempotency/rollback and may never mutate gaps truth to hide failure.
Alternatives: auto-execute on proposal (rejected: catastrophic). Consequences: safety by construction.
Risks: scope pressure to collapse stages -> resisted by governance. Rollback: n/a. Unresolved: executor design (separate WO).
