# Rollout Sequence (DESIGN-ONLY) — 12 gated stages (do NOT collapse)

1.  Wiring implementation PR (code-only): caller module + gated runner, digest idempotency, in-memory output, component-level gate.
2.  R2D2 cold audit.
3.  HELM merge-only (head-match).
4.  HELM deploy-dark with gates UNSET (planner present, runner not appended).
5.  R2D2 deploy-dark audit.
6.  Controlled planner invocation with output DISCARDED / held in-memory (gates set enabled+authorised; NO publication).
7.  R2D2 invocation audit.
8.  SEPARATE proposal-publication contract WO (schema/TTL/consumer governance).
9.  Publication deploy-dark.
10. Publication activation audit.
11. SEPARATE executor DESIGN WO.
12. SEPARATE executor implementation + runtime gates + approval pipeline.

Each stage is independently governed and audited. No stage may be merged into another.
