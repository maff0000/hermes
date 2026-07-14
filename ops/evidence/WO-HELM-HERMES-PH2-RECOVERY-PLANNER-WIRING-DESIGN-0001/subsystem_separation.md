# Pre-existing Subsystem Separation (DESIGN-ONLY)

| Aspect | NEW PH2 planner | PRE-EXISTING subsystem |
|--------|-----------------|------------------------|
| Module(s) | utils/hermes_recovery_planner_v1.py | utils/recovery_planner.py + utils/recovery_executor.py |
| API | build_recovery_proposal / ProposedWorkload / evaluate_recovery_planner_gate | RecoveryLibrary / RecoveryPlanner / RebuildPlan / RebuildStep |
| Role | deterministic DRY-RUN proposal planner | pre-existing OANDA startup-backfill recovery library |
| Wiring | none (deployed dark) | referenced by main.py docstrings (startup backfill path) |

## Mandatory future wiring rules
- Use the fully-qualified import `utils.hermes_recovery_planner_v1`. NEVER the ambiguous bare `recovery_planner`.
- No shared classes; no shared executor; no fallback new->old; no automatic handoff; no shared gates; no shared Redis keys;
  no shared runtime status; no shared configuration; never invoke RecoveryLibrary; never invoke the pre-existing executor.

## Recommendation for the legacy subsystem
Later ISOLATE it into a legacy package (e.g. utils/legacy/) + DEPRECATE via a SEPARATE discovery/removal WO (assess live usage
by main.py first). DO NOT modify or remove it in this or the wiring WO. (ADR-0009.)
