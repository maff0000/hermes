# FW-08 F2-R3-FUT-2 — Build + OCI-Inspection Producer Registration Contract (INERT)

**WO:** `WO-HELM-HERMES-FW08-F2-R3-FUT-2-BUILD-AND-OCI-INSPECTION-PRODUCER-REGISTRATION-CONTRACT-IMPLEMENTATION-0001`
**Owner:** HERMES (Helm) · **Contract version:** 1 · **Generated (UTC):** 2026-07-28
**Lifecycle stages:** 4 (build-producer registration) + 5 (OCI-inspection-producer registration)
**Module:** `design/hermes_fw08_producer_registration_v1.py`
**Tests:** `tests/test_fw08_producer_registration_v1.py`

## What FUT-2 is

FUT-2 is the **second future gate** of the merged FW-08 real-proof lifecycle. It defines the **inert**
contract for registering **two distinct producer roles** so that later (Stage-B) a **build producer** and an
**OCI-inspection producer** are **structurally separated**:

- a **build producer** (`BUILD_PRODUCER`, stage 4) emits **`BUILD_EVIDENCE` only**;
- an **OCI-inspection producer** (`OCI_INSPECTION_PRODUCER`, stage 5) emits **`OCI_INSPECTION_EVIDENCE` only**;
- the two are **execution-distinct** across all six F2-R3 dimensions (execution identity, registration, runner,
  executable boundary, evidence reference, provenance), keyed on **`executable_digest + authority_reference +
  runner_identity`**, not on tool/producer names.

## What FUT-2 is NOT

FUT-2 does **not** register a real producer, activate a registry, build or inspect an image, perform real OCI
inspection, produce real build proof, or touch runtime. It is **not** external-verifier integration (that is
**FUT-7 / stages 17-18**), **not** the evidence store (that is **FUT-3 / gate 6**), and **not** Stage-B. Real
producer registration is **UNAVAILABLE** and **fails closed**. No configuration values live in code — only
references. No secrets. UTC-only.

## The controls

### §4A Model — `ProducerRegistrationRecord`

A frozen dataclass binding the full field set (EV-04 build / EV-05 OCI): `contract_version`, `lifecycle_stage`,
`registration_id`, `producer_role`, producer/execution/executable identity references, `authority_root_reference`,
`approval_record_reference`, `activated_registry_handle_reference`, `permitted_evidence_class`,
`forbidden_evidence_classes`, `registration_utc`, `validity_end_utc`, `revocation_state`, `provenance`,
`synthetic_or_real_classification`, `status`, `fault_code`, `evidence_references`, `created_by`, a
`registration_digest` and a `registration_seal`. **References + status only** — no private/secret/endpoint
fields. The classification is set by the **module issuer**, never the caller. The digest binds every field
except itself + the seal; the seal binds `digest + classification`, so a relabel to `REAL` breaks the seal.
The token-gated `new_synthetic_producer_registration(**kw)` mints **SYNTHETIC-only** records; a non-synthetic
classification or a missing module token raises `ProducerRegistrationForbidden`.

### §4G Production unavailable — the decisive root (mirrors the FUT-1 final fix)

The **FUT-1 lesson** (PR#122 took three R2D2 corrections because acceptance kept rooting in a single primitive
— a caller classification, then a monkeypatchable Boolean, then a reflectively-forgeable local seal) is **baked
in from the start**:

- `_ModuleRegistrationIssuer` holds an **ephemeral, name-mangled HMAC key** (`secrets.token_bytes(32)`), no
  getter, never serialised. `_MODULE_ISSUER` is the singleton; `_REGISTRATION_TOKEN` is a module-private
  sentinel.
- `production_producer_registry_configured() -> False` — no fs/env/network lookup.
- `configured_registry_authority_identity() -> None` — a **NON-Boolean, module-controlled second anchor** so
  the Boolean can **never** be the sole trust root. A caller cannot supply it.
