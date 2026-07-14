# Caller Options Matrix (DESIGN-ONLY)

| # | Model | Coupling | Failure domain | Dup-invoke risk | Startup | Cadence ctrl | Observability | Container | Plan-only? | Blocks HERMES publishers? | Verdict |
|---|-------|----------|----------------|-----------------|---------|--------------|---------------|-----------|-----------|---------------------------|---------|
| 1 | Existing supervisor runner (piggyback on a data runner) | HIGH | shared with that surface | med | with host runner | inherits | mixed | in-container | yes | YES (shares thread/cadence) | reject |
| 2 | **Dedicated recovery_planner runner (own supervisor thread)** | LOW | isolated per-thread | low (mutex+digest) | after publishers | own interval | dedicated health | in-container | yes | NO (thread-isolated) | **RECOMMENDED** |
| 3 | Event-triggered after gaps refresh | LOW | isolated | low | needs event bus | event | good | in-container | yes | NO | reject: no gaps event bus today |
| 4 | Timer-driven | LOW | isolated | med (recompute) | timer | fixed | good | in-container | yes | NO | subsumed by #2 + digest idempotency |
| 5 | Control-plane / manual | LOW | isolated | low | manual | manual | poor continuous | in-container | yes | NO | reject: no continuous health |
| 6 | Separate sidecar/process | LOW | fully isolated | low | separate | own | good | NEW container | yes | NO | reject: breaks single-container absorption + ops cost |

**Chosen: #2** — proven per-runner thread isolation (fault in one runner ≠ others down), in-container Docker absorption,
digest-gated cadence, dedicated health, component-level gate failure (see gate_failure_domain.md). Import MUST be
`utils.hermes_recovery_planner_v1` (fully qualified) — never the bare legacy `recovery_planner`.
