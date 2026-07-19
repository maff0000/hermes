# Changed-file inventory (grouped)

Base `a780a16182c3035e1161ac396092e203aa0a5eac`.

## Production tooling (new)
- `tools/hermes_clean_build_context_v1.py` — §6 clean exact-SHA build-context tool + §12 security scan
  + §13 manifest + `.dockerignore` evaluator (F-DR-01).
- `tools/hermes_image_label_verify_v1.py` — §8 OCI provenance-label verification (F-DR-02).

## Build context / image (modified)
- `.dockerignore` — §7 hardening (independent defence).
- `Dockerfile` — §8 runtime-stage `ARG SOURCE_SHA` + `ARG BUILD_UTC` + two OCI `LABEL`s ONLY.

## Schema (new)
- `schemas/deployment_readiness/build_context_manifest.v1.schema.json` — §13 manifest contract.

## Design prose + models (modified / new)
- `docs/design/deployment_readiness/architecture_v1.md` — §7/§7A/§14/§19 prose corrections (labels +
  clean context MANDATORY; legacy-only scope for "not blocking"; F-DR mapping; safety-gate section).
- `docs/design/deployment_readiness/models/acceptance_matrix.v1.json` — control ids + new implemented
  gates (T-CLEAN-CONTEXT, T-BUILD-MANIFEST, T-SECRET-SCAN, T-PROHIBITED-PATH, T-OCI-LABELS,
  T-SOCKET-AMBIGUITY, T-HISTORICAL-INFERENCE).
- `docs/design/deployment_readiness/models/deployment_stage_model.v1.json` — Stage-B mandatory blocking
  controls + invariants.
- `docs/design/deployment_readiness/models/future_wo_sequence.v1.json` — FW-08/FW-19 control ids.
- `docs/design/deployment_readiness/models/control_identifier_mapping.v1.json` — NEW canonical single
  control-identifier mapping (F-DR-01 / F-DR-02).

## Tests (new)
- `tests/test_pre_stage_b_build_corrections_v1.py` — all §14 categories (49 tests).

## Evidence (new)
- `ops/evidence/WO-HELM-HERMES-PRE-STAGE-B-BUILD-CONTEXT-OCI-PROVENANCE-AND-SAFETY-GATE-CORRECTIONS-IMPLEMENTATION-0001/`
