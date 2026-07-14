# Output Disposition (DESIGN-ONLY) — DECISION

Options: (1) in-memory only; (2) structured logs; (3) control-plane inspection; (4) future Redis proposal contract;
(5) SQL audit; (6) file artefact.

## DECISION (phased): Phase-1 = IN-MEMORY result + structured health telemetry ONLY. NO publication.
- Proposal PUBLICATION requires its own publication-contract WO (schema/TTL/freshness/consumer governance).
- Persistence (SQL/file) requires its own governance decision.
Rationale: prove invocation + health safely before any surface is published; a published plan is higher-risk (staleness,
accidental consumption) and must be governed separately.

## Non-negotiable statements
- A proposal IS NOT an execution request.
- A published proposal MUST NOT be consumed automatically by an executor.
- Any publication key requires schema, TTL, freshness and consumer governance.
- No executor may infer authorisation from proposal existence. (ADR-0007.)
