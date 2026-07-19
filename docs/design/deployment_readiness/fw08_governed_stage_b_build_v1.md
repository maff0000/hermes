# HERMES FW-08 — Governed Stage-B Candidate-Image Build Enforcement (v1)

- **WO:** WO-HELM-HERMES-FW08-GOVERNED-STAGE-B-IMAGE-BUILD-ENFORCEMENT-IMPLEMENTATION-0001
- **Authority:** HELM (HERMES market-data lane)
- **Status:** IMPLEMENTED tooling + contracts + fixture/dry-run tests, **plus the PR#114 exact-audit
  corrections (§§ below).** **NO image built / published / deployed / wired / installed / enabled /
  executed.** PR#114 open, unmerged, inert.
- **Created (UTC):** 2026-07-19
- **Canonical base:** `fe2037c5b4b4836d1038b4c0f734426ac9b3fa02` (merge of PR#113; carries undeployed
  PR#103–#113)
- **Repo:** `git@github.com:maff0000/hermes.git`

> **Not complete until the CORRECTED head is audited + merged + lineage-audited.** This document does not
> claim FW-08 is complete, deployed, or active. The R2D2 exact-head audit of the prior head `066ff0aa`
> returned GREEN with a **canonical-preflight correction required**; the prior `066ff0aa` head was therefore
> **NOT merge-ready on its own** — the eight corrections below had to be applied to the open PR first. The
> **real canonical preflight must reach `USABLE` against the exact canonical `fe2037c5` before any real
> Stage B**, and the Stage-B build itself is **separately authorised**. Synthetic fixtures do NOT certify a
> real candidate image. R2D2 must cold-audit the CORRECTED head before merge.

---

## Purpose

Resolve the R2D2 carry-forward findings on the PR#113 pre-Stage-B gate and make a future Stage-B HERMES
candidate-image build the output of a single, fail-closed, mechanically-governed wrapper — never an ad-hoc
`docker build`.

| Finding | Resolution in FW-08 |
|---------|---------------------|
| **F-113-01** `remote.origin.url` is a weak identity signal | §7 trusted canonical-ref **reachability**: the source SHA must be reachable from `refs/remotes/origin/main` (or be an authorised PR head with a governed audit binding). Read-only, `--no-replace-objects`. |
| **F-113-02** OCI labels declared mandatory but not mechanically unavoidable | §13 the governed wrapper invokes the label verifier with `expected_source_sha` (never optional) and requires post-build revision/created equality; a direct build with empty labels **fails**. |
| **F-113-03** `.dockerignore` matcher not Docker-faithful | §10 a parity-fixtured Docker/Moby-faithful matcher establishes the effective context; the homemade PR#113 matcher is **not** the Stage-B proof. |
| **F-113-06** prohibited paths materialised before failure | §9 pre-materialisation rejection + quarantine: prohibited members are rejected before export where practical; a failed export is quarantined, stamped UNUSABLE, and destroyed. |
| **F-113-04** secret scan = defence-in-depth (not comprehensive) | §17 kept as defence-in-depth; the wrapper still fails closed on detected secret patterns and reports safe path + rule-id only. The PR#114 §6 correction adds a governed, fingerprint-bound disposition for the scanner's own rule-literal self-match (never a value; detection unchanged). |

---

## PR#114 exact-audit corrections (governed on the OPEN PR before merge)

The R2D2 exact-head audit of `066ff0aa` returned `GREEN … EXACT_HEAD_AUDITED_WITH_CANONICAL_PREFLIGHT_
CORRECTION_REQUIRED`. HELM independently reproduced the self-match: the clean-context tool run against the
canonical `fe2037c5` returns `result=FAIL` / exit 3 with **one** secret finding, rule `SEC-PRIVATE-KEY-PPK`,
path `tools/hermes_clean_build_context_v1.py` (the scanner's own PPK rule literal self-matching; **value not
disclosed**). The following eight corrections were applied to the open PR:

| § | Correction | Where |
|---|-----------|-------|
| §6 | **Secret-scanner false-positive** → a versioned, deterministic, **TYPED, fingerprint-bound disposition** mechanism. A disposition binds contract_version, exact rule_id, exact repo-relative path, a **value-free fingerprint** (`sha256(rule_id + path + one-way value_digest + structure_digest)` — the matched bytes are folded one-way and **never stored/exposed**), category, reason_code, owner, creation UTC, permanence-or-expiry, source-SHA-or-policy binding, evidence_reference. Detection is **not** weakened. Only the exact governed fingerprint may be dispositioned; unknown/changed/expired/malformed findings **FAIL CLOSED**. No skip-by-path/rule-alone, no wildcards, no CLI/env ignore lists, no failure→warning. | `tools/hermes_clean_build_context_v1.py`, governed records in `tools/fw08_scanner_dispositions.v1.json` |
| §7 | **Real canonical preflight** (non-building): trusted-source verify → trusted-ref freshness → clean-context export → manifest validate → effective-context validate → prohibited-path scan → secret scan + governed dispositions → build-input validation → final TOCTOU revalidation, **STOPPING before Docker**. The exact canonical `fe2037c5` reaches terminal `USABLE`; **no runner is constructed, no docker/image/tag/SBOM/vuln/publish/deploy**. | `run_canonical_preflight()` |
| §8 | **Trusted-ref freshness** bound via an authenticated read-only fetch (injectable fetcher) + exact `origin/main` comparison + WO-authorised SHA equality + reachability + no local-ref substitution; a fetch failure **fails closed** unless a separately-governed immutable source binding is supplied. Mutation-bearing cases use ISOLATED fixture repos; no live canonical fetch. | `verify_trusted_ref_freshness()` |
| §9 | **Final TOCTOU boundary**: an immutable snapshot (per-file sha256 + size + mode + symlink-ness + file count + manifest + effective checksums) is revalidated **immediately before the runner receives the context**; any changed/appeared/disappeared/type-change/mode-change rejects **before** invocation. | `finalise_context_snapshot()` / `revalidate_context_snapshot()` |
| §10 | **Docker-pattern parity**: leading-whitespace-before-comment is Moby-faithful (a literal pattern, not a comment); inverted char classes `[^…]` behave correctly; escaped comment/negation markers, ranges, literal-bracket escapes, parent-exclusion + child-negation, repeated `**`, trailing spaces, rooted/unrooted all covered by parity fixtures. **Unsupported / malformed syntax FAILS CLOSED** (no silent reinterpretation). | `design/hermes_fw08_dockerignore_matcher_v1.py` |
| §11 | **Vuln allow-list governance**: a CRITICAL/HIGH is governed **only** by an exact, typed, unexpired disposition binding **every** required field (vuln_id, package, installed_version, image_id, source_sha, severity, reason, risk_owner, approval_authority, creation/expiry UTC, scanner id+version, vuln-DB id+timestamp, evidence_checksum, disposition_id). A truthy id alone, a wildcard, a mismatch, or an expiry does **not** govern. **No blanket exception.** | `design/hermes_fw08_vuln_disposition_v1.py` |
| §12 | **Readiness evidence binding**: the evaluator no longer accepts fabricatable bare booleans. Each gate result is an immutable typed evidence record (gate_id, result, reason_code, source_sha, image_id, evidence_checksum, producer/tool identity, UTC, contract_version); the evaluator recomputes each binding checksum and validates cross-gate consistency (same source_sha; same image_id post-build; lifecycle ordering; no stale/duplicate-conflicting/missing evidence). Pure + I/O-free; inputs are **evidence records, not booleans**. | `design/hermes_fw08_readiness_evidence_v1.py` |
| §13 | **Active Phase-2 import evidence**: a future real image must prove Phase-2 modules present-but-inert via inspectable evidence (static import graph / entrypoint transitive-import scan / non-running module-loader trace). Proof requires: all modules present; none imported by the startup entrypoint (direct **or** transitive); none registered as runner/callback; none dynamically imported; no activation env default; no plugin discovery; a test-only declaration is **not** accepted. Fixture-backed; no image. | `design/hermes_fw08_active_import_evidence_v1.py` |

**Doctrine (must remain true):** the canonical preflight must pass before a real Stage B; dispositions are
narrow + fingerprint-bound; trust freshness is mandatory; the final TOCTOU boundary is mandatory; the matcher
fails closed on unsupported syntax; vuln dispositions are fully governed; readiness is evidence-bound;
real-image active-import evidence is mandatory; **synthetic fixtures do NOT certify a candidate image**; PR#114
is incomplete until the corrected head is audited + merged; the Stage-B build is separately authorised.

---

## Reused (already-merged PR#113 — imported)

- `tools/hermes_clean_build_context_v1.py` — exact-SHA `git archive` clean-context export + deterministic
  manifest + mechanical scan; fail-closed exit 2 on abbreviated/invalid SHA. **PR#114 §6 adds ONLY the
  narrow, fingerprint-bound finding-disposition mechanism to this file (detection unchanged; no value
  stored); PR#113's own contract tests still pass unchanged.**
- `tools/hermes_image_label_verify_v1.py` — OCI label verifier (`--expected-source-sha` / inspect / labels
  / legacy).
- `schemas/deployment_readiness/build_context_manifest.v1.schema.json`.

The governed wrapper is built **on top of** these and makes them **mandatory**.

---

## Components (production-owned; NOT imported by runtime)

| § | Component | Path |
|---|-----------|------|
| §6 | Governed build wrapper (sole entrypoint) | `tools/hermes_stage_b_build_v1.py` |
| §7 | Trusted canonical provenance | `verify_trusted_provenance()` in the wrapper |
| §8/§9 | Clean-context integration + quarantine | `prepare_governed_context()` in the wrapper |
| §10 | Docker-faithful `.dockerignore` matcher + parity fixtures | `design/hermes_fw08_dockerignore_matcher_v1.py` |
| §10/§14 | Build-context + image-content contract constants | `design/hermes_fw08_context_contract_v1.py` |
| §18 | Candidate-readiness state machine | `design/hermes_fw08_candidate_state_v1.py` |
| §19 | Candidate-readiness evaluator (pure, boolean) | `design/hermes_fw08_readiness_evaluator_v1.py` |
| §12 (PR#114) | Evidence-bound readiness evaluator (pure) | `design/hermes_fw08_readiness_evidence_v1.py` |
| §11 (PR#114) | Vuln-disposition governance (pure) | `design/hermes_fw08_vuln_disposition_v1.py` |
| §13 (PR#114) | Phase-2 active-import evidence contract (pure) | `design/hermes_fw08_active_import_evidence_v1.py` |
| §6 (PR#114) | Governed secret-scanner dispositions | `tools/fw08_scanner_dispositions.v1.json` |
| §7–§9 (PR#114) | Canonical preflight / trusted-ref freshness / final TOCTOU | `tools/hermes_stage_b_build_v1.py` |
| §11–§17,§20,§21 | Inputs / command / runners / OCI / image-content / SBOM / vuln / guards | `tools/hermes_stage_b_build_v1.py` |
| — | Candidate-readiness evidence schema | `schemas/deployment_readiness/stage_b_candidate_readiness.v1.schema.json` |
| — (PR#114) | Machine-readable corrections contract | `schemas/deployment_readiness/fw08_corrections.v1.json` |
| §23 | Tests | `tests/test_fw08_governed_build_v1.py`, `tests/test_fw08_corrections_v1.py` |

---

## Trusted provenance (§7, F-113-01)

**Allowed canonical refs:** `refs/remotes/origin/main` (default; configurable list). A source SHA is trusted
iff it is a full 40-hex commit object, the repo identity matches, AND either:

1. it is **reachable from an allowed canonical ref** (`git --no-replace-objects merge-base --is-ancestor`),
   returning `REACHABLE_FROM_CANONICAL_REF`; or
2. it is an **authorised PR head** carrying a non-empty governed audit binding, returning
   `AUTHORISED_PR_HEAD`.

**Rejected:** arbitrary orphan commit; foreign repo with a spoofed remote string (identity matches but SHA
not reachable); git-replace / alternate-object injection (`--no-replace-objects` judges true reachability);
branch / tag / `HEAD` / `latest`; abbreviated SHA; any detached object not proven reachable. The check is
**read-only** — it never fetches or mutates the canonical repo (reject cases use isolated tmp repos).

---

## Docker invocation boundary (§12)

The docker command is **fully constructed by the wrapper** as an explicit argument array — no shell, no
`shell=True`, no user-supplied arbitrary docker args:

```
docker build --file <ctx>/Dockerfile --tag hermes-fw08-candidate-<name>:local-<sha12>
  --build-arg SOURCE_SHA=<full-40-hex> --build-arg BUILD_UTC=<tz-aware-UTC>
  --label org.opencontainers.image.revision=<sha> --label org.opencontainers.image.created=<utc>
  --no-cache <ctx>
```

Rejected argument classes: `--push`, registry targets, `--output type=registry`, `--network`/`host`,
`--privileged`, `--add-host`, `--secret`, `--ssh`, remote builder, registry-style tags. `SOURCE_SHA` +
`BUILD_UTC` build-args are mandatory; the target is an explicit **local, non-registry** image tag.

**Injectable runner + real-docker non-invocation.** The wrapper calls docker/SBOM/scan **only** through a
`DockerRunner`. The **default** is `RefusingDockerRunner` (raises on everything). `RealDockerRunner` is a
**distinct class** doubly gated: it requires `enable_real_execution=True` **and**
`HERMES_FW08_ALLOW_REAL_DOCKER=1`, and fails closed **before any subprocess** otherwise. Tests inject a
`FakeRunner` returning fixture payloads and assert (behaviourally, by monkeypatching `subprocess`) that no
`docker` process is ever spawned.

---

## Candidate readiness (§18/§19)

State machine (ordered, non-skippable): `NOT_STARTED → SOURCE_VERIFIED → CONTEXT_EXPORTED →
CONTEXT_VERIFIED → BUILD_READY → BUILD_COMPLETED → IMAGE_INSPECTED → SBOM_COMPLETED →
VULNERABILITY_SCAN_COMPLETED → CANDIDATE_READY`; `REJECTED` terminal. **No PUBLISHED/DEPLOYED state exists.**

The pure evaluator returns `ready=True` only when **every** gate passes: trusted source verified, clean
context manifest valid, effective docker context valid, prohibited findings = 0, secret findings = 0 (or a
governed policy permits only explicitly-classified non-secret matches), build succeeded, image id captured,
OCI labels exact-match, image content passed, SBOM passed, vuln scan passed, **and** every safety flag
clear: `publish_attempted=false`, `deploy_attempted=false`, `consumer_live=false`, `shadow_enabled=false`,
`phase2_activated=false`.

**Candidate readiness ≠ publication ≠ deployment ≠ activation.** The evidence bundle records
`runtime_wired=false`, `config_installed=false`, `shadow_enabled=false`, `shadow_executed=false`,
`consumer_live=false`, `published=false`, `deployed=false`.

---

## Non-mutation & dependencies

- No real image built/tagged/published; no registry login/push; no real SBOM/scan; no deploy / container
  replace / restart; no runtime wiring / Phase-2 import into active runtime; no config install; no evidence
  mount; no shadow enable/execute; no Redis/SQL/migration; no `/etc`/host-global writes; no new third-party
  dependency (stdlib only). UTC throughout.
- **FW-08 does not correct HELM deployment truth (§26).** That correction remains a dependency on **FW-16**.
- **This doc must not state FW-08 is complete** until merged + lineage-audited.
