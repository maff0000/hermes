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
fields.**

**C-PR122-EAR-PROMOTION.** Public construction (`new_external_authority_readiness`) is **synthetic-only**: a
caller-selected non-synthetic classification is **rejected** with `EARPromotionForbidden` (not silently
repaired). The module seal is **integrity metadata for synthetic contract objects only** — it is **NOT a
production trust root**: not external authority, not a trust anchor, not a production issuer, not HSM/KMS
evidence, and not permission to enter real mode. **A valid module seal does not prove external authority** and
does **not** by itself prevent synthetic-to-real promotion.

**C-PR122-EAR-AVAILABILITY-SOLE-ROOT (layered real-mode gate).** Availability is **necessary but NOT
sufficient**. Real readiness requires, *in addition to* `production_external_authority_available() == True`, a
successful **MODULE-CONTROLLED production verification result** (`ProductionAuthorityVerificationResult`) that
is **externally rooted**, **bound to the exact readiness record**, and obtained through the module-controlled
verifier boundary `verify_external_authority_readiness(readiness)` — which takes **no caller verifier and no
caller result**. A caller can **neither mint nor supply** that result. **No production verifier is currently
configured** (`production_verifier_configured() == False`), so the verification operation fails closed with
`EAR-PRODUCTION-VERIFICATION-UNAVAILABLE`; **real-mode validation therefore remains fail-closed even when
`production_external_authority_available` is monkeypatched `True`.** Ordering:
`validate_authority_readiness(real_mode=True)` returns `('EAR-PRODUCTION-AUTHORITY-UNAVAILABLE',)` while
availability is False (necessity), and `('EAR-PRODUCTION-VERIFICATION-UNAVAILABLE',)` once availability is
forced True but no module-boundary verification result exists (insufficiency). A freshly-minted,
*correctly-sealed* `REAL` record (via a reflective or `object.__new__` path) still fails — on the **missing
external verification**, **not** on the seal. **Readiness cannot be established without externally-rooted
verification; the local seal is not that root.** Stage 4 must later integrate the actual external
verifier/trust anchor and bind `ProductionExternalAuthorityVerifier` to it; **this WO does not perform that
integration.**

Readiness states: `AUTHORITY_READY`, `AUTHORITY_UNAVAILABLE`, `AUTHORITY_REVOKED`, `AUTHORITY_INVALID`.
Authority classes: `SYNTHETIC_TEST_AUTHORITY_READINESS`, `GOVERNED_EXTERNAL_AUTHORITY_READINESS`,
`REVOKED_AUTHORITY_READINESS`, `UNTRUSTED_AUTHORITY_READINESS`.

### (B) Validator — `validate_authority_readiness`

Reject-not-repair, fail-closed, returns `()` iff valid. Rejects wrong type, digest tamper, seal
forgery/relabel, synthetic-in-real-mode, bad state/class, revoked/invalid, missing references, non-UTC,
expiry, unsupported contract version, wrong lifecycle stage, missing provenance, a reference that is actually
an inline payload (`EAR-REFERENCE-IS-INLINE-PAYLOAD`), and caller-supplied signing material
(`EAR-CALLER-SIGNING-MATERIAL`). A relabel of `synthetic_or_real_classification` to `"REAL"` breaks the seal
(`EAR-SEAL-INVALID`) because the seal binds the classification. In **real mode** it additionally returns
`EAR-PRODUCTION-AUTHORITY-UNAVAILABLE` (availability False) or `EAR-PRODUCTION-VERIFICATION-UNAVAILABLE` (no
module-boundary verification result) — see C-PR122-EAR-AVAILABILITY-SOLE-ROOT above.

### (B2) Production verification result — `ProductionAuthorityVerificationResult` / `validate_production_verification_result`

A record-bound production verification result obtained **only** through the module-controlled boundary — the
FUTURE (Stage-4) externally-rooted evidence, **unavailable** in this inert WO. A
`ProductionAuthorityVerificationResult` (frozen dataclass) binds contract_version, lifecycle_stage,
verification_mode, `readiness_record_digest`, external_authority/trust_anchor/issuer/attestation_policy
references, evidence_references, verification/validity UTC, verifier_identity_reference, verification_outcome,
provenance, fault_code, synthetic/real classification, `result_digest`, and a MODULE-issued `result_receipt`
HMAC. **References + status only — no private keys, signed payloads, credentials, endpoints, or real
public-key material.**

**C-PR122-EAR-VR-RECEIPT-REFLECTIVELY-FORGEABLE.** The `result_receipt` HMAC is **local object-integrity
metadata only** (synthetic contract support). It is **not** externally rooted, **not** external proof, **not**
production attestation, **not** sufficient verification, and **never** the external trust root — a
reflectively-reachable in-process key can mint a valid receipt, so a valid local receipt must **never by
itself** cause acceptance. Accordingly, `validate_production_verification_result` **independently** requires,
for any REAL/production result and **before** the receipt could cause acceptance: (1)
`production_verifier_configured() == True` (else `EAR-VR-PRODUCTION-VERIFIER-NOT-CONFIGURED`), and (2) the
result's `verifier_identity_reference` matches the module-controlled `configured_production_verifier_identity()`
(a **non-Boolean** anchor, so the config Boolean cannot become the new sole root — `None` here yields
`EAR-VR-VERIFIER-IDENTITY-NOT-CONFIGURED`; any other value yields `EAR-VR-VERIFIER-IDENTITY-MISMATCH`). Both
are unconfigured (`False` / `None`), so a reflectively-forged, correctly-receipted `REAL` result is rejected in
the standalone validator **and** on the top-level route even when both Booleans and
`verify_external_authority_readiness` are monkeypatched. A caller cannot supply the configured verifier
identity. **Stage 4** must replace the inert `production_verifier_configured()` /
`configured_production_verifier_identity()` with a governed, externally-rooted verifier identity +
configuration contract (no host values or secrets here); **this WO does not.**

`verify_external_authority_readiness(readiness)` is the module-controlled operation (**no caller verifier / no
caller result**); with `production_verifier_configured() == False` it returns `(None,
('EAR-PRODUCTION-VERIFICATION-UNAVAILABLE',))`. `new_synthetic_test_verification_result` is module-token-gated
and **SYNTHETIC-only** (a caller cannot mint a `REAL` result).
`validate_production_verification_result(result, readiness, now_utc)` returns `()` iff the result is a genuine,
current, non-synthetic, successful, record-bound production verification produced by the configured verifier,
else sorted `EAR-VR-*` codes: `EAR-VR-PRODUCTION-VERIFIER-NOT-CONFIGURED`,
`EAR-VR-VERIFIER-IDENTITY-NOT-CONFIGURED`, `EAR-VR-VERIFIER-IDENTITY-MISMATCH`,
`EAR-VR-WRONG-TYPE`, `EAR-VR-RECEIPT-INVALID`, `EAR-VR-DIGEST-TAMPER`, `EAR-VR-SYNTHETIC`,
`EAR-VR-NOT-VERIFIED`, `EAR-VR-RECORD-MISMATCH`, `EAR-VR-AUTHORITY-MISMATCH`, `EAR-VR-TRUST-ANCHOR-MISMATCH`,
`EAR-VR-ISSUER-MISMATCH`, `EAR-VR-POLICY-MISMATCH`, `EAR-VR-EVIDENCE-MISMATCH`, `EAR-VR-CONTRACT-VERSION`,
`EAR-VR-STAGE-MISMATCH`, `EAR-VR-UTC-INVALID`, `EAR-VR-EXPIRED`, `EAR-VR-REVOKED`.

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
