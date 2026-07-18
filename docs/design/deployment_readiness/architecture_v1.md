# HERMES Cumulative PR103-PR111 Deployment-Readiness + Passive Phase-2 Runtime-Wiring Design (v1)

- **WO:** WO-HELM-HERMES-CUMULATIVE-PR103-PR111-DEPLOYMENT-READINESS-AND-PHASE2-RUNTIME-WIRING-DESIGN-0001
- **Authority:** HELM (HERMES market-data lane)
- **Status:** DESIGN-ONLY. Nothing here is implemented, built, deployed, wired, installed, enabled, or executed.
- **Created (UTC):** 2026-07-18
- **Canonical base/head:** `adc21c4dfef571debd0ef05bb22bc7af0b289ea3` (merge of PR#111)
- **Repo:** `git@github.com:maff0000/hermes.git`

> **Non-deployment assertion.** No statement in this design claims the Phase-2 shared-stream-recovery
> capability is deployed, live, active, wired, or authoritative. The capability is **present-but-inactive**
> in a candidate image built from the canonical base; it is not in the running container. The current
> Mode-C reconnect authority remains fully and solely authoritative.

This document is the main architecture doc. It is accompanied by machine-readable models under
`models/`, JSON schemas under `schemas/deployment_readiness/`, a disabled config example under
`examples/`, and bounded design-validation tests at `tests/test_deployment_readiness_design_v1.py`.
Where a claim is load-bearing it is grounded in canonical code at the base SHA; assumptions,
provisional values, and unavailable evidence are marked explicitly.

---

## §6 Cumulative PR portfolio (PR103-PR111)

Full per-PR fields are machine-readable in `models/nine_pr_inventory.v1.json` (verified from
`git diff --name-status <merge>^1..<merge>`, `.dockerignore`, `Dockerfile` line 52, and an
import-graph grep over the app root). Prior "inert" labels were re-verified, not trusted. Summary:

| PR | Merge | Class | Enters image? | Runtime-imported? | Effect |
|----|-------|-------|---------------|-------------------|--------|
| 103 | eb928a1c | PRODUCTION (1 runtime module) + docs/evi/test | yes | **yes** (`main.py:1140`) | `SUPPORTED_CONFIG_VERSIONS {2,3}->{3}`. No-op on live v3; v2 fails loud. **Only automatic runtime change on rebuild.** |
| 104 | ff497f36 | TOOLING (predeploy gate) | yes (tool) | no | Operator/CI gate; not imported. |
| 105 | f08f7df8 | CI-ONLY | **no** (`.github/` excluded) | no | Zero runtime effect. |
| 106 | 8de293de | DOCS/EVIDENCE + test | docs yes, test no | no | Inert reference evidence. |
| 107 | 458d430f | TOOLING (validator) + schema + fixtures | tool/schema yes, fixtures no | no | Operator validator; not imported. |
| 108 | ae80d113 | DESIGN prototype + schema | `design/` yes | no | Inert contract prototype. |
| 109 | 8355044b | PRODUCTION pure core + CI(M) + schema(M) | yes | **no** | Phase-1 pure core; present-but-inactive; no importer. |
| 110 | b50162a2 | DESIGN prototypes + schemas | `design/`/schemas yes | no | Inert Phase-2 design. |
| 111 | adc21c4d | 10 PRODUCTION `utils/hermes_sss_*` modules | yes | **no** | Phase-2 impl; present-but-inactive; disabled-by-default; RefusingShadowExecutor. |

**Automatic-runtime-effect ruling.** Only **PR103** modifies a module that a runtime path imports
(`main.py` builds `DstAwareMarketHours`). On the live **v3** config the change from `{'2','3'}` to
`{'3'}` is a behavioural **no-op**; a v2 config would now resolve to `None` (fail-loud: behave open +
validator error). Every other PR either does not enter the image (PR105 CI) or enters but is imported
by nothing on a runtime path (PR109 pure core, PR111 sss modules), or is a tool/doc/design artifact.

`requirements.txt` is **unchanged** across all nine PRs -> no dependency-lock change, no new startup
dependency, reproducible wheels unchanged.

Independent reversibility, interactions, and pre-build/pre-deploy validation per PR are in the model.

---

## §7 Image-content + build-context design

**Build context (verified).** `Dockerfile` line 52 `COPY --chown=hermes:hermes . ${APP_HOME}` copies
the whole filtered context. `.dockerignore` **excludes**: `tests/`, `ops/evidence/`, `.github/`,
`docker-compose*.yml`, `Dockerfile`, `.dockerignore`, `.env*`, `*.bak`, `*.log`, `__pycache__`,
`README*.md`, `mock/`, `/data/`, `signal_history/`, `.git`. It does **not** exclude `utils/`,
`design/`, `schemas/`, `docs/`, `config/`, `tools/`, `adapters/`, `ops/ci/`, `main.py`.

**Therefore a candidate image built from `adc21c4d` CONTAINS:** Phase-1 pure core
(`utils/hermes_shared_stream_recovery_v1.py`), the 10 Phase-2 `utils/hermes_sss_*_v1.py` modules,
`design/` prototypes, operator tools (`tools/hermes_predeploy_gate_v1.py`,
`tools/hermes_transition_evidence_validator_v1.py`), the PR103-modified market-hours loader, schemas
and docs. It **EXCLUDES:** CI workflow (`.github/`), all `tests/`, and `ops/evidence/`.

**Requirements the design imposes on a FUTURE image-build WO (not done here):**

- Pin the build to canonical SHA `adc21c4d` (immutable ref) from a clean context.
- Add an OCI **source-SHA label** and **build-UTC label** so *capability-present* is distinguishable
  from *capability-active* and from *which source built the image*. (See §20; the running image lacks a
  source-SHA label, which is part of the deployment-truth ambiguity.)
- Produce an **SBOM**, record the **image digest**, run **vuln**, **secret**, **prohibited-host-path**,
  and **package-inventory-vs-current-image** scans.
- Verify dependency-lock reproducibility (wheels only, no index).

**Proofs the readiness audit must produce (§14 Stage C):**
1. **Phase-2 present-but-inactive** — the 10 `hermes_sss_*` modules exist in the image.
2. **No active runtime import** — `grep` over the app root (`main.py`, `signal_builder.py`,
   `config.py`, `env_config.py`, `backfill_atr_metrics.py`, `adapters/`, `utils/watchdog.py`) shows no
   import of `hermes_sss_*` / `hermes_shared_stream_recovery`. Confirmed at base: only
   `tests/`, `design/`, and sibling `utils/hermes_sss_*` import them.
3. **No config default activates** — `ShadowAdapterConfig.shadow_enabled` defaults `False`;
   `shadow_consumer_live` is const `False`; `enabled` returns true only for explicit `True` + `False`.
4. **No JSONL path created at image start** — the writer's directory creation is lazy (`_ensure_dir`
   on first successful `append`); at import/start nothing touches the filesystem.

