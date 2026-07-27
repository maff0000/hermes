# FW-08 F2-R3 — Real-Image-Proof Controlled Lifecycle (DESIGN-ONLY)

**Classification:** `F2_R3_REAL_PROOF_EXECUTION_CONTROLLED_LIFECYCLE_DESIGN`
**Contract version:** 1. **Generated (UTC):** 2026-07-27T00:00:00+00:00.
**Canonical base:** `7c45d7a3ef73a1f60fe66d035d2ba7188be5ca66`.
**Machine-readable companion:** `docs/design/fw08/f2_r3_real_proof_lifecycle/f2_r3_real_proof_lifecycle.v1.json`.

## 1. Purpose (design-only; real proof unavailable)

The FW-08 F-2 chain is contract-complete at the **INERT** level: F2-R1 (external authority + module-owned
verifier), F2-R2 (build/OCI producer independence), F2-R3 (execution/content independence + real-image-proof
contract). **Real-image proof CANNOT be produced yet.** In
`design/hermes_fw08_real_image_proof_v1.py`, `production_real_image_source_available()` returns **False**, so
`assemble_proof(candidate_mode="REAL_CANDIDATE")` **always** fails closed with
`("F2R3-REAL-PROOF-REQUIRES-REAL-IMAGE-AND-AUTHORITY", "RIP-REAL-SOURCE-UNAVAILABLE")` — no fallback, no
downgrade, no synthetic substitution.

This document **designs (does NOT execute)** the future controlled, separately-gated lifecycle that could earn
**ONE** genuine `REAL_IMAGE_PROOF` against **ONE** real candidate image, and designs the closure of three
disclosed limitations: **L-F2R3-NEARCOPY**, **L-F2R3-BYPASS-STATIC**, **L-F2R3-REFLECTIVE**.

**Nothing here is built, inspected, signed, published, deployed, or activated.** No Docker runs. No real
authority is configured. No secret values appear anywhere (references only). No `/etc`, host-global, cron, or
another-app writes. All design flags are false except `design_only`.

## 2. The invariant

```
REAL_IMAGE_PROOF => DISTINCT_EXECUTIONS
                  + CONTENT_RESOLVED_EVIDENCE
                  + GOVERNED_PRODUCER_AUTHORITY
                  + EXTERNALLY_VERIFIED_ACTIVE_IMPORT
                  + EXTERNAL_ISSUER_ATTESTATION   (L-F2R3-REFLECTIVE closure)
```

No real proof may be asserted from fixture data. Classification is set by the module issuer, never by the
caller string; the ephemeral in-process seal binds `proof_digest + classification`, so a relabelled synthetic
breaks the seal (`RIP-SYNTHETIC-IN-REAL-MODE` in real mode).

## 3. Serial gate model

The lifecycle is **22 strictly serial gates**. Every gate has ONE owner, an explicit mutation authority, a
required input, produced evidence, a required approval, a success verdict, a **fail-closed** verdict, prohibited
side effects, a rollback/containment path, and the next gate. **No agent approves or audits its own mutation:**
HELM performs controlled execution only under Chief Architect authorisation; R2D2 performs an independent,
read-only audit of evidence it did not build; build and OCI producers each emit only their own evidence.

## 4. Twenty-two-stage walkthrough