- `register_producer(mode="REAL_CANDIDATE", ...)` **always** returns
  `(None, ("PR-FUT2-REAL-REGISTRATION-UNAVAILABLE", "F2R3-FUT2-REAL-PRODUCER-REGISTRATION-NOT-CONFIGURED"))`.
  `TEST_ONLY` / `INERT_SIMULATION` require `test_token is _REGISTRATION_TOKEN` else
  `PR-FUT2-SYNTHETIC-REQUIRES-MODULE-TOKEN`; otherwise mint a `SYNTHETIC_UNREGISTERED` record sealed by
  `_MODULE_ISSUER`.

The **local seal is object-integrity metadata ONLY** — it is not a production trust root, not registry
authority, not external attestation, and never by itself grounds real-mode acceptance.

### §4B Role permissions — `validate_producer_registration`

Reject-not-repair, fail-closed, sorted reason tuple; `()` iff a valid synthetic record. The **INDEPENDENT
real-registration gate** runs **FIRST** (before any valid seal could cause acceptance): for `real_mode` OR a
real-classified/real-status record it requires `production_producer_registry_configured() == True` **and** the
record to resolve the module-controlled `configured_registry_authority_identity()` (None here →
`PR-FUT2-REGISTRY-AUTHORITY-NOT-CONFIGURED`; mismatch → `PR-FUT2-REGISTRY-AUTHORITY-MISMATCH`). It **appends** a
reason (guaranteeing rejection) so no real record ever validates — even when the Boolean is monkeypatched True.
Further rejects: wrong type, digest tamper, seal invalid, synthetic-in-real-mode, real-status-forbidden, bad
role, evidence-class mismatch, dual-role, evidence-class violation, evidence bound-elsewhere, inline-payload and
non-canonical references, missing references/provenance, non-UTC, expiry, revocation, contract version, wrong
stage, and authority-not-governed (real records only).

### §4C Distinctness — `evaluate_producer_registration_distinctness`

Derives distinctness (`()` iff genuinely distinct). Rejects same registration_id / producer / execution /
executable / authority reference (`PR-FUT2-SAME-*`), role mismatch, a registration claiming both roles
(`PR-FUT2-DUAL-ROLE`), and canonicalised identities that collapse to the same value (case/whitespace/unicode/
proxy alias → `PR-FUT2-ALIASED-IDENTITY`). It **delegates** to
`execution_identity.evaluate_execution_independence` and surfaces its `EI-*` reasons verbatim, so
**`EI-COMMON-EXECUTABLE-AND-AUTHORITY`** fires when the two share `executable_digest + authority + runner` —
**differing labels alone are not sufficient**.

### §4E Approval boundary — `validate_registration_approval`

Delegates `ApprovalRecord` digest recompute for tamper detection. Rejects producer self-approval (incl. alias)
`PR-FUT2-SELF-APPROVAL`, an approver sharing execution/executable/authority with the producer
`PR-FUT2-APPROVER-NOT-INDEPENDENT`, an approval bound to another producer/role/registration
`PR-FUT2-APPROVAL-BINDING`, and copied/relabelled/expired/revoked approvals.

### §4F Evidence-class separation — `validate_evidence_class`

Build producer output limited to `BUILD_EVIDENCE`; OCI to `OCI_INSPECTION_EVIDENCE`. Rejects build-emitting-OCI
/ OCI-emitting-build (`PR-FUT2-EVIDENCE-CLASS-VIOLATION`), an OCI producer **repackaging the build producer's
execution output** (`PR-FUT2-OCI-REPACKAGES-BUILD`), inline-payload and non-canonical class references.

### Evidence tail

`eventual_evidence_record_ids()` extends the D-PR120-EV-TAIL sequence with
`EV-04-BUILD-PRODUCER-REGISTRATION` and `EV-05-OCI-INSPECTION-PRODUCER-REGISTRATION`.

## Prohibited interpretations

- This contract does **not** mean a real producer is registered or a registry is activated.
- `SYNTHETIC_UNREGISTERED` minted here is test-only; it never certifies a real registration.
- The module seal is **not** registry authority; it is an ephemeral in-process integrity seal.
- No output authorises execution, publication, deployment, image build/inspection, or any runtime change.

Held stages: **6-22**. Real registration **fails closed**. The seal is **integrity-metadata-only**.
