# ADR-0005: Gate failure domain
Context: enabled-without-authorised must fail closed (105) without crashing the market-data spine.
Decision: COMPONENT-level fail-closed — evaluate the gate inside the planner runner; 105 becomes a binding planner fault
(GATE_FAILCLOSED_105, no runner, health RED, log ERROR); supervisor + critical runners keep running.
Alternatives: whole-container SystemExit(105) at boot (rejected for an advisory feature: disproportionate blast radius).
Consequences: 105 remains visible+binding at component level; deliberate divergence from gaps/backfill surface pattern (justified).
Risks: an operator might miss a component fault -> mitigated by health RED + alert threshold.
Rollback: gates unset -> disabled. Unresolved: whether governance ever mandates whole-container fail-closed (needs explicit sign-off).
