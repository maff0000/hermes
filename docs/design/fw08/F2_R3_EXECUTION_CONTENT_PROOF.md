# FW-08 F2-R3 — Execution / Content Independence + Real-Image Proof (INERT)

**WO:** WO-HELM-HERMES-FW08-F2-R3-EXECUTION-CONTENT-INDEPENDENCE-AND-REAL-IMAGE-PROOF-IMPLEMENTATION-0001
**Owner:** HERMES (Helm). **Created (UTC):** 2026-07-27. **Contract version:** 1.

F2-R3 closes the remaining F-2 gaps *before* any real candidate-image proof. It builds NO real image, runs NO
docker/SBOM/scan, publishes/deploys/wires NOTHING, creates NO live key/secret/authority, and adds NO
third-party dependency (stdlib only). The single permitted non-determinism is the ephemeral in-process seal
key (`secrets.token_bytes(32)`) held by the module-owned proof issuer — mirroring F2-R1's ActivationAuthority /
`_ModuleVerifier`.

## The future invariant

```
REAL_IMAGE_PROOF => DISTINCT_EXECUTIONS
                  + CONTENT_RESOLVED_EVIDENCE
                  + GOVERNED_PRODUCER_AUTHORITY
                  + EXTERNALLY_VERIFIED_ACTIVE_IMPORT
```

**No real proof may be asserted from fixture data.** In this WO the real-image source is UNAVAILABLE, so real
proof ALWAYS fails closed.

## Controls

### §4/§5 Execution independence — `design/hermes_fw08_execution_identity_v1.py`
`ExecutionIdentity` binds the full execution identity (id / producer / process / runner / host-container / tool
/ executable+invocation digests / authority / evidence-output / attestation). `evaluate_execution_independence`
derives independence — there is NO `independent` boolean parameter. It rejects same execution id, same process
or same runner+host, copied/relabelled evidence, one execution emitting both roles, and (the §5 core) a common
executable + authority + runner. Independence is keyed on `executable_digest + authority_reference +
runner_identity`, **not** on tool or producer NAMES: a renamed tool or a relabelled producer id does not bypass.

### §6/§7 Content resolution + near-copy — `design/hermes_fw08_content_resolution_v1.py`
A reference is not content. `resolve_and_verify` resolves a reference through an IMMUTABLE storage boundary to
content whose digest must MATCH the independently-expected digest at the expected version, rejecting mutable
references, content mismatches and stale versions. The **production** resolver is unavailable (raises
`ContentResolverUnavailable`), so real-mode resolution fails closed; the synthetic resolver is module-token
gated (a caller cannot mint a trusted one). `detect_alias_or_nearcopy` catches near-copies BEYOND byte
equality.

Fields that MAY legitimately agree (same image): `image_id`, `candidate_id`, `source_sha`.
Fields that MUST come from independent OCI inspection: `manifest_digest`, `config_digest`, `filesystem_digest`,
`layer_digests`, and inspection metadata (`extraction_method`, `tool_identity`, `inspection_utc`). If ALL are
absent or merely restate build metadata, the "OCI" record is a dressed-up copy → `CR-NEAR-COPY-FROM-BUILD`.

### §8 Governed dual-role exception — `design/hermes_fw08_producer_independence_v1.py` (add-only)
`GovernedDualRoleException` models the full contract (distinct executions/refs/content-digests/approvals,
external authority-root binding, future expiry, revocation reference, audit reason, non-empty compensating
controls, no self-approval, no wildcard scope, self-verifying digest). It is **modelled, not activated**:
`governed_dual_role_active()` is `False`. The F2-R2 `DualRoleException` is untouched.

### §9 Legacy comparator containment — `design/hermes_fw08_legacy_comparator_containment_v1.py`
`validate_image_bound_active_import` stays **byte-identical** (14 fixture-comparison tests depend on it). §9 is
CONTAINMENT + VERIFICATION, not mutation: the comparator is declared NOT a readiness authority
(`comparator_is_readiness_authority()` is `False`), `assert_no_readiness_bypass` statically verifies the real
production wrapper routes readiness ONLY through the governed validators (never the raw comparator, no dynamic
route), and `guard_not_proof` refuses a verdict whose provenance is the raw comparator.
**Fixture-level comparison is preserved; authority is NOT preserved.**

### §10/§11/§12 Real-image proof — `design/hermes_fw08_real_image_proof_v1.py`
`RealImageProof` binds the full §10 field set. It can ONLY be minted by the module-owned `_ProofIssuer`
(ephemeral HMAC seal). `production_real_image_source_available()` is `False`, so `assemble_proof(REAL_CANDIDATE)`
ALWAYS returns `RIP-REAL-SOURCE-UNAVAILABLE` + `F2R3-REAL-PROOF-REQUIRES-REAL-IMAGE-AND-AUTHORITY` — no
fallback, no downgrade, no synthetic proof. Synthetic proof validates ONLY in TEST_ONLY/INERT_SIMULATION and is
mintable ONLY via `new_synthetic_test_proof` (which supplies the private `_PROOF_TOKEN`). The classification is
set by the issuer, never by the caller — the seal binds `proof_digest + classification`, so a relabelled
synthetic string breaks the seal. `verify_proof` enforces §11/§12 replay/substitution resistance: forged seal,
digest tamper, expiry, synthetic-in-real-mode, source/image/candidate-tag mismatch, substituted
execution/producer/SBOM/vuln, and revocation-at-use.

### §13 Stage-B wiring — `tools/hermes_stage_b_build_v1.py` (add-only, inert)
For `REAL_CANDIDATE`, when the runner exposes F2-R3 artifacts, the ordering now includes execution-independence
+ content-resolution + near-copy + real-image-proof assembly — all of which FAIL CLOSED
(`F2R3-REAL-PROOF-REQUIRES-REAL-IMAGE-AND-AUTHORITY`). When the runner supplies no F2-R3 artifacts (default
runner), the block is skipped exactly as before — byte-identical inert behaviour. TEST_ONLY/inert behaviour is
unchanged.

## Status
Real proof is **unavailable and fail-closed** in this WO. **No real proof is asserted.** Classification:
`F2_R3_INERT_EXECUTION_CONTENT_INDEPENDENCE_AND_REAL_IMAGE_PROOF_CONTRACT_IMPLEMENTED_REAL_PROOF_EXECUTION_OUTSTANDING`.