**Adequacy ruling.** The existing `Dockerfile` and compose are **adequate** for a present-but-inactive
deploy (the capability ships inert). The **only** recommended change is adding the source-SHA +
build-UTC labels; that is an impl-only follow-on (`FW-19`). This design does **not** modify the
Dockerfile/compose.

---

## §8 Passive runtime-wiring design (FUTURE impl, not done here)

**Runtime module(s) a future passive-wiring WO would touch:** `main.py` — specifically the assembly
region where `DstAwareMarketHours` (`main.py:1140`) and the `HermesWatchdog` are constructed and the
stream loop runs (`main.py` ~660-880: `retry_count`, `set_stream_state`, `record_tick`,
`record_candle_m1`, `consume_recovery_request`). No other file needs editing for a read-only observer;
an additive current-authority observation surface (§18) may require a small additive method on
`utils/watchdog.py` and/or `adapters/oanda.py` — identified, not implemented.

**Integration point & cadence.** A passive shadow-adapter invocation reads a **copied immutable
snapshot** of watchdog/adapter state and evaluates. Cadence is **hybrid**: a periodic tick at
~watchdog interval (`shadow_eval_cadence_sec`) plus **bounded event hooks** (dedup-windowed,
rate-capped via `callback_dedup_window_sec` + `max_callback_shadow_events_per_min`).

