# ADR-0003: Existing-coverage completeness
Context: governed Redis history is a rolling retention window, not full history.
Decision: bound planning to the retention window; mark coverage RETENTION_BOUNDED; exclude beyond-retention as OUT_OF_RETENTION;
recommend a SEPARATE coverage-truth surface for beyond-retention completeness.
Alternatives: treat rolling history as complete (rejected: invents phantom gaps); SQL tables (not established as authoritative);
manifest summaries (insufficient).
Consequences: honest within-window planning; explicit refusal to overclaim.
Risks: within-window index gaps during writer outage -> surfaced as real gaps by the gaps surface, not masked.
Rollback: n/a (design).
Unresolved: authoritative beyond-retention store.
