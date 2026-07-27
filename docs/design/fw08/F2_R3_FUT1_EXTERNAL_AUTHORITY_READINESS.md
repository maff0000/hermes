# FW-08 F2-R3-FUT-1 — External-Authority + Issuer-Attestation Readiness Contract (Stage 3, INERT)

**WO:** WO-HELM-HERMES-FW08-F2-R3-FUT-1-EXTERNAL-AUTHORITY-AND-ISSUER-ATTESTATION-READINESS-CONTRACT-IMPLEMENTATION-0001
**Owner:** HERMES (Helm) · **Contract version:** 1 · **Generated (UTC):** 2026-07-27T00:00:00+00:00
**Lifecycle stage:** 3 (external-authority + issuer-attestation readiness) — the first FUTURE gate (FUT-1).

---

## What this is

Stage 3 of the merged FW-08 real-proof lifecycle. It defines **HOW HERMES will LATER reference and verify a
governed external authority + issuer-attestation boundary** WITHOUT the HERMES app, a caller, or any
co-resident Python code ever possessing or inventing production signing authority. It closes the
`L-F2R3-REFLECTIVE` limitation **only at the contract boundary**.

Design module: `design/hermes_fw08_external_authority_readiness_v1.py` (PURE, INERT, stdlib-only, not imported
by runtime).

## What this is NOT

- **No real HSM / KMS / external signer / production isolation exists or is claimed.** Real external authority
  is UNAVAILABLE and FAILS CLOSED.
- **No keys, secrets, credentials, or signing material** are created, stored, or embedded anywhere.
- **No signing / issuance / minting capability** is exposed on any interface.
- **No runtime is touched**; no I/O, subprocess, network, filesystem, or environment lookup.

---

## Controls

### (A) Readiness model — `ExternalAuthorityReadiness`

Frozen dataclass, the EV-03 authority-readiness record. Binds ALL of: contract_version, lifecycle_stage (=3),
readiness_state, authority_class, external_authority_reference, trust_anchor_reference, resolver_reference,
issuer_identity_reference, attestation_policy_reference, evidence_references, issued_or_observed_utc,
validity_end_utc (expiry/rotation), provenance, fault_code, fail_closed_reason,
synthetic_or_real_classification, readiness_digest, seal. **References only — no signing/private material
fields.** `synthetic_or_real_classification` is set by the MODULE issuer, never the caller.

Readiness states: `AUTHORITY_READY`, `AUTHORITY_UNAVAILABLE`, `AUTHORITY_REVOKED`, `AUTHORITY_INVALID`.
Authority classes: `SYNTHETIC_TEST_AUTHORITY_READINESS`, `GOVERNED_EXTERNAL_AUTHORITY_READINESS`,
`REVOKED_AUTHORITY_READINESS`, `UNTRUSTED_AUTHORITY_READINESS`.

### (B) Validator — `validate_authority_readiness`

Reject-not-repair, fail-closed, returns `()` iff valid. Rejects wrong type, digest tamper, seal
forgery/relabel, synthetic-in-real-mode, bad state/class, revoked/invalid, missing references, non-UTC,
expiry, unsupported contract version, wrong lifecycle stage, missing provenance, a reference that is actually
an inline payload (`EAR-REFERENCE-IS-INLINE-PAYLOAD`), and caller-supplied signing material
(`EAR-CALLER-SIGNING-MATERIAL`). A relabel of `synthetic_or_real_classification` to `"REAL"` breaks the seal
(`EAR-SEAL-INVALID`) because the seal binds the classification.

### (C) Verifier-only boundary — `ExternalAuthorityVerifier`

An ABC that exposes ONLY `verify_authority_evidence(readiness, evidence_reference, now_utc) -> (bool,
reasons)`. It has **no** `sign` / `issue` / `mint` / `generate_key` / `import_key` / `export_key` /
`rotate_key` / `approve_self` / `self_register` method. `ProductionExternalAuthorityVerifier` raises
`ExternalAuthorityUnavailable` (no production verifier configured). `SyntheticExternalAuthorityVerifier` is
module-token gated and deterministic (for tests).

- `require_verifier_only` rejects any object exposing a forbidden capability
  (`EAR-VERIFIER-EXPOSES-SIGNING`) or not implementing the interface (`EAR-NOT-VERIFIER-INTERFACE`).
- `select_production_verifier` obtains the production verifier from the **module boundary, not the caller**;
  here it returns `(None, ('EAR-PRODUCTION-VERIFIER-UNAVAILABLE',))` — production verification cannot proceed.
- `verify_readiness_no_caller_trust_loop` refuses any caller-supplied verifier in real mode
  (`EAR-CALLER-OWNED-TRUST-LOOP`): a caller can never supply BOTH the asserted authority AND its verifier. The
  verifier must come from the module/external boundary, which is unavailable → **fail closed**.

### (D) Issuer-attestation boundary — `IssuerAttestationReference`

Frozen dataclass: issuer_identity, authority_root_reference, attestation_evidence_reference,
evidence_location_reference, verifier_result, lifecycle_readiness, reference_digest. **No signed payload /
private material / mock credential embedded.** `validate_issuer_attestation_reference` rejects inline payload,
missing issuer / authority-root / attestation refs, digest tamper, and any self-issuance — a `verifier_result`
asserting `SIGNED_BY_HERMES` / self-issuance is refused (`EAR-IA-SELF-ISSUED`).

### (E) Production unavailable

`production_external_authority_available()` is **False** (no filesystem/env/network lookup). In `REAL_CANDIDATE`
mode, `resolve_authority_readiness` **ALWAYS** returns
`(None, ('EAR-REAL-AUTHORITY-UNAVAILABLE', 'F2R3-FUT1-REAL-EXTERNAL-AUTHORITY-NOT-CONFIGURED'))` — no fallback,
no synthetic substitution. This mirrors F2-R1's `ResolverNotConfigured` / `require_resolver`. Synthetic
`AUTHORITY_READY` is mintable ONLY in `TEST_ONLY` / `INERT_SIMULATION`, sealed by the module issuer, and only
via the module helper `new_synthetic_test_readiness` (which supplies the private `_READINESS_TOKEN`). A caller
cannot mint it.

### (F) Evidence tail

`eventual_evidence_record_ids()` extends the D-PR120-EV-TAIL sequence with `EV-03-AUTHORITY-READINESS`.

---

## The reflective-key residual (honest note)

The module seal is an **ephemeral in-process HMAC** (`_ReadinessIssuer`, name-mangled key, no getter) — the
same pattern and the same residual posture as F2-R1's `_ModuleVerifier` and F2-R3's `_ProofIssuer`. It proves
the CONTRACT boundary (a caller cannot forge/relabel a record) but it is **NOT a real external signer**. Real
external authority — an HSM/KMS/external signer rooted outside the HERMES app — is UNAVAILABLE and outstanding
for stage 4+. Nothing here creates it, claims it, or authorises any runtime change.

**Real external authority is unavailable and fails closed. No real HSM / KMS / signing exists.**
**Next held stage:** build-producer registration (stage 4).