**Ownership, locking, snapshot boundary.** The observer owns no lock across any market-data or recovery
path. It takes an **immutable copy** of the needed fields at a single boundary and evaluates off that
copy. Callback duration budget **p99 <= 25ms**; on any overrun/failure it is isolated and dropped, never
retried in a loop, never propagated.

**Per-source mapping** (observation only): generation (connection generation, or *unavailable*),
current-authority observation (§18), heartbeat + shared-progress both = `adapters/oanda.py`
`_health.last_tick_at` (**same surface**, §9 collapse), socket state = `AdapterState`/watchdog
`stream_state`, auth/provider-fault, instrument-health = watchdog `_market_truth_checker` +
`_instrument_last_tick`/M1, limiter/cooldown observation, runtime identity, source SHA, config version.

**How it is MECHANICALLY passive (not merely intended):**
- The observer evaluates an **immutable copied view** — it cannot mutate watchdog/adapter state because
  it never holds a reference to the live mutable objects during evaluation.
- The only executor standing where a reconnect executor would be is **`RefusingShadowExecutor`**, which
  **raises `ShadowExecutionForbidden`** if anything asks it to execute — the impossibility is testable.
- **No reconnect handle is reachable** from the shadow path (reconnect-capability dependency scan is a
  blocking test). The shadow result is a **proposal-only** record; it never suppresses, permits, or
  alters a reconnect, never consumes the one-shot recovery request, never mutates the limiter/cooldown/
  generation, never reorders, never blocks.
- All failure modes (snapshot / map / compare / write) are isolated from current authority. Mode-C
  authority stays fully authoritative regardless of shadow outcome or shadow failure.

**Binding constraints:** read-only; no one-shot consume; no limiter/cooldown mutation; no reorder; no
block; no executor wrap/share; shadow result never alters reconnect; all failure modes isolated.

---

## §9 Same-source provenance design

`heartbeat` and `shared-progress` are two **interpretations of the same surface**: `adapters/oanda.py`
`_health.last_tick_at`. The design assigns a **single canonical provenance id** for that surface. The
evidence snapshot records the provenance id and the list of derived interpretations plus a `collapsed`
flag (see `schemas/deployment_readiness/phase2_jsonl_record.v1.schema.json`
`same_source_provenance`). Because both interpretations collapse to one provenance id, a single surface
**cannot self-corroborate** (two views of one tick are not two independent pieces of evidence).

- Connection generation is **associated-or-unavailable** (unavailable != false).
- **Future independent evidence** (e.g. a genuinely separate transport signal) can be added under a
  *new* provenance id without double-counting; the collapse guard rejects reusing the same id for a
  claimed-independent source.
- **Observability** exposes the collapse (the `collapsed` flag + provenance id are in every record).
- **Tests** must prevent *cosmetic relabelling* — renaming `last_tick_at` under a second key does not
  create a second provenance id.

---

## §10 Configuration design

Schema: `schemas/deployment_readiness/phase2_config.v1.schema.json`; disabled example:
`docs/design/deployment_readiness/examples/phase2_config.disabled.example.json`. Mirrors
`utils/hermes_sss_config_v1.ShadowAdapterConfig` (contract version `1`).