| # | Stage | Owner | Mutation authority | Input | Evidence | Approval | Success verdict | Fail-closed verdict | Prohibited side effects | Rollback / containment | Next gate |
|---|-------|-------|--------------------|-------|----------|----------|-----------------|---------------------|-------------------------|------------------------|-----------|
| 1 | canonical-source freeze | Chief Architect | authorises freeze; no code/image mutation | CA authorisation naming the source SHA | EV-01 source-freeze record | CA authorisation | SOURCE_FROZEN | NO_AUTHORISATION -> HALT | no build/inspect/mutation | abandon; nothing created | candidate identity allocation |
| 2 | candidate identity allocation | Chief Architect | allocates isolated candidate id | EV-01 + isolation namespace | EV-02 candidate-identity record | CA | CANDIDATE_ALLOCATED | NAMESPACE_NOT_ISOLATED / TAG_REUSE -> HALT | no latest tag, no wiring, no restart | release id; namespace cleanup | external-authority readiness |
| 3 | external-authority readiness | HELM | configures governed external resolver (§6) | EV-02 + §6 (references) | EV-03 authority-readiness record | CA authorises; HELM configures | AUTHORITY_READY | AUTHORITY_UNAVAILABLE -> FAIL_CLOSED | no secrets, no /etc/host-global | tear down resolver -> fail-closed | build-producer registration |
| 4 | build-producer registration | HELM | registers build producer (build evidence only) | EV-03 + governed registry/handle | EV-04 build-producer registration | CA authorises; external approval | BUILD_PRODUCER_REGISTERED | SELF_APPROVED -> FAIL_CLOSED | build producer must not inspect/verify | revoke registration | OCI-inspection-producer registration |
| 5 | OCI-inspection-producer registration | HELM | registers execution-distinct OCI producer | EV-04 + distinctness matrix | EV-05 OCI-producer registration | CA authorises; external approval | OCI_PRODUCER_REGISTERED | PRODUCERS_NOT_DISTINCT -> FAIL_CLOSED (`EI-COMMON-EXECUTABLE-AND-AUTHORITY`) | OCI must not consume build payload as truth | revoke registration | evidence-store readiness |
| 6 | evidence-store readiness | HELM | readies immutable resolver (§9); NO store mutation | EV-05 + §9 | EV-06 evidence-store readiness | CA authorises; HELM readies | EVIDENCE_STORE_READY | STORE_MUTABLE / ALIASED -> FAIL_CLOSED (`CR-MUTABLE-REFERENCE`) | no store mutation, no alias | store read-only; quarantine | candidate-image build |
| 7 | candidate-image build | build producer | executes controlled build (build evidence only) | EV-01 + EV-04 + namespace | EV-07 build result | CA authorises; HELM supervises | IMAGE_BUILT | BUILD_FAILED / WRONG_SOURCE -> FAIL_CLOSED | no OCI inspection, no latest tag, no runtime touch | delete candidate image | immutable image digest capture |
| 8 | immutable image digest capture | build producer | captures immutable digest | EV-07 + candidate image | EV-08 immutable-digest record | under run authorisation | DIGEST_CAPTURED | DIGEST_UNSTABLE / MUTABLE_TAG -> FAIL_CLOSED | no post-capture mutation | discard image+digest | independent OCI inspection |
| 9 | independent OCI inspection | OCI producer | inspects by digest (inspection evidence only) | EV-08 digest ONLY | EV-09 OCI inspection | under run authorisation | OCI_INSPECTED | NEAR_COPY / FROM_BUILD -> FAIL_CLOSED (`CR-NEAR-COPY-FROM-BUILD`) | must not consume build payload as truth | discard inspection; quarantine | execution-independence validation |
| 10 | execution-independence validation | HELM | runs `evaluate_execution_independence` (derived, no boolean) | EV-07 + EV-09 identities | EV-10 execution-independence verdict | recorded; audited by R2D2 | EXECUTIONS_INDEPENDENT | NOT_INDEPENDENT -> FAIL_CLOSED (`EI-*`) | no independence boolean, no name bypass | halt; retain verdict | content-resolution validation |
| 11 | content-resolution validation | HELM | resolves refs via `resolve_and_verify`; no store mutation | EV-06 + EV-07/EV-09 refs | EV-11 content-resolution verdict | recorded; audited by R2D2 | CONTENT_RESOLVED | UNRESOLVED / MISMATCH -> FAIL_CLOSED (`CR-*`) | no synthetic resolver in real mode | halt; store untouched | semantic near-copy validation |
| 12 | semantic near-copy and derivation validation | HELM | runs `detect_alias_or_nearcopy` | EV-11 + EV-07 build_result + EV-09 oci_inspection | EV-12 near-copy verdict | recorded; audited by R2D2 | NOT_A_NEAR_COPY | NEAR_COPY -> FAIL_CLOSED (`CR-ALIAS-SAME-CONTENT`, `CR-NEAR-COPY-FROM-BUILD`) | one differing field NOT sufficient | halt; quarantine | SBOM generation |
| 13 | SBOM generation | OCI producer | generates SBOM by digest (inspection evidence only) | EV-08 + EV-09 | EV-13 SBOM record | recorded (not produced in this WO) | SBOM_GENERATED | SBOM_MISSING / SUBSTITUTED -> FAIL_CLOSED (`RIP-SUBSTITUTED-SBOM`) | no runtime mutation | discard SBOM | vulnerability scanning |
| 14 | vulnerability scanning | OCI producer | scans by digest (inspection evidence only) | EV-08 + EV-13 | EV-14 vulnerability result | recorded (not produced in this WO) | VULN_SCANNED | VULN_MISSING / SUBSTITUTED -> FAIL_CLOSED (`RIP-SUBSTITUTED-VULN`) | no runtime mutation | discard result | trust-anchor resolution |
| 15 | trust-anchor resolution | HELM | resolves lineage via governed resolver (§6) | EV-03 + lineage refs | EV-15 trust-anchor record | CA authorises; HELM resolves | TRUST_ANCHOR_RESOLVED | ANCHOR_UNAVAILABLE -> FAIL_CLOSED | no secrets, no /etc | tear down -> fail-closed | activated-registry validation |
| 16 | activated-registry validation | HELM | validates handle via F2-R1 `activate_registry` (sole minting path) | EV-04/EV-05 + EV-15 | EV-16 activated-registry record | recorded; audited by R2D2 | REGISTRY_ACTIVATED | HANDLE_FORGED -> FAIL_CLOSED (`AH-SEAL-INVALID`) | no caller-minted authority | revoke handle | active-import validation |
| 17 | active-import validation | HELM | validates via trusted handle (`validate_active_import_with_activated_handle`) | EV-16 + EV-09 | EV-17 active-import verdict | recorded; audited by R2D2 | ACTIVE_IMPORT_VALIDATED | COMPARATOR_PROVENANCE -> FAIL_CLOSED (`LC-COMPARATOR-NOT-PROOF-AUTHORITY`) | comparator never confers authority | halt; quarantine | real-proof assembly |
| 18 | real-proof assembly | HELM | invokes `assemble_proof`; issuer sets classification | EV-10..EV-17 | EV-18 candidate proof or reasons | recorded; proof is NOT publish/deploy authority | REAL_IMAGE_PROOF_ASSEMBLED (only when source available) | SOURCE_UNAVAILABLE -> FAIL_CLOSED (`RIP-REAL-SOURCE-UNAVAILABLE`) | no caller classification, no synthetic downgrade | no proof minted; quarantine | independent R2D2 proof audit |
| 19 | independent R2D2 proof audit | R2D2 | independent READ-ONLY audit; mutates/approves nothing | EV-01..EV-18 + `verify_proof` | EV-19 R2D2 audit verdict + blueprint | mandatory gate; no self-audit | AUDIT_GREEN (`verify_proof` -> (True, ())) | AUDIT_RED -> FAIL_CLOSED (`RIP-SEAL-INVALID`, `RIP-SUBSTITUTED-*`, `RIP-REVOKED-AT-USE`) | R2D2 never executes/mutates/approves own work | RED/AMBER halts; evidence read-only | publication decision |
| 20 | publication decision | Chief Architect | decides publication (a decision, not a mutation) | EV-19 GREEN | EV-20 publication decision record | CA explicit publication authorisation | PUBLICATION_AUTHORISED | NOT_AUTHORISED -> HALT | no registry publication unless separately authorised | un-publish if erroneous | deployment decision |
| 21 | deployment decision | Chief Architect | decides deployment (separate later action) | EV-20 + EV-19 | EV-21 deployment decision record | CA explicit deployment authorisation | DEPLOYMENT_AUTHORISED | NOT_AUTHORISED -> HALT | no deployed-image replacement, no restart, no protected-runtime touch | out of this lifecycle; runtime untouched | rollback or evidence quarantine |
| 22 | rollback or evidence quarantine | HELM | deterministic cleanup/quarantine of isolated namespace | EV-01..EV-21 + namespace inventory | EV-22 closure record | recorded; R2D2 may re-audit | RUN_CLOSED (runtime unchanged, restarts 0) | CLEANUP_INCOMPLETE -> quarantine + escalate | no protected-runtime touch, no evidence mount to live | namespace quarantined | terminal — R2D2 exact-head design audit |

