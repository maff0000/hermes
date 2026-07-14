# ADR-0007: Output disposition + proposal TTL
Context: how to dispose of a computed proposal safely.
Decision: Phase-1 IN-MEMORY + health telemetry ONLY; publication is a SEPARATE contract WO; candidate proposal key is TTL-bound
(positive TTL, fail-loud expiry ~2x cadence), not persistent.
Alternatives: persistent proposal key (rejected: stale plan danger); immediate publication (rejected: ungoverned consumption);
SQL/file (separate governance).
Consequences: safe staged rollout; no accidental consumption.
Risks: none in-memory. Rollback: n/a. Unresolved: publication transport + consumer contract.