**Fail-closed semantics (grounded in the module):** `shadow_enabled` **defaults `False`**; any
absent/malformed/unknown-version config -> **DISABLED**; **only a JSON boolean `true`** enables (no env
string coercion — `'true'`/`'1'`/`'yes'` are wrong-type -> DISABLED); `shadow_consumer_live` is const
`False` and a `true` value is a validation error -> DISABLED; `consumer_live=true` can **never** activate.
The `enabled` property is the only gate the adapter consults and returns true **only** when
`shadow_enabled is True and shadow_consumer_live is False`. Heartbeat/shared-progress horizons are
**PROVISIONAL** (`horizons_provisional` const true; hard >= soft; out-of-range -> DISABLED). No secrets
(rejected by the redaction layer).

**Mount semantics (design, no `/etc`/host-global write here).** Config **CONTENT** lives in governed
`hermes_config` (DB) and/or a **mounted** project-owned file — **not embedded** in the image and **not**
read from `os.environ`/files by the module (the raw mapping is passed in by the future caller).
Externally inspectable. **Install is a SEPARATE WO** (`FW-06`); the config is validated **before**
container replace; deploy-with-disabled is possible; **enablement is a later independent mutation**
(`FW-13`).

---

## §11 JSONL storage hardening (N-1, N-2)

Grounded in `utils/hermes_sss_jsonl_writer_v1.py` and `utils/hermes_sss_redaction_v1.py`.

**N-1 (append not proven fully atomic).** Current `append()` does: redaction scan -> serialise
(`json.dumps(sort_keys, separators)` + `\n`) -> size bound -> `_ensure_dir` -> rotate-if-needed
(`os.replace` for the rename, which is atomic) -> `open(path,"ab"); fh.write(encoded)` -> chmod. It
**never raises** into the caller (OSError and a final `except Exception` are caught and returned as a
`WriteResult`). The gap: the append itself is a **buffered write with no fsync and no per-record
framing/checksum**, so a crash mid-write can leave a **partial tail line**. The hardening design
(follow-on `FW-01`, blocking before Stage F) adds:
- **Framing / partial-write detection**: each line is a complete canonical JSON object terminated by
  `\n`; a **read-side validator** treats a trailing non-parseable fragment as a recoverable partial tail
  and truncates/ignores it (corrupt-tail recovery), never counting it as a record.
- **Crash consistency & fsync policy**: define a bounded fsync policy (e.g. fsync-on-rotate always;
  fsync-on-append configurable) so durability is explicit, not accidental.
- **Rotation races / interrupted-rotation recovery**: rotation already uses atomic `os.replace`; the
  design adds detection of an interrupted rotation (active file larger than bound with a half-written
  companion checksum) and safe re-derivation.
- **Concurrent-writer prevention**: single-writer invariant (one observer instance) documented +
  enforced (advisory lock / single-owner), so no concurrent appenders interleave.
- **Disk-full / bounded failure**: already returns `io_error:*` without a retry loop; kept.
- Ruling: **incremental harden** (not replace) — the existing writer's isolation and rotation are
  sound; N-1 adds framing/detection/fsync policy on top.

**N-2 (redaction covers string values only).** Confirmed: `_walk` only calls `_value_violation` on
`isinstance(node, str)`; "numbers / bools / None carry no secret surface" and **bytes/bytearray/int
identifiers are not scanned**. The hardening design (follow-on `FW-02`, blocking before Stage F):
- **Fixed closed record schema is the PRIMARY defence** — the JSONL record shape is closed
  (`additionalProperties:false`, enumerated fields) so arbitrary raw payload objects cannot reach the
  sink. Producers construct the record from known typed fields, not from raw provider/SDK objects.
- **Scan is defence-in-depth** extended to non-string values:
  `bytes/bytearray -> decode-and-scan or reject`, `int identifiers -> pattern/whitelist`, `list/tuple`,
  `mapping`, `dataclass -> asdict`, `enum -> .value`, encoded strings (base64/hex heuristics), nested,
  and **raw-payload/provider-SDK objects -> reject** (never serialise an opaque object).