## 5. Operator authority matrix

| Lane | May | May not |
|------|-----|---------|
| **Chief Architect** | sequence the lifecycle; authorise each gate; authorise publication + deployment; name source + candidate | perform execution, inspect, sign, deploy, audit; self-approve a mutation it also performs |
| **HELM** | perform controlled execution ONLY under CA authorisation (config, validation invocation, cleanup) | authorise itself; approve its own mutations; perform the independent audit; set proof classification |
| **R2D2** | perform independent READ-ONLY audit; issue the audit blueprint/verdict | execute; mutate evidence; audit evidence it built; self-audit |
| **build producer** | produce build evidence only (build, digest capture) | perform OCI inspection; verify proof; approve/audit its own evidence |
| **OCI producer** | produce inspection evidence only (inspect, SBOM, vuln) by digest | consume the build payload as truth; build the image; verify proof; approve/audit its own evidence |

**Build != OCI distinctness** is asserted across **six dimensions** — execution identity, registration, runner,
executable boundary, evidence reference, provenance — and is keyed on `executable_digest + authority_reference +
runner_identity`, **not** on tool/producer names (a renamed tool or relabelled producer does not bypass;
`evaluate_execution_independence` returns `EI-COMMON-EXECUTABLE-AND-AUTHORITY` / `EI-ONE-EXECUTION-BOTH-ROLES`).
**No self-approval, no self-audit.**

