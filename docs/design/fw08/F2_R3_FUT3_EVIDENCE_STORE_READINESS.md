# FW-08 F2-R3-FUT-3 — Immutable Evidence-Store + Resolver Readiness Contract (INERT)

**WO:** `WO-HELM-HERMES-FW08-F2-R3-FUT-3-IMMUTABLE-EVIDENCE-STORE-READINESS-CONTRACT-IMPLEMENTATION-0001`
**Owner:** HERMES (Helm) · **Contract version:** 1 · **Generated (UTC):** 2026-07-29
**Lifecycle stage:** 6 · **Gate:** 6 · **EV record:** `EV-06-EVIDENCE-STORE-AND-RESOLVER-READINESS`
**Module:** `design/hermes_fw08_evidence_store_readiness_v1.py`
**Tests:** `tests/test_fw08_evidence_store_readiness_v1.py`

## What FUT-3 is

FUT-3 is **Stage 6 = Gate 6** of the merged FW-08 real-proof lifecycle — the **third future gate**. It produces
an **EV-06** readiness record that **READIES the already-implemented §9 content-addressed immutable resolver**
(`design/hermes_fw08_content_resolution_v1.py`) and **BINDS** readiness to **EV-04** (build-producer
registration) and **EV-05** (OCI-inspection-producer registration) from FUT-2. It authorises **no store
mutation**. Storage is **technology-neutral** — no filesystem, SQL, Redis, Git, OCI registry, or object store is
selected, opened, or written.

## Consumes §9 — does NOT reimplement it

There is exactly **one** resolver in the codebase. This module **consumes** it and **delegates** every
content-resolution / immutability / alias / near-copy check to
`design/hermes_fw08_content_resolution_v1.py`, surfacing its `CR-*` codes **verbatim**:

- `require_content_resolver(resolver, real_mode=…)` → `CR-RESOLVER-MISSING` / `CR-RESOLVER-UNTRUSTED` /
  `CR-SYNTHETIC-IN-REAL-MODE`;
- `resolve_and_verify(reference, expected_digest, …)` → `CR-UNRESOLVED-REFERENCE` / `CR-MUTABLE-REFERENCE` /
  `CR-REFERENCE-CONTENT-MISMATCH` / `CR-CHANGED-CONTENT`;
- `detect_alias_or_nearcopy(…)` → `CR-ALIAS-SAME-CONTENT` / `CR-NEAR-COPY-FROM-BUILD` /
  `CR-FABRICATED-OCI-DIGEST` / `CR-OCI-FROM-ACTIVE-IMPORT` / `CR-EMBEDDED-EVIDENCE`.

The §9 `ProductionContentResolver` **always** raises `ContentResolverUnavailable`, so a **real resolve fails
closed** unconditionally.

## What FUT-3 is NOT

FUT-3 does **not** create a real evidence store, write any evidence object, perform any SQL/Redis/filesystem/
object-store write, open a socket, read a file, or touch runtime. It does **not** claim operational immutability,
real durability, live resolution, live comparison, retention/recovery, or operational store availability. Real
resolution is **unavailable** and **fails closed**. No configuration **values** live in code — only references /
policy tokens. No secrets. UTC-only. **Content-level separation is PREPARED, not operationally proven.**

## The controls

### §A Model — `EvidenceStoreReadinessRecord`

A frozen dataclass binding the full EV-06 field set: `contract_version`, `lifecycle_stage`(6), `fut`("FUT-3"),
`readiness_record_id`, `ev04_reference`, `ev05_reference`, `resolver_contract_reference`,
`resolver_identity_reference`, `store_policy_reference`, `digest_algorithm`("sha256"), `immutable_storage_id`,
`storage_version_scheme`, `supported_evidence_classes`, `canonical_serialisation_reference`,
`max_object_size_policy_reference`, `fail_closed_policy_reference`, `creation_utc`, `validity_end_utc`,
`revocation_state`, `synthetic_or_real_classification`, `readiness_status`, `fault_code`, `provenance`,
`evidence_references`, `created_by`, a `readiness_digest` and a `readiness_seal`. **References + policy tokens +
status only** — **no endpoint / bucket / credential / host-path / raw-key fields.** The digest reuses §9's
canonical-serialisation + sha256 scheme (no competing scheme). The seal binds `digest + classification`, so a
relabel to `REAL` breaks it. The token-gated `new_synthetic_readiness(**kw)` mints **SYNTHETIC-only** records; a
non-synthetic classification or a missing module token raises `EvidenceStoreReadinessForbidden`.

