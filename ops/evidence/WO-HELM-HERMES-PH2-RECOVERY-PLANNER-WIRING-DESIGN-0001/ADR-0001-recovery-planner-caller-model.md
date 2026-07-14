# ADR-0001: Recovery-planner runtime caller model
Context: the deployed-dark PH2 planner needs a future runtime caller without endangering the market-data spine.
Decision: a DEDICATED `recovery_planner` runner inside the existing HermesPublisherSupervisor, plan-only, digest-gated,
component-isolated failure, in-container. Import strictly `utils.hermes_recovery_planner_v1`.
Alternatives: piggyback existing runner (rejected: coupling/blocking); event-only (no gaps event bus); timer (subsumed);
manual (no continuous health); sidecar (breaks single-container absorption).
Consequences: reuses proven per-thread isolation; dedicated health; slight new runner surface.
Risks: a badly written runner could still hog CPU -> mitigated by digest short-circuit + workload bounds.
Rollback: do not append the runner (gates unset) — planner stays dark.
Unresolved: final module name for the caller; readiness-wait tuning.
