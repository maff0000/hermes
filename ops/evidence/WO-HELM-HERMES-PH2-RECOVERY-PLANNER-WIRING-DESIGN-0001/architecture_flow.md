# Architecture / Data-Flow (DESIGN-ONLY)

```mermaid
flowchart TD
  subgraph HERMES_CONTAINER[HERMES container 0a7d567 — existing]
    SUP[HermesPublisherSupervisor] --> RG[gaps runner -> hermes:gaps:XAU_USD:v1]
    SUP --> RB[backfill_status runner -> hermes:backfill:status:XAU_USD:v1]
    SUP -. FUTURE, gated .-> RP[recovery_planner runner (plan-only)]
  end
  RG --> GK[(hermes:gaps:XAU_USD:v1 GET read-only)]
  HIST[(governed candle history index, RETENTION-BOUNDED, read-only)] --> COV[ExistingCoverageSnapshot]
  POL[[governed external RecoveryPlanningPolicy - no config in code]] --> RP
  CLOS[[HERMES regular schedule + governed exceptional-closure evidence]] --> RP
  GK --> RP
  COV --> RP
  RP -->|calls PURE| PURE[[utils.hermes_recovery_planner_v1.build_recovery_proposal]]
  PURE -->|ProposedWorkload DRY_RUN_ONLY| MEM[(in-memory result + health telemetry ONLY)]
  MEM -. SEPARATE publication-contract WO .-> PUB[(hermes:recovery:proposal:XAU_USD:v1 TTL — NOT in this design)]
  PUB -. SEPARATE approval + executor WO .-> EX[(executor — NOT in scope)]

  classDef future stroke-dasharray:5 5;
  class RP,PUB,EX future;
```

Read-only inputs (GET only): gaps key, governed history index (retention-bounded), external policy, closure evidence.
Only mutation ever contemplated downstream: a single TTL-bound proposal key — and ONLY via a separate publication-contract WO.
The pure planner never touches Redis. Legacy utils/recovery_planner.py + recovery_executor.py are OUTSIDE this flow.