> **A frozen dataclass is NOT immutable storage.** In-process object-immutability does not prove that the
> backing store forbids update-in-place / delete / silent replacement. This record **readies** the §9 immutable
> resolver; it does not itself constitute a content-addressed immutable store.

### §G Production unavailable — the decisive root (mirrors FUT-1/FUT-2)

The **FUT-1 lesson** (PR#122 took three corrections; FUT-2 baked it in) is baked in here from the start:

- `_ModuleReadinessIssuer` holds an **ephemeral, name-mangled HMAC key** (`secrets.token_bytes(32)`), no getter,
  never serialised. `_MODULE_ISSUER` is the singleton; `_READINESS_TOKEN` is a module-private sentinel.
- `production_evidence_store_configured() -> False` — no fs/env/network/store lookup.
- `configured_store_authority_identity() -> None` — a **NON-Boolean, module-controlled second anchor** so the
  Boolean can **never** be the sole trust root. A caller cannot supply it.
- `ready_evidence_store(mode="REAL_CANDIDATE", …)` **always** returns
  `(None, ("ES-REAL-READINESS-UNAVAILABLE", "F2R3-FUT3-REAL-EVIDENCE-STORE-NOT-CONFIGURED"))`. `TEST_ONLY` /
  `INERT_SIMULATION` require `test_token is _READINESS_TOKEN` else `ES-SYNTHETIC-REQUIRES-MODULE-TOKEN`;
  otherwise mint a `SYNTHETIC_UNREADY` record sealed by `_MODULE_ISSUER`.

The **local seal is object-integrity metadata ONLY** — not a production trust root, not store authority, not
external attestation, and never by itself grounds real-mode acceptance.

### §B Resolver readiness — `validate_resolver_readiness`

**Delegates** to §9 `require_content_resolver` and surfaces its `CR-*` verbatim. There is **no** raw-dict /
local-file / caller-provided-evidence-and-resolver acceptance path (structurally impossible; asserted via tests).
Additionally requires `resolver_identity_reference` present + canonical, `digest_algorithm == "sha256"` (else
`ES-DIGEST-ALGORITHM-UNSUPPORTED`), and `immutable_storage_id` + `storage_version_scheme` present (else
`ES-MUTABLE-STORAGE-UNREADY`). The **independent real-readiness gate runs first**.

### §C Immutability — `validate_immutability`

Requires an `immutable_storage_id` + a resolved `storage_version`, verifies the resolved evidence's **own
self-digest recomputes** (`ES-DIGEST-MUTATED`), and **delegates** the resolution checks to §9
`resolve_and_verify` — surfacing `CR-MUTABLE-REFERENCE` / `CR-REFERENCE-CONTENT-MISMATCH` / `CR-CHANGED-CONTENT`.
Own codes: `ES-UPDATE-IN-PLACE-FORBIDDEN`, `ES-DELETE-FORBIDDEN`, `ES-STORAGE-ID-MISSING`,
`ES-STORAGE-VERSION-MISSING`, `ES-METADATA-MUTATED`, `ES-DIGEST-MUTATED`.

### §D Duplicate & alias policy — `evaluate_duplicate_policy`

Same canonical identity + different content → `ES-CONFLICTING-DUPLICATE`. **Delegates** alias / near-copy to §9
`detect_alias_or_nearcopy`, surfacing `CR-ALIAS-SAME-CONTENT` / `CR-MUTABLE-REFERENCE` / the near-copy codes
verbatim. **No silent canonicalisation / repair** of aliases.

### §E EV-04/EV-05 binding — `validate_upstream_binding`

Binds to the **exact** upstream EV-04 (build) + EV-05 (OCI) **`ProducerRegistrationRecord` objects** — a raw
string cannot assert a binding where the stronger object exists. **Delegates** each to
`producer_registration.validate_producer_registration` (surfacing `PR-FUT2-*` verbatim) and
`evaluate_producer_registration_distinctness`. Rejects missing (`ES-EV04-MISSING` / `ES-EV05-MISSING`),
lifecycle mismatch (`ES-UPSTREAM-LIFECYCLE-MISMATCH`), reference mismatch (`ES-EV04-REFERENCE-MISMATCH` /
`ES-EV05-REFERENCE-MISMATCH`), evidence-class mismatch (`ES-EVIDENCE-CLASS-MISMATCH`), shared registry authority
(`ES-REGISTRY-AUTHORITY-MISMATCH`), wrong resolver contract (`ES-RESOLVER-CONTRACT-MISMATCH`), copied upstream
reference (`ES-COPIED-UPSTREAM-REFERENCE`), expired/revoked upstream (`ES-UPSTREAM-EXPIRED` /
`ES-UPSTREAM-REVOKED`), and synthetic upstream in real mode (`ES-SYNTHETIC-UPSTREAM-IN-REAL-MODE`).

### §F Producer & evidence binding — `validate_producer_binding`

Preserves the producer-registration id / role / evidence class / storage binding; build vs OCI evidence **must
stay distinct** (delegates distinctness). Own codes `ES-PRODUCER-BINDING-BROKEN`, `ES-BUILD-OCI-NOT-DISTINCT`.
`content_level_separation_status()` returns `CONTENT_LEVEL_SEPARATION_PREPARED_NOT_OPERATIONALLY_PROVEN` — this
contract does **not** claim content-level independence operationally proven.

### §6 Evidence-reference boundary — `validate_evidence_reference`

A reference is a **pointer governed by §9, not evidence itself**. Rejects inline JSON / Base64 / PEM /
compressed / control-chars / path traversal / encoded traversal / local path / `file://` / shell fragment /
embedded credential / raw dict / digest-only-string-as-evidence / mutable query param / alias / non-canonical
encoding → `ES-REFERENCE-IS-INLINE-PAYLOAD` / `ES-REFERENCE-NONCANONICAL` / `ES-REFERENCE-MUTABLE-QUERY` /
`ES-REFERENCE-DIGEST-ONLY`. **Limitation:** it validates a well-formed pointer; it does **not** resolve the
reference to real content (that is §9's job, unavailable here).

### Main validator + evidence tail

`validate_evidence_store_readiness` composes §A/§B/§E/§F + the independent real-readiness gate + seal / digest /
type / UTC / version / stage / revocation / provenance / reference-presence checks; `()` iff a valid **SYNTHETIC**
EV-06 record. `eventual_evidence_record_ids()` extends the EV-04/EV-05 tail with
`{"evidence_store_readiness": "EV-06-EVIDENCE-STORE-AND-RESOLVER-READINESS"}`.

## Disclosed limitations

- **Opaque-reference:** §6 validates a well-formed pointer, not resolved content (§9 owns resolution; unavailable
  here).
- **Co-resident wholesale module replacement:** a hostile actor replacing the whole module (or the §9 module) is
  out of scope; supported + lightly-reflective paths fail closed (mirrors FUT-1/FUT-2).

## Prohibited interpretations

- This contract does **not** claim operational immutability, real durability, live resolution/comparison,
  retention/recovery, or operational store availability.
- `SYNTHETIC_UNREADY` minted here is test-only; it never certifies a real store or resolver.
- The module seal is **not** store authority; it is an ephemeral in-process integrity seal.
- No output authorises store mutation, evidence writes, execution, publication, deployment, or any runtime change.
- **Content-level separation is PREPARED, not operationally proven.**

Held gates: **7-8 (FUT-4) and beyond**. Real readiness **fails closed**. The seal is **integrity-metadata-only**.
Canonical **undeployed**; runtime **unchanged**.