## 6. Real authority contract (§6)

- **authority_root:** governed external authority root (reference only) — `AuthorityRoot` / `validate_authority_root`.
- **resolver:** `ProductionAuthorityResolver` via `require_resolver`; no `/etc` or filesystem fallback.
- **verifier:** verifier-only application interface; no signing capability exposed to callers.
- **signing/attestation:** external boundary; non-exportable key or equivalent (see §12).
- **approval authority:** external; distinct/authorised/non-self/unexpired quorum >= threshold.
- **revocation source:** external `RevocationSet` (effective-time gated).
- **expiry / rotation:** future-dated artefacts; rotation does not re-mint historic proofs.
- **compromise response:** revoke externally -> `verify_proof` returns `RIP-REVOKED-AT-USE`; run halts.
- **authority_unavailable_behaviour:** `FAIL_CLOSED`.
- **no secret values in:** code, git, evidence, memory-fabric, design-doc.
- **config source:** `PROJECT_OWNED_OR_CONTAINER_ABSORBABLE_GOVERNED`; **no /etc or host-global.**

## 7. Build-producer contract (§7)

Produces build evidence only: candidate-image build, immutable digest, build execution identity,
`build_result`, artefact digests, return codes. Registered under a governed `ProducerRegistry` bound to a
trusted `ActivatedRegistryHandle`. **build_producer_must_not:** `perform_oci_inspection`, `verify_proof`.

## 8. OCI-producer contract (§8)

Produces inspection evidence only: independent OCI inspection by digest — `manifest_digest`, `config_digest`,
ordered `layer_digests`, `filesystem_digest`, image metadata, inspection tool output digest — plus SBOM and
vulnerability result. **oci_producer_must_not_consume_build_payload_as_truth: true;
must_inspect_image_independently_by_digest: true.** Execution identity is distinct from the build producer.

## 9. Evidence-store resolver contract (§9)

Immutable, content-addressed, versioned. `resolve_and_verify` enforces a digest match at the expected version
and rejects aliases (`CR-ALIAS-SAME-CONTENT`), mutable references (`CR-MUTABLE-REFERENCE`), changed content
(`CR-CHANGED-CONTENT`), unresolved references (`CR-UNRESOLVED-REFERENCE`), and reference/content mismatch
(`CR-REFERENCE-CONTENT-MISMATCH`). `require_content_resolver` gates real mode. **No store mutation in this WO.**

## 10. Limitation closure — L-F2R3-NEARCOPY (§10)

An "OCI" record can be a dressed-up copy of build metadata. Closure: OCI evidence is **rejected** unless **ALL**
mandatory independently-inspected values are present **and** derived from independent OCI inspection.

- **Mandatory independently-inspected values:** `manifest_digest`, `config_digest`, `ordered_layer_digests`,
  `filesystem_digest`, `image_metadata_from_oci`, `inspection_tool_output_digest`.
