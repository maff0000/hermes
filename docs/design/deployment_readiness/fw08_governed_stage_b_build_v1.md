# HERMES FW-08 — Governed Stage-B Candidate-Image Build Enforcement (v1)

- **WO:** WO-HELM-HERMES-FW08-GOVERNED-STAGE-B-IMAGE-BUILD-ENFORCEMENT-IMPLEMENTATION-0001
- **Authority:** HELM (HERMES market-data lane)
- **Status:** IMPLEMENTED tooling + contracts + fixture/dry-run tests. **NO image built / published /
  deployed / wired / installed / enabled / executed.** PR open, unmerged, inert.
- **Created (UTC):** 2026-07-19
- **Canonical base:** `fe2037c5b4b4836d1038b4c0f734426ac9b3fa02` (merge of PR#113; carries undeployed
  PR#103–#113)
- **Repo:** `git@github.com:maff0000/hermes.git`

> **Not complete until merged + lineage-audited.** This document does not claim FW-08 is complete,
> deployed, or active. It describes the repository-owned tooling that makes a FUTURE Stage-B build
> mechanically governed. R2D2 must perform an exact-head cold audit before merge.

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
| **F-113-04** secret scan = defence-in-depth (not comprehensive) | §17 kept as-is with documented limits; the wrapper still fails closed on detected secret patterns and reports safe path + rule-id + count only. |

---

## Reused (already-merged PR#113 — imported, NOT modified)

- `tools/hermes_clean_build_context_v1.py` — exact-SHA `git archive` clean-context export + deterministic
  manifest + mechanical scan; fail-closed exit 2 on abbreviated/invalid SHA.
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
| §19 | Candidate-readiness evaluator (pure) | `design/hermes_fw08_readiness_evaluator_v1.py` |
| §11–§17,§20,§21 | Inputs / command / runners / OCI / image-content / SBOM / vuln / guards | `tools/hermes_stage_b_build_v1.py` |
| — | Candidate-readiness evidence schema | `schemas/deployment_readiness/stage_b_candidate_readiness.v1.schema.json` |
| §23 | Tests | `tests/test_fw08_governed_build_v1.py` |

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
