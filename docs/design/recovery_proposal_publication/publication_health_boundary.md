# Publication-Health Boundary (§19)

R2D2 finding: the planner's exact `held_count` is process-local and not externally auditable. **This WO does not solve
planner health.** It scopes only the minimum *publication-component* health, and keeps four concerns separate:

| Concern | Owner | This WO |
|---|---|---|
| Proposal contract (K1/K2) | recovery publisher | designed here |
| Publication-component health | recovery publisher | minimum fields defined here; separate key DEFERRED |
| Planner health / held counters | recovery planner | **DEFERRED** to a future planner-health contract WO |
| Executor state | future executor | **DEFERRED**, separate authorisation |

Minimum publication-component health needed (to prove the publisher is alive and honest), carried in the K2 status record
(no separate health key at the dark stage): `publisher_enabled`, `last_validation_utc`, `last_publication_utc`,
`last_refusal_code`, `current_proposal_id`, `current_generation`, `expires_at_utc`, `component_fault_code`. A dedicated
`hermes:recovery_proposal:health:XAU_USD:v1` key is added ONLY if a consumer contract later requires publisher liveness
separately from the status record — decided in the publisher implementation WO, not here. Planner held-counters are explicitly
NOT combined into this contract merely for convenience.