- **May match build (same image):** `image_id`, `candidate_id`, `source_sha`.
- **Must be independently derived:** the OCI-only set above.
- **Rule:** one differing field is **NOT** sufficient. Enforced by
  `design/hermes_fw08_content_resolution_v1.py::detect_alias_or_nearcopy`
  (`CR-NEAR-COPY-FROM-BUILD` / `CR-FABRICATED-OCI-DIGEST` / `CR-OCI-FROM-ACTIVE-IMPORT` / `CR-EMBEDDED-EVIDENCE`).

## 11. Limitation closure — L-F2R3-BYPASS-STATIC (§11)

Static assurance that readiness/proof authority cannot be reached via the raw legacy comparator or a side route.
**Approach:** `remove_public_authority_exports`, `capability_restricted_validator_interface`,
`runtime_call_path_enforcement`, `explicit_non_authority_result_types`, `package_export_tests`,
`call_site_inventory`, `dynamic_invocation_tests`, `cli_and_compat_route_exclusion`.

The legacy comparator `validate_image_bound_active_import` may **remain** for fixture comparison (byte-identical)
but **never confers readiness or proof authority**. Enforced by
`design/hermes_fw08_legacy_comparator_containment_v1.py`: `comparator_is_readiness_authority()` is `False`,
`assert_no_readiness_bypass` (`LC-WRAPPER-DIRECT-COMPARATOR` / `LC-CLI-BYPASS` / `LC-DYNAMIC-ROUTE`),
`guard_not_proof` (`LC-COMPARATOR-NOT-PROOF-AUTHORITY`).

## 12. Limitation closure — L-F2R3-REFLECTIVE (§12)

Today the proof seal is a module-owned ephemeral in-process HMAC (`_ProofIssuer`, `secrets.token_bytes(32)`) —
sufficient for INERT modelling but reflective: the application that mints could also forge. A real proof needs
an **external signing/attestation boundary** the application cannot forge:

- external signing/attestation boundary; **non-exportable key or equivalent** (HSM / KMS);
- **verifier-only application interface** (verify, never sign);
- issuer identity bound to the authority root; scoped signing policy (no wildcard);
- external revocation -> `verify_proof` -> `RIP-REVOKED-AT-USE`; issuer key rotation (historic proofs not re-minted);
- compromise response: revoke externally and halt; strict **test/live separation** — synthetic never validates
  in real mode (`RIP-SYNTHETIC-IN-REAL-MODE`).

**not_configured_or_created_in_this_wo: true.**

## 13. Governed dual-role policy (§13)

Default **DUAL_ROLE_FORBIDDEN**. `GovernedDualRoleException` is modelled, not activated
(`governed_dual_role_active()` is `False`). **Recommended disposition:
`PERMANENT_TOMBSTONE_UNLESS_A_CONCRETE_NEED_ARISES`.** If ever retained, the exception requires: distinct
executions/evidence-refs/content-digests/approvals, external authority-root binding, future expiry, revocation
reference, audit reason, non-empty compensating controls, no self-approval, no wildcard scope, self-verifying
digest. **activated_in_this_wo: false.**

## 14. SBOM + vulnerability contract (§14)

SBOM: `sbom_reference`, content digest, tool identity, UTC — bound to `immutable_image_digest`. Vulnerability:
`vulnerability_result_reference`, content digest, scanner identity, UTC — bound to `immutable_image_digest`.
Substitution resistance at use: `RIP-SUBSTITUTED-SBOM` / `RIP-SUBSTITUTED-VULN`. **produced_in_this_wo: false.**

## 15. Real-proof acceptance matrix (§15)

**Required inputs:** frozen `canonical_source_sha`, `immutable_image_digest`, distinct build+OCI execution
identities, content-resolved build+OCI evidence, independently-inspected OCI values (near-copy passed), SBOM
reference, vulnerability result reference, external trust-anchor lineage, trusted activated-registry handle,
externally-verified active import, external issuer attestation.

- **GREEN:** ALL inputs present/resolved/independent/attested; `assemble_proof` mints a `REAL_IMAGE_PROOF` and
  `verify_proof` returns `(True, ())` under the expected context; R2D2 independent audit GREEN.
