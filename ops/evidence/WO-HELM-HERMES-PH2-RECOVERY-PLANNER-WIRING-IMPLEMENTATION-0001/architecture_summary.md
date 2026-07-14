# HERMES PH2 Recovery-Planner PLAN-ONLY Runtime Wiring — Implementation Architecture
WO-HELM-HERMES-PH2-RECOVERY-PLANNER-WIRING-IMPLEMENTATION-0001 · base cdc1e58 · CODE-ONLY

> This wiring invokes a deterministic planner only. It does not publish, authorise or execute recovery.

## Source file map
- `utils/hermes_recovery_planner_runtime_v1.py` — the plan-only integration layer (NEW).
- `utils/hermes_publisher_runtime_v1.py` — one additive block in `default_runner_specs()`: append `recovery_planner`
  runner ONLY when `HERMES_RECOVERY_PLANNER_ENABLED` (non-raising env check).
- `utils/hermes_recovery_planner_v1.py` — the pure planner (UNCHANGED; imported fully-qualified).

## Components (thin; business logic stays in the pure planner)
- `evaluate_recovery_planner_component_gate` / `RecoveryPlannerGate105Error` — component-level 105 (typed, NOT SystemExit).
- `recovery_planner_append_enabled()` — env-only, non-raising append decision (ENABLED only).
- `load_policy_from_reader` (+`_FilePolicyReader`) — read-only mounted JSON at `/app/config/recovery_planner_policy.v1.json`;
  schema validate; map to `RecoveryPlanningPolicy`+`CostModel`; default-deny `POLICY_*` faults.
- `GapsAdapter` — GET-only `hermes:gaps:XAU_USD:v1` -> `GapSurfaceSnapshot`; freshness (<=policy staleness, default 120s).
- `CoverageAdapter` — read-only, RETENTION-BOUNDED (M1-H4 35d, D1 120d) `ExistingCoverageSnapshot`; beyond-retention excluded.
- `build_regular_closures` + `detect_unresolved_exceptional` — Option A: HERMES weekend only; suspected exceptional -> block.
- `acquire_consistent_snapshot` — pre/post gaps digest consistency; bounded retry; `BLOCKED_INPUT_INCONSISTENCY`.
- `semantic_digest` — idempotency tuple (gaps/coverage/closure/policy digests + planner_version); volatile-ts invariant.
- `InMemoryProposalHolder` — process-local only; exec flags validated false; not trusted across restart.
- `RecoveryPlannerRunner` / `recovery_planner_step` — orchestrates gate -> policy -> snapshot -> digest dedup -> pure plan
  -> in-memory hold -> structured summary log + component state.

## Gate truth table (component-level)
F/F -> DISABLED (no reads) · F/T -> DISABLED · T/F -> GATE_FAILCLOSED_105 (typed, contained; critical runners survive;
never SystemExit / whole-container) · T/T -> PLAN_ONLY_ENABLED.

## Disabled deploy-dark behaviour (unambiguous)
Gates unset -> runner NOT appended -> zero policy reads, zero gaps/coverage reads, zero planner invocations, zero proposals,
zero Redis writes, zero faults, no effect on critical runners.

## Future controlled invocation (no code change required)
Install the governed policy file at the canonical path + set the two gates + restart/reload the HERMES component + observe
structured logs and supervisor state to confirm an in-memory proposal. No publisher, no health key, no SQL, no vendor, no
executor, no Falcon required.

## Non-goals
No proposal/health publication; no SQL/vendor/file write; no D1 seed/backfill; no executor; no legacy `recovery_planner.py`/
`recovery_executor.py` bridge; no Falcon/ARES/HELIOS/NEO/SOLO; no Compose/gate/runtime change in this WO.
