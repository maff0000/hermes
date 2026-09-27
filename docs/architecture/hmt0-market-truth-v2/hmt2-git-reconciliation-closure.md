# HMT-2 Git/GitHub Reconciliation Gate — Closure Record

**Initiative:** `HMT-2 Git/GitHub Reconciliation Gate`
**Verdict:** `GREEN — HMT-2 GIT AUTHORITY RESTORED`
**Verdict date:** 2026-09-27
**Status:** DOCUMENTATION ONLY. This record is written from the already-merged state of
`hmt-2/governed-gc-mbp1-historical-corpus` at commit `be8da9527a09cbcf3d5cbe3dff4a52360fb4d487`. No
`.py` file, migration, schema, config, or CI workflow is touched by this record.

## What this closes

Before this reconciliation, HMT-2's real operational mechanisms — the mechanisms that actually ran
HMT-2 acquisition/canonicalisation sessions — existed only as ad-hoc scripts on Trinity, an ungoverned
host outside Git. That is a Git/GitHub authority gap: the thing that actually ran in production was not
the thing anyone could review, diff, or roll back. This record closes that gap: the real mechanisms are
now ported, tested, portable HERMES repository code, merged to the governed branch, and have been
proven to run — via disposable proof, never touching real production paths — on **both** dell-debian and
Trinity, with every prior ad-hoc/ungoverned copy retired (archived, not deleted).

## 1. Full topology / lineage

```
sealed HELM handoff bundle (Trinity, ungoverned)
        |  /srv/hmt-data/evidence/git-reconciliation/trinity-operational-delta/
        |  manifest sha256 557d0e9d3d1048717840e5dd3a71cf7546c81fba6c348cb19274d06362d1ddd9
        v
classification (4 product/operational code artefacts, 3 deployment/infra assets,
                 1 test tool, 7 docs, 1 runtime-data snapshot, 7 OBSOLETE)
        v
independent verification (all 23 artefacts hash-checked, immutability confirmed)
        v
port into governed repo code
        |  branch: wo/WO-HERMES-HMT2-TRINITY-OPS-PORT-0001
        |  commit d04664b — "Port Trinity HMT-2 operational delta into governed repo code"
        v
independent, adversarial audit
        |  GREEN, with LIVE reproduction on real Trinity host of the privilege-boundary
        |  negative tests (nested systemd-run genuinely fails "Interactive authentication
        |  required"; secret-file read genuinely denied to hmt-compute, allowed to hmt-data)
        |  ONE minor non-blocking nit raised: a Trinity-shaped literal default in config.py
        v
follow-up fix, pre-merge
        |  commit 3e0a0ab — "Fail loud instead of guessing a host path"
        |  (removes the Trinity-literal path defaults the audit flagged; every no-safe-
        |  cross-host-default field now resolves to None when unset and validate() raises
        |  ConfigError naming every missing env var, rather than silently guessing)
        v
MERGE — PR #179
        |  commit be8da9527a09cbcf3d5cbe3dff4a52360fb4d487
        |  parents: ca48e8c250aa661cb415f8c75cc867a737062581 (prior branch tip, PR #177)
        |           3e0a0ab2522758bf29d306e6891662bb13381352 (the WO branch incl. the fix)
        |  into hmt-2/governed-gc-mbp1-historical-corpus
        v
dell-debian proof (authoritative worktree fast-forwarded to be8da95)
        |  real system identities/groups created matching the privilege model
        |  systemd units installed; both launchers proven to reproduce every privilege
        |  boundary (incl. the anti-pattern negative test) using a DISPOSABLE PROOF ROOT
        |  (never touching real production corpus paths)
        |  read-only proof: real existing governed canonical data read through the new
        |  config-driven path resolution
        v
Trinity proof (the actual live host — real identities, real paths, real 422-session
                canonical ledger)
        |  real config installed at /etc/hmt2/hmt2-ops.env
        |  the two governed launcher scripts + disk-guard script installed IN PLACE OF the
        |  prior ad-hoc versions
        |  every privilege/anti-pattern proof reproduced live and successfully
        v
retirement
           old ad-hoc launcher versions -> archived (not deleted) at
               /srv/hmt-code/archived-pre-governed-launchers/
           7 obsolete Trinity-only scripts + the old un-ported orchestrator -> archived
               (not deleted) at /srv/hmt-code/archived-superseded-artefacts/
           systemd units (hmt2.slice, disk-guard timer) remained healthy and undisturbed
               throughout (daemon-reload succeeded, slice stayed idle/Tasks:0)
```

One governed implementation now runs on both hosts. No competing ad-hoc copies remain live.

