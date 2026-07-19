# FW-08 PR#114 FINAL pre-build trust-boundary corrections (R-1 / R-2 / R-3)

WO-HELM-HERMES-PR114-FW08-FINAL-PREBUILD-TRUST-BOUNDARY-CORRECTIONS-0001
Authority: HELM (HERMES market-data lane). UTC.

PR #114 OPEN / UNMERGED. Prior head `1ea6f20`. NO image built, NO docker/SBOM/vuln/publish/deploy, NO
merge/rebase/force-push, NO runtime/config/shadow/Redis/SQL change. stdlib only (no new third-party dep).

## R-1 (§6/§7/§8) — bare-Boolean readiness REMOVED
- `design/hermes_fw08_readiness_evaluator_v1.py` is a **fail-closed tombstone**: `ReadinessInputs`/
  `ReadinessVerdict` access and `evaluate(...)` raise `BareBooleanReadinessRemovedError`. No compatibility
  constructor recreates readiness from bools. The wrapper no longer imports/uses it.
- The **sole** readiness path is a validated, producer-trusted, **sealed** evidence chain:
  - `design/hermes_fw08_producer_trust_v1.py` — typed `ProducerTrustContract` + `ProducerTrustRegistry` +
    `GovernedEvidenceSealer` (unforgeable in-process HMAC seal with an ephemeral, never-persisted key).
  - `design/hermes_fw08_evidence_chain_v1.py` — Merkle-bound `EvidenceChainManifest` +
    `validate_evidence_chain` (rejects reorder/missing/dup/append/removed/tamper/binding-mismatch).
- **Checksum proves integrity, not authority.** `test_r1_public_caller_cannot_fabricate_trusted_readiness`
  proves three distinct forgery attacks (random seal, own-key seal, unknown producer) all fail closed.

## R-2 (§9/§10/§11) — immutable real-build context (Option A)
- `design/hermes_fw08_immutable_context_v1.py` — private project-local snapshot -> verify -> finalise
  (chmod read-only + verify owner/mode) -> **atomic single-consumer** acquire (`BUILD_IN_USE`) -> release ->
  destroy (cleanup-failure fails closed). Lifecycle: QUARANTINED/VERIFIED/FINALISED/BUILD_IN_USE/RELEASED/
  DESTROYED/REJECTED.
- The runner receives ONLY the finalised context identity. Because CI may run as root (read-only bits do not
  block root), immutability is enforced by BOTH chmod AND cryptographic revalidation: every mutation
  (write/replace/add/remove/mode/owner/symlink) invalidates before success.
- Wired into `run_stage_b_candidate_build`: the immutable snapshot is finalised + acquired before the (fake)
  build; `assert_intact()` re-checked after consumption; destroyed after evidence capture.

## R-3 (§12/§13/§14) — image-bound active-import evidence
- `design/hermes_fw08_active_import_evidence_v1.py::validate_image_bound_active_import` binds evidence to the
  ACTUAL candidate image: `image_id` + `source_sha` + `image_filesystem_digest` mandatory, trusted producer,
  checksummed import graph + module inventory (recomputed — rejects tamper), admissible non-self-attested
  method, freshness. A `TEST_ONLY_DECLARATION` is not candidate proof. Every import/mismatch/tamper/stale
  case rejects.

## §15 wrapper flow / §17 canonical preflight / §21 no build
- Single readiness path scanned (`test_s15_wrapper_has_single_readiness_path_no_alternate`): one
  `CANDIDATE_READY` advance gated by `_build_trusted_chain`, one `runner.build`, immutable finalise+acquire
  present, no bare-Boolean evaluator.
- Canonical preflight vs EXACT `fe2037c5` -> **USABLE**, `runner_constructed=false`, `docker_invoked=false`,
  image/tag/sbom/vuln = null (see `19_...txt`).
- Default `RefusingDockerRunner` refuses all ops; no docker subprocess in-process (see `18_no_docker_proof.txt`).

## Results
- Focused (`test_fw08_governed_build_v1` + `test_fw08_corrections_v1` + `test_fw08_final_trust_v1`): **211 passed**.
- PR#113 regression (`test_pre_stage_b_build_corrections_v1`): **49 passed**.
- Full suite: 51 failed / 4 errors PRE-EXISTING (redis/starlette/env infra), 2041 passed.
- **Node diff vs baseline: EMPTY** (`17_node_diff.txt`) — no regression, no prior test weakened.

Doc/model: `schemas/deployment_readiness/fw08_final_trust.v1.json`.
