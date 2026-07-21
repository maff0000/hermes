# FW-08 F2-R1-C — Module-Owned Trust Boundary (AMBER closure)

WO-HELM-HERMES-FW08-F2-R1-EXTERNAL-PRODUCER-REGISTRY-ROOT-OF-TRUST-IMPLEMENTATION-0001 (F2-R1-C).
Owner: HERMES (Helm). Created (UTC): 2026-07-21. Contract version: 1. Status: **INERT — no image, no runtime.**

## Why this correction exists

The F2-R1 build introduced an authority root / manifest / approval / revocation / resolver / activated-handle
set plus a hardened producer registry. An audit returned AMBER: the authority was still **caller-mintable in
REAL-candidate mode** —

| Finding | Defect |
|---------|--------|
| C1 | The resolver was caller-supplied; a caller-created `GOVERNED_EXTERNAL_RESOLVER` was accepted in real mode. |
| C2 | The caller supplied the SAME `activation_authority` used to both SEAL and VERIFY the handle — it owned both sides. |
| C3 | Approvers were checked only against `root.authorised_approver_identities` — the root named its own approvers. |
| C4 | Revocation was captured at handle creation but NOT rechecked at candidate-readiness USE. |
| C5 | The wrapper's raw-registry else-branch was not mechanically excluded from REAL_CANDIDATE mode. |

## The module-owned trust boundary

Real-candidate trust **cannot be supplied by the caller.** A new module owns the boundary:

- **Trust-anchor PROVIDER** (`hermes_fw08_trust_anchor_provider_v1`). The **production** provider is
  UNAVAILABLE (`production_provider_available()` is `False`) and performs **no** filesystem / `/etc` /
  environment lookup — so `REAL_CANDIDATE` can **never** activate/validate in this WO. A module-owned
  **synthetic** provider serves `TEST_ONLY` / `INERT_SIMULATION`; it is gated by a module-private
  `_MODULE_TOKEN` and is **not caller-substitutable**.
- **Module-owned VERIFIER** (`_ModuleVerifier`, ephemeral name-mangled HMAC key, mirroring
  `ActivationAuthority`) both **seals** and **verifies** handles for the trusted path. The caller never
  supplies the verifier — `validate_active_import_externally_rooted` has **no** `activation_authority`
  parameter (fixes **C2**).
- **Resolver anchoring** — `verify_resolver_identity` requires the resolver's exact identity **and** its
  content digest (sha256 over `inspect.getsource(type)` + module + qualname) to be listed in the anchor set.
  A subclass / proxy / copied-with-changed-metadata / id-collision / wrong-digest resolver fails (fixes **C1**).
- **Approver anchoring** — `verify_approver_anchored` requires every manifest approver to be in the anchor
  set, not merely named by the root (fixes **C3**).
- **Revocation at use** — `obtain_current_revocation` re-obtains the set from the trusted boundary and
  `validate_active_import_externally_rooted` rechecks the handle at USE (`AI4-REVOKED-AT-USE`), failing closed
  when the source is unavailable (`AI4-REVOCATION-UNAVAILABLE`) (fixes **C4**).
- **Typed candidate mode** — `CANDIDATE_MODES` (`TEST_ONLY` / `INERT_SIMULATION` / `REAL_CANDIDATE`), never a
  bare bool, never defaulting to real. The wrapper routes `REAL_CANDIDATE` **only** through the
  externally-rooted path; the raw path is mechanically unreachable in real mode (fixes **C5**).

## Real authority is UNAVAILABLE

`REAL_CANDIDATE` always fails closed here with `TC-PRODUCTION-PROVIDER-UNAVAILABLE` →
`RA-C-TRUST-CONTEXT-UNAVAILABLE` / `AI4-TRUST-CONTEXT-UNAVAILABLE`
(+ `F2R1-REAL-CANDIDATE-REQUIRES-EXTERNALLY-VERIFIED-HANDLE`) **before any caller-supplied object is trusted**.
Real external authority-root/manifest resolution (F2-R2) and a KMS/HSM-backed verifier + externally-signed
approvers (F2-R3) remain outstanding.

## Honest scope notes

- The synthetic module verifier's key lives in-process; anything running in the same process could reflectively
  reach a name-mangled attribute. This mirrors the existing `ActivationAuthority` / `GovernedEvidenceSealer`
  pattern — the guarantee is that an ORDINARY caller of the API cannot supply or read the verifier, not that it
  is HSM-isolated. Real isolation is F2-R3.
- The registry immutable-snapshot hardening from F2-R1 is retained **unchanged**.
- No image is built, no runtime is wired, no live key / secret / KMS call / `/etc` write occurs. The only
  non-determinism is `secrets.token_bytes(32)` for the ephemeral in-process module verifier seal.