### Exact commit SHAs (as verified against the real, current tip of the branch)

| Ref | SHA | What |
|---|---|---|
| Merge commit (PR #179) | `be8da9527a09cbcf3d5cbe3dff4a52360fb4d487` | Merges the WO branch into `hmt-2/governed-gc-mbp1-historical-corpus` |
| Merge parent 1 (prior tip) | `ca48e8c250aa661cb415f8c75cc867a737062581` | `hmt-2/governed-gc-mbp1-historical-corpus` before this merge (PR #177) |
| Merge parent 2 (WO branch tip) | `3e0a0ab2522758bf29d306e6891662bb13381352` | Config-default fix, last commit on `wo/WO-HERMES-HMT2-TRINITY-OPS-PORT-0001` |
| Port commit | `d04664b` (short) | "Port Trinity HMT-2 operational delta into governed repo code" |
| Config-default fix commit | `3e0a0ab` (short) | "Fail loud instead of guessing a host path" |

These were confirmed directly from `git log`/`git show` against `origin/hmt-2/governed-gc-mbp1-historical-corpus`
on dell-debian at the time this record was written — not transcribed from an earlier plan.

## 2. Privilege model, as now governed in Git

Two non-overlapping identities, enforced structurally, not by operator discipline:

- **Canonical compute identity** (`hmt-compute` on Trinity; a host names its own equivalent) — runs
  canonical dispatch/compute sessions. Launched via
  [`deployment/hmt2/hmt2-run.sh`](../../../deployment/hmt2/hmt2-run.sh): a single `systemd-run --scope
  --slice=<slice> --uid=<compute> --gid=<hmt>` boundary, run once per session, never nested. This
  identity is **never** a member of the acquire-secrets group and is granted **no** generic
  `systemd-run` authority (no polkit rule, no sudo, no linger).
- **Acquisition identity** (`hmt-data` on Trinity) — the only identity that can read the Databento
  credential. Launched via
  [`deployment/hmt2/hmt2-acquire-run.sh`](../../../deployment/hmt2/hmt2-acquire-run.sh): a **transient
  service unit** (`--unit=<name>`, deliberately never `--scope`) with `--property=SupplementaryGroups=`
  active, because — empirically verified — only service units apply `SupplementaryGroups=` via
  systemd's exec framework; a `--scope` invocation of the same identity comes back with the base group
  only. This is the documented, deliberate reason the two launchers use different `systemd-run`
  invocation shapes; do not "simplify" them to match.
- A `hmt-acquire-secrets`-equivalent group whose sole member is the acquisition identity; the secret
  file/directory is owned `root:<that group> 0750`/`0440` so the canonical compute identity cannot even
  `ls` its directory.
- The single, root-owned orchestrator,
  [`market_truth/acquisition/orchestration/canonical_root_orchestrator.py`](../../../market_truth/acquisition/orchestration/canonical_root_orchestrator.py),
  must itself run directly as root (`os.geteuid() != 0` refusal enforced in `main()`) and calls the
  launcher exactly once per session — never wrapped in anything that would place the orchestrator
  itself inside `hmt2.slice`.

**The anti-pattern, explicitly forbidden and never revived:** a nested `systemd-run` — the canonical
compute identity attempting to call `systemd-run` a second time from inside its own already-scoped
process. This fails with a polkit "Interactive authentication required" error, and that failure **is
the correct, intended security boundary**, not a bug. The retired script that did this,
`hmt2_routine_recovery_driver_v2.py`, is documented (never revived) in
[`docs/architecture/hmt0-market-truth-v2/hmt2-trinity-operational-delta-obsolete-notes.md`](hmt2-trinity-operational-delta-obsolete-notes.md).
The corrected topology is `canonical_root_orchestrator.py`'s own module docstring, and is exercised by
`tests/hmt2/orchestration/test_privilege_boundary.py`.

Supporting modules, all under `market_truth/acquisition/orchestration/`: `config.py` (below),
`host_guards.py` (disk/memory/PSI preflight + `CausalMemorySampler`), `chunk_allowlist.py`
(ledger-truth-derived, bounded session batches — never a hardcoded ordinal position),
`exceptional_envelope.py` (below), `quarantine.py` and `retry.py` (failed-acquisition handling), and
`market_truth/acquisition/diagnostics/credential_check.py` (a free, non-billable credential-validation
diagnostic, parametrised over session id and ledger path — never a hardcoded path).

**Exceptional memory envelope:** routine cgroup envelope is `MemoryHigh=12G` / `MemoryMax=16G`
(`hmt2.slice`); an Architect-authorised exceptional envelope may raise the ceiling up to `MemoryMax=24G`
for exactly one session, applied only against a caller-supplied `EnvelopeAuthorization`, always restored
to routine on success **or** failure (structural, via the module's context-manager `finally`), and never
auto-escalated by the mechanism itself — the ceiling is an operator/Architect-owned configuration input,
not something the code decides on its own initiative.

## 3. Configuration doctrine (3-tier model)

Governed by `market_truth/acquisition/orchestration/config.Hmt2OpsConfig`, mirroring the doctrine already
established elsewhere in this repository:

- **Tier 1 — defaults/schema**: the typed shape of every configurable value, plus safe, host-agnostic
  defaults for **generic policy/tuning fields only**.
- **Tier 2 — environment/deployment configuration**: every field is overridable by an environment
  variable of the same convention (`HMT2_OPS_*`, see `ENV_VAR_NAMES` in `config.py`), sourced in
  production from `/etc/hmt2/hmt2-ops.env` (overridable via `HMT2_OPS_ENV_FILE` for testing).
- **Tier 3 — secrets (file-backed, never read here)**: `config.py` resolves only the *path* to the
  Databento credential file (`HMT2_OPS_ACQUIRE_SECRET_PATH` / `acquire_secret_path`); the credential
  *value* is read exclusively inside the acquisition worker process (the acquisition identity), never by
  any orchestration module, never logged or printed.

### Host-specific values — every future deployment target MUST configure these; no safe cross-host default exists (unset -> loud `ConfigError`, never a silent guess)

| Env var | Field | Why no default is safe |
|---|---|---|
| `HMT2_OPS_REPO_DIR` | `repo_dir` | Different clone location on every host |
| `HMT2_OPS_VENV_PYTHON` | `venv_python` | Different Python venv location per host |
| `HMT2_OPS_LAUNCHER_PATH` | `launcher_path` | Where `hmt2-run.sh` is installed on this host |
| `HMT2_OPS_ACQUIRE_LAUNCHER_PATH` | `acquire_launcher_path` | Where `hmt2-acquire-run.sh` is installed |
| `HMT2_OPS_SCRATCH_DIR` | `scratch_dir` | Host-local scratch/TMPDIR path |
| `HMT2_OPS_EVIDENCE_ROOT` | `evidence_root` | Host-local evidence output path |
| `HMT2_OPS_DISK_GUARD_MOUNT` | `disk_guard_mount` | The filesystem mount to watch differs per host |

Also host-specific in value (configured via `/etc/hmt2/hmt2-ops.env` for the shell launchers, not through
`Hmt2OpsConfig` directly): `HMT2_COMPUTE_UID`/`HMT2_COMPUTE_GID`, `HMT2_DATA_UID`/`HMT2_DATA_GID`,
`HMT2_ACQUIRE_SECRETS_GID` — deliberately **not** reproduced numerically in this document (already
documented elsewhere as host-local, non-portable, per the governing exclusion on uid/gid values), and
`HMT2_CANONICAL_RESEARCH_ROOT` (or `None`, to defer to `canonical_worker`'s own resolution),
`HMT2_OPS_ORACLE_PATH`, `HMT2_OPS_ACQUIRE_SECRET_PATH` (path only).

### Generic, host-agnostic policy/tuning values — same sane default expected correct on any host until a deployer overrides it

`HMT2_OPS_SLICE_NAME` (default `hmt2.slice`), `HMT2_OPS_DISK_GUARD_WARN_PCT`/`STOP_PCT` (70/80),
`HMT2_OPS_PER_SESSION_TIMEOUT_SEC` (900), `HMT2_OPS_PRE_SESSION_MIN_MEMAVAILABLE_KB` /
`IN_SESSION_MIN_MEMAVAILABLE_KB` (40 GiB / 24 GiB), `HMT2_OPS_SUSTAINED_LOW_MEM_SECONDS` (60),
`HMT2_OPS_POLL_INTERVAL_SEC` (5), `HMT2_OPS_PSI_FULL_AVG10_STALL_THRESHOLD` (5.0),
`HMT2_OPS_ROUTINE_MEMORY_HIGH_BYTES`/`MAX_BYTES` (12 GiB / 16 GiB),
`HMT2_OPS_EXCEPTIONAL_PREFLIGHT_MIN_MEMAVAILABLE_KB` (40 GiB),
`HMT2_OPS_EXCEPTIONAL_AUTHORISED_CEILING_MEMORY_MAX_BYTES` (24 GiB),
`HMT2_OPS_MAX_CANONICAL_CHUNK_SESSIONS` (10).

`Hmt2OpsConfig.validate()` additionally enforces cross-field sanity (e.g. the exceptional ceiling must
exceed the routine max; the in-session memory floor must be strictly less than the pre-session floor;
disk-guard warn must be strictly less than stop) — a future third deployment target inherits these checks
automatically and cannot configure a nonsensical policy without a loud `ConfigError`.

The three shell launchers/disk-guard script are byte-for-byte identical across hosts by construction —
only `/etc/hmt2/hmt2-ops.env` differs. The systemd unit files are shipped as `.template`s with `${VAR}`
placeholders, rendered per host via `deployment/hmt2/render_unit_template.sh` (an explicit-allowlist
`envsubst`, never a blanket substitution).

## 4. dell-debian vs. Trinity — roles going forward

**dell-debian is HERMES's development authority and intended normal runtime/replay environment.**
**Trinity is temporary/auxiliary heavy historical acquisition/build/recovery infrastructure — it is not
required for normal HERMES operation, and it must not become an architectural runtime dependency.**

This reconciliation exists specifically to end Trinity's prior status as an ungoverned, undocumented
place where real mechanisms silently lived and ran outside Git. That status is now closed: the
mechanisms are governed code, reviewable and portable, and Trinity's own installation runs the exact
same governed artefacts as dell-debian's. Trinity's continued *use* for heavy historical
acquisition/build/recovery work is expected and acceptable — its continued existence as a place where
*ungoverned* logic can silently diverge from Git is what this reconciliation forecloses. Nothing in
HERMES's normal (non-HMT-2-acquisition) runtime path may come to depend on Trinity being available.

## 5. Current corpus state at closure

**Snapshot date: 2026-09-27T11:20:00Z**, from the sealed bundle's `reports/current_trinity_state.json`.
**This is a point-in-time snapshot, not a live-updating figure** — it describes the corpus state at the
moment of closure, not an ongoing measurement this document tracks. Anyone needing the current state
after this date must re-query the live ledgers, not this record.

| Ledger | State | Count |
|---|---|---|
| Acquisition ledger | COMPLETE | 422 |
| Acquisition ledger | PLANNED | 26 |
| Acquisition ledger | FAILED_AMBIGUOUS | 0 |
| Canonical ledger | CANONICAL_COMPLETE | 422 |
| Canonical ledger | CANONICAL_PENDING | 0 |

**Confirmed HMT-2 programme spend at closure:** $53.735906 of a $100 ceiling.

## 6. Disclosed, unresolved exposure — carried forward, not resolved

A conservatively-carried, **unresolved** worst-case exposure of **$0.246818** exists: a truncated
download that was never billed per the HERMES ledger, with no provider-side evidence available to either
confirm or rule out an external charge. This reconciliation does **not** resolve this exposure. It is
recorded here honestly, exactly as disclosed in the sealed bundle, and remains open. It must continue to
be carried forward in any future accounting of HMT-2 programme spend until independently resolved
(e.g. against a provider statement) — not written off, not hidden, not rounded away.

## 7. Batch 14 — remains BLOCKED

**Batch 14 remains BLOCKED, pending separate Architect authorization.** Nothing in this reconciliation —
the port, the audit, the merge, or either runtime proof — authorizes, implies, or creates a pathway
toward resuming Batch 14. Only the Architect may authorize its resumption, and this document explicitly
does not do so.

## 8. Explicitly out of scope / not started by this reconciliation

HMT-3, HMT-LIVE-1, DARWIN, any canonical semantic change, any ledger schema change, Batch 14 (see §7),
and any real Databento acquisition. This reconciliation is a Git/GitHub authority and deployment-parity
closure only.

## Mirroring — action required by a session with Memory Fabric access

This session (a documentation-only FORGE Engineer with SSH + `gh` access, no Memory Fabric access) cannot
write to R2D2's blueprint or to Memory Fabric directly. **This Git document is the authoritative source**
for that mirroring. A separate step, by whoever has Memory Fabric access (e.g. R2D2 or Helm), should:

- Mirror the topology, verdict, and the two disclosed open items (§6's `$0.246818` exposure, §7's Batch
  14 BLOCKED status) into R2D2's blueprint.
- Store an equivalent closure record in Memory Fabric under the appropriate persona namespace(s).

Until that mirroring happens, this document — at commit history rooted in
`be8da9527a09cbcf3d5cbe3dff4a52360fb4d487` — is the single source of truth for this closure.

## Verdict

**GREEN — HMT-2 GIT AUTHORITY RESTORED**
**2026-09-27**
