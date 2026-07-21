# FW-08 F2-R1 — External Producer-Registry Root of Trust

WO-HELM-HERMES-FW08-F2-R1-EXTERNAL-PRODUCER-REGISTRY-ROOT-OF-TRUST-IMPLEMENTATION-0001.
Owner: HERMES (Helm). Created (UTC): 2026-07-21. Contract version: 1. **INERT — no image build, no live authority.**

## The defect (F2-R1)

FW-08 F-2 introduced `design/hermes_fw08_producer_registry_v1.py::ProducerRegistry` with integrity, scope,
expiry and freeze controls. But registry **authority is caller-mintable**: a caller can construct a fresh
registry, invent an approval authority, register controlled producers with `real_evidence_authority=True`,
freeze it, recompute every checksum, and hand it to the candidate-readiness validator.

A checksum proves **INTEGRITY** (the bytes were not altered). It does **NOT** prove **AUTHENTICITY** (that
THIS registry is the one a governed external authority actually approved). Those are different properties. An
attacker who controls the bytes controls the checksum too.

## The correction

Candidate-readiness code now accepts a registry **only via an ACTIVATED HANDLE** minted by resolution through
a governed **EXTERNAL authority boundary**:

- `AuthorityRoot` (§7) — the external root of trust; classification is a field but trust is conferred by the
  **resolver returning it**, never by a caller setting the string.
- `AuthorisedRegistryManifest` (§8) — binds a registry by `registry_digest` + `producer_set_digest` (never
  inlines it) under an authority root, backed by an approval quorum.
- `ApprovalRecord` / `evaluate_approvals` (§14) — distinct, authorised, non-self, unexpired quorum.
- `RevocationSet` (§15) — trust is revocable.
- `AuthorityResolver` (§9) — THE external boundary. `ProductionAuthorityResolver` is **structurally
  non-functional** in this WO (raises `ResolverNotConfigured`); `SyntheticAuthorityResolver` is test-only.
- `ActivationAuthority` + `ActivatedRegistryHandle` (§10) — the handle carries an HMAC **seal** computed with
  an ephemeral, name-mangled, never-serialised key (mirrors `GovernedEvidenceSealer`). A caller-built
  lookalike handle fails `is_trusted` with `AH-SEAL-INVALID`.
- `activate_registry` (§11) — the sole minting path (14-step order), fails closed at every step.

**Unforgeable by ordinary callers, and impossible to obtain for a REAL candidate** without an external
resolver this WO does not provide.

## Scope — nothing is activated here

No root-of-trust source is selected, configured, or activated in this WO. The default/fake build runner
exposes no resolver, so the existing raw-registry active-import path is used unchanged (byte-behaviour
identical). Real external authority integration (a working `ProductionAuthorityResolver`) is **outstanding**
(F2-R2/R3). See `f2_r1_root_of_trust_sources.v1.json` for the allowed future sources and their requirements.