- **AMBER:** a non-fatal gap or disclosure-suffices condition; run **HALTS** pending resolution.
- **RED:** any prerequisite missing/failed (`RIP-REAL-SOURCE-UNAVAILABLE`, `RIP-EXECUTIONS-NOT-INDEPENDENT`,
  `RIP-CONTENT-NOT-RESOLVED`, `RIP-NEAR-COPY`, `RIP-SEAL-INVALID`, `RIP-SYNTHETIC-IN-REAL-MODE`,
  `RIP-SUBSTITUTED-*`, `RIP-REVOKED-AT-USE`) -> FAIL_CLOSED.

**no_missing_prerequisite_may_downgrade_to_synthetic: true.**

## 16. Candidate isolation (§16)

Separate namespace; **no** deployed-image replacement; **no** latest tag; **no** runtime wiring; **no** restart;
**no** config install; **no** evidence mount into the live container; **no** operational-registry publication
unless separately authorised; deterministic cleanup/quarantine; **current runtime untouched** (source
`71ea3bd4`, image `c5fc2a62f424`, container `d80018037b7f`, restarts 0).

## 17. Evidence / audit sequence (§17)

Eleven evidence packages (EV-01 source-freeze; EV-02 candidate-identity; EV-03 authority-readiness;
EV-04/EV-05 producer-registrations; EV-06 evidence-store-readiness; EV-07/EV-08 build-and-digest; EV-09
OCI-inspection; EV-10..EV-12 independence/content/near-copy verdicts; EV-13/EV-14 SBOM+vuln;
EV-15..EV-17 trust-anchor/activated-registry/active-import; EV-18/EV-19 proof-assembly+R2D2-audit). Each
package records: UTC, source/image ids, execution ids, producer registrations, commands/invocation digests,
return codes, artefact digests, SHA256SUMS, operator identity, mutation status, and next gate. **no_secrets:
true** (proof digests only, never the seal key).

## 18. Future serial WO sequence (does NOT authorise execution)

An **ordered** sequence of future serial WOs, one per major gate cluster:

1. FW08-F2-R3-FUT-1 — external authority + issuer attestation configuration (Gate-2/3).
2. FW08-F2-R3-FUT-2 — distinct build+OCI producer registration (Gate-4/5).
3. FW08-F2-R3-FUT-3 — immutable evidence-store readiness (Gate-6).
4. FW08-F2-R3-FUT-4 — controlled candidate build + digest capture (Gate-7/8).
5. FW08-F2-R3-FUT-5 — independent OCI inspection + SBOM + vuln (Gate-8/9/13/14).
6. FW08-F2-R3-FUT-6 — bypass-static hardening (Gate-16).
7. FW08-F2-R3-FUT-7 — real-proof assembly + independent R2D2 audit (Gate-17/18).
8. FW08-F2-R3-FUT-8 — publication + deployment decisions (Gate-19/20/21).

**Every entry has `authorises_execution: false`.** The sequence itself does **NOT** authorise execution. Each
future WO requires **separate Chief Architect authorisation + independent R2D2 audit**; **no WO in this sequence
authorises real Stage-B execution.**

## 19. Carry-forward (§20)

**OUTSTANDING, not implemented, not widened:** F-1, F-3, broader F-4, FW-16, N-1, N-2, PR#108/FW-17, R2D2
state-key durability note, production performance proof.

## 20. Runtime Protection & Non-Authorisation

**Protected runtime is untouchable.** Source `71ea3bd4598d8af5628f78de10f8022088ad3f05`; image `c5fc2a62f424`;
container `d80018037b7f`; restarts 0; runners 9; config v3; `consumer_live=false`. Canonical base
`7c45d7a3ef73a1f60fe66d035d2ba7188be5ca66`.

This is a **design artifact only.** Nothing here is built, inspected, signed, deployed, or activated. No real
authority is configured (`authority_configured=false`); no image is built (`image_build_authorised=false`); no
execution is authorised (`real_execution_authorised=false`); no runtime is mutated (`runtime_mutation=false`);
no Redis or SQL is touched; no secrets are present. Real-image proof remains **unavailable and fail-closed**:
`production_real_image_source_available()` is False, so `assemble_proof(REAL_CANDIDATE)` always returns
`RIP-REAL-SOURCE-UNAVAILABLE` / `F2R3-REAL-PROOF-REQUIRES-REAL-IMAGE-AND-AUTHORITY`.

**Next gate:** independent R2D2 exact-head design audit; **no real Stage-B execution authorised.**