- Blocking tests over the full type matrix (`bytes/int-id/list/tuple/mapping/dataclass/enum/encoded/
  nested/raw-obj/account-ids/DSNs/tokens/bearer/provider-SDK`) must pass **before activation**.

---

## §12 External evidence accessibility

Design (do **not** install/create here): a **project-owned host mount** -> **container mount path**
(the config's `evidence_output_path`, e.g. `/data/shadow_stream_recovery/`). Ownership matches the
container's non-root `hermes` (UID/GID 10001) with restrictive perms; rotation + retention +
`max_retained_files` + quota; per-rotated-file `.sha256` checksum access; operator-inspectable;
read-only for any consumer; backed up with the evidence policy; **evidence is distinct from logs**;
fail-loud capacity alert on disk-full. **No boot-critical remote mount, no hidden host-global, no
secrets, no Redis, no SQL.** Orchestrator/cloud-volume compatible. Provisioned by `FW-07`.

---

## §13 Performance-validation design

Three distinct measurements (do **not** certify here):
1. **Pre-deploy synthetic** — micro-benchmark snapshot/mapper/decision/comparison/JSONL-construct/
   write/rotation/writer-failure/concurrency with a **monotonic clock**, **UTC evidence**, warm-up,
   documented sample size, reporting p50/p95/p99/max, CPU/mem, event rate, failure-injection.
2. **Deployed-disabled overhead** — the disabled adapter must add ~zero overhead (it is not invoked).
3. **Shadow-enabled operational** — with the observer running, prove **watchdog-coexist** and
   **callback-coexist** non-interference and **callback p99 <= 25ms**.

**Fail conditions:** p99 > 25ms; any measurable interference with watchdog/market-data timing; a write
path that blocks. Gate `FW-05`.

---

## §14 Deploy-dark sequence (Stages A-H)

Machine-readable: `models/deployment_stage_model.v1.json`. Summary:

- **A Cumulative readiness verify** — shadow off. Verify classification/import-graph/build-context.
- **B Image build only** — shadow off. Reproducible build from `adc21c4d`, labels, SBOM, scans. No container replaced.
- **C Deployment-readiness audit** — shadow off. Prove present-but-inactive, no import, disabled default, no JSONL at start.
- **D Deploy-dark** — shadow off. **DEPLOYMENT**: container replace + disabled mounted config; validate health/market-data/authority; rollback window.
- **E Post-deploy-dark audit** — shadow off. Confirm inert in-container, no JSONL, authority unchanged; **N-1/N-2 closed** (blocking gate).
- **F Separately-authorised shadow-enable** — **shadow ON**. **ACTIVATION** (config-only, no image change, explicit UTC start, bounded, disable path, zero reconnect authority).
- **G Live shadow observation** — shadow ON. Bounded evidence collection.
- **H Post-observation R2D2 audit** — shadow off (resting). Terminal. **No Phase-3 cutover.**

**Deploy != Activate.** Stage D deploys the capability *disabled*; Stage F activates it via a separate,
independently-authorised config-only mutation. They are distinct stages with distinct gates.

---

## §15 Rollback design

Machine-readable: `models/rollback_state_machine.v1.json`. Fifteen failure modes (F01-F15), each with
an independent plan. Requirements: exact prior image digest **`c5fc2a62f424`**, exact prior source SHA
**`71ea3bd`**, prior-config preservation, atomic governed config replace, **documented-not-executed**
container commands, max decision window (15 min), evidence preservation (never delete failure
evidence/shadow JSONL), post-rollback verify (health/market-data/restart-count/config-v3), restart-count
recording, UTC, R2D2 post-rollback audit, **no Redis/SQL**.

- **Immediate image rollback** (touch health/market-data/reconnect/leak): F04, F05, F06, F07, F08, F09.
- **Shadow-disable-only** (config-only, no image rollback; reconnect never depended on shadow): F11-F15.
- **No-deploy / no-activate** (abort before the risky step): F01, F02, F03, F10.

---

## §16 Operational observability

Expose shadow state via a **bounded project-local status file / health endpoint / mounted surface /
existing logging** — comparison-class counts, last-record UTC, same-source collapse flag, writer
health, config version, source SHA, enabled flag. **No Redis/SQL** unless a separate architectural
decision authorises it. Not implemented here (`FW-12`).

---

## §17 Reconnect-authority safety contract

Restated invariants, each mechanically enforced by the passive design:
1. Instrument RED **!=** transport failure; instrument-health stays fail-loud.
2. Stale may raise an incident / **propose**, but **not authorise** reconnect.
3. Socket connected **!=** healthy.
4. Same-source cannot self-corroborate (§9 collapse).
5. Unavailable **!=** false.
6. Inference **!=** observation.
7. Proposal-only **!=** authority.
8. Shadow **cannot affect** current authority.
9. Only genuine governed **transport** evidence may **eventually** authorise reconnect — never shadow.
10. Provider-disconnect bypass is **narrow + reason-coded**.
11. No caller emergency flag.
12. No shadow executor / reconnect-handle (`RefusingShadowExecutor` raises).
13. No output is an executable command.

Any wiring that weakens any invariant is **invalid** and blocks the programme.

---

## §18 Current-authority observation design

The current authority consumes a **one-shot** recovery request: `main.py:731`
`state.watchdog.consume_recovery_request()`. Observing the outcome must **not** consume/acknowledge/
mutate that one-shot, nor touch limiter/cooldown/generation/reorder/latency, nor wrap/intercept, nor
change its lifetime.

**Design.** Where the outcome is not already observable via existing immutable state, add an
**additive passive observation surface**: an additive method on `utils/watchdog.py` (and/or
`adapters/oanda.py`) that **exposes a COPY** of the last recovery decision (outcome, reason code,
connection generation, request/proposal identity, UTC) as **immutable records**, **without**
consuming/acknowledging the one-shot and **never** becoming a second authority. Exact future file
changes: `utils/watchdog.py` (additive read-only accessor recording each `consume_recovery_request`
outcome into a bounded immutable ring) and the observer in the new wiring (`FW-03`/`FW-04`). Not
implemented here.

---

## §19 PR103-PR111 dependency matrix

Machine-readable: `models/pr_dependency_matrix.v1.json`. Hard prereq chain `108 -> 109 -> 110 -> 111`;
PRs 103-107 orthogonal. **No hidden dependency or incompatibility.** `requirements.txt` unchanged.
**Cumulative deploy is SAFE** — no PR forces a blocker. Only PR103 needs post-build config-version
validation (no-op on v3); PR111 wants a source-SHA label (version-reporting) but that is not blocking.

---

## §20 HELM state / deployment-truth correction design (future WO, not mutated here)

**The ambiguity.** The running image `c5fc2a62f424` (from source `71ea3bd`, the Mode-C deploy) does
**not** contain PR103-111. The R2D2 blueprint field `deployed_runtime: f69df68 (blob-proven)` is
**STALE** — `f69df68` was the *pre-Mode-C rollback* source, not the running source `71ea3bd`.

**Correction design (append-only; no history rewrite; not executed here):**
- Distinguish **current-runtime** (running image digest + its source SHA) from **prior-deploy-events**
  (a dated append-only log).
- Distinguish **canonical-SHA** (`adc21c4d`, the candidate) from **deployed-SHA** (`71ea3bd`, running).
- Distinguish **capability-deploy** (image contains capability, disabled) from **capability-activation**
  (shadow enabled).
- Record scope by PR / WO.
- **Avoid** a loose `canonical_deploy_divergence=false` when the SHAs differ — they *do* differ
  (`adc21c4d` != `71ea3bd`), so divergence is *true* until a new image is deployed.
- Set clear **current pointers**: `running_image_digest=c5fc2a62f424`, `running_source_sha=71ea3bd`,
  `candidate_base_sha=adc21c4d`; correct/retire the stale `deployed_runtime=f69df68`.
- HELM-owned, R2D2-auditable, append-only. Implemented by `FW-16`. **Not mutated in this WO.**

---

## §21 PR#108 fabric anomaly assessment

The missing **dated** PR#108 R2D2 audit fabric key does **not** compromise deployment-evidence
completeness: alternative provenance exists — the git merge (`ae80d113`) and its evidence bundle, the
`:latest` audit pointer, and the mirrored audit record together establish that PR#108 landed and was
inert design. Deployment readiness rests on **canonical code + build-context + import-graph proofs**,
not on that one fabric key. **Repair is a separate CA-authorised process (`FW-17`); not done here.**

---

## §22 Test & acceptance matrix

Machine-readable: `models/acceptance_matrix.v1.json` (~25 categories; per test: lifecycle stage, owner,
input, expected, blocking-failure, evidence). Only `T-DESIGN` and `T-NODE-DIFF` are executed in this
design PR (`implemented_here:true`); all others are specified for the future programme.

---

## §23 Required follow-on WOs

Machine-readable: `models/future_wo_sequence.v1.json` (19 WOs). Governance: safe coherent impl WOs may
combine, but **merge + deploy + activation are NEVER combined**. Parallelisable set (N-1, N-2,
observation-surface, HELM-state, PR108-repair, gate-wiring, SHA-label) precedes the sequential gate
chain (merge -> image-build -> readiness-audit -> deploy-dark -> post-deploy-dark-audit -> shadow-enable
-> live-observation -> post-observation-audit).

---

## §24 Quality, diagrams, and doctrine

Production-owned, versioned (`v1`), UTC-dated (2026-07-18), explicit assumptions/provisional-values/
unavailable-evidence, no secrets, no embedded env config, container-oriented, externally-inspectable,
R2D2-auditable. Diagrams below.

### Data-flow sequence (passive shadow observation)

```
watchdog/adapter live state
        │  (single boundary: immutable COPY)
        ▼
   snapshot ──► mapper ──► shadow decision (RefusingShadowExecutor stands in for executor)
                                   │
                                   ▼
                            comparator (10 classes) ──► JSONL record (closed schema, redaction)
                                                              │
                                                              ▼
                                                    mounted evidence path (lazy dir, append-only)

current Mode-C authority: consume_recovery_request()  ── UNTOUCHED, fully authoritative
        │  (observed via additive immutable-copy surface, one-shot NOT consumed)
        ▼
   current_authority_observation (COPY) ──► into the same JSONL record
```

### Deployment state machine

```
A verify → B build → C readiness-audit → D deploy-dark(off) → E post-audit(off, N1/N2 gate)
   → F shadow-enable(ON, config-only) → G observe(ON) → H post-audit  [TERMINAL — no Phase-3]
```

### Rollback state machine

```
STEADY_DEPLOY_DARK ──immediate(F04-F09)──► ROLLBACK_IMAGE ─restore c5fc2a62f424─► VERIFY ─► AUDITED
STEADY_SHADOW_ENABLED ──disable-only(F11-F15)──► ROLLBACK_SHADOW_DISABLE ─config-off─► VERIFY ─► AUDITED
(pre-step: F01/F02/F03/F10 → abort before deploy/activate)
```

### Failure matrix / config schema / storage schema / PR-dependency / acceptance / future-WO

See `models/rollback_state_machine.v1.json`, `schemas/deployment_readiness/phase2_config.v1.schema.json`,
`schemas/deployment_readiness/phase2_jsonl_record.v1.schema.json`,
`models/pr_dependency_matrix.v1.json`, `models/acceptance_matrix.v1.json`,
`models/future_wo_sequence.v1.json`.

> No statement in this document claims the Phase-2 capability is deployed, live, or active.
```
