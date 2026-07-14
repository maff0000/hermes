# HERMES PH2 Recovery Planner — Runtime Wiring Blueprint (DESIGN-ONLY)

**WO:** `WO-HELM-HERMES-PH2-RECOVERY-PLANNER-WIRING-DESIGN-0001` · **Mode:** DESIGN-ONLY · **Base:** `0a7d567`

> This document defines **how** the deployed-dark deterministic planner (`utils/hermes_recovery_planner_v1.py`) could later
> be invoked safely inside HERMES. It implements nothing, changes no runtime, writes no Redis key, and touches no gate.

## 0. Accepted current state (verified by value)
Planner deployed **dark** at `0a7d567`: module present + importable + side-effect-free; gates unset (`DISABLED`); no runner,
no adapter, no publisher, no executor, no proposal key; `consumer_live=false`. PH2 pair live: `hermes:gaps:XAU_USD:v1`
(`GAPS_FOUND`), `hermes:backfill:status:XAU_USD:v1` (`READY_FOR_BACKFILL_DESIGN`). PH1 D1 runtime-complete.

**Subsystem distinction (mandatory):** the NEW planner is `utils/hermes_recovery_planner_v1.py`
(`build_recovery_proposal` / `ProposedWorkload`). The PRE-EXISTING `utils/recovery_planner.py` + `utils/recovery_executor.py`
(OANDA startup-backfill "recovery library", classes `RecoveryLibrary`/`RecoveryPlanner`) are a **separate** legacy subsystem.
This design keeps them strictly apart (see §N and `subsystem_separation.md`).

## A. Runtime caller — DECISION
**Recommended: a dedicated `recovery_planner` runner inside the existing `HermesPublisherSupervisor`, running plan-only with
component-isolated failure and digest-gated cadence.** (Caller model #2, adapted.) See `caller_options_matrix.md` for the full
comparison. Rationale: it reuses the proven per-runner-thread isolation already used by gaps/backfill_status (a fault in one
runner does not stop the others — `HermesPublisherSupervisor.fault_summary`), runs in-container (Docker-absorbed, no host
cron/systemd), and is trivially observable. It diverges from the existing surface pattern in one deliberate way — **gate
failure is component-level, not whole-container** (see §F).

- **Component name:** `recovery_planner` runner (step `recovery_planner_step`) + a thin caller module,
  e.g. `utils/hermes_recovery_planner_runtime_v1.py` (a FUTURE wiring WO; not created here).
- **Ownership:** HERMES / HELM lane. **Import:** MUST be the fully-qualified `import utils.hermes_recovery_planner_v1 as rp`.
  Never the bare `recovery_planner`.
- **Lifecycle:** constructed during supervisor assembly ONLY when both gates true; runs in its own supervisor thread; graceful
  stop on shutdown; on crash the supervisor restarts only that runner (existing behaviour). **Startup order:** after the
  existing publisher runners (it depends on gaps being published). **Runs in the existing HERMES container** and **shares the
  existing supervisor** (thread isolation), but does NOT share code/gates/keys/config with any other runner or the legacy subsystem.
- **Why safer than alternatives:** a dedicated runner avoids coupling the planner to any existing publisher's cadence/failure
  domain (rules out #1); event-only (#3) risks missed triggers if the gaps event bus doesn't exist; a bare timer (#4) recomputes
  needlessly (mitigated here by digest idempotency); manual-only (#5) can't provide continuous health; a sidecar/process (#6)
  adds operational + deployment complexity and breaks single-container absorption. All models remain plan-only in this design.

## B. Gaps input acquisition — DESIGN
- **Key (read-only, GET):** `hermes:gaps:XAU_USD:v1`. The pure planner stays Redis-free; the RUNNER performs the GET.
- Validate: `contract_version==v1`; `instrument==XAU_USD` (reject `XAUUSD`); `overall_gap_state` in the gaps vocab;
  `d1_boundary` present; `source_generated_at_utc` parses UTC. Expected TTL doctrine: gaps key is **persistent (-1)**; freshness
  is judged by `generated_at_utc`, not TTL. **Max accepted age:** `2 × gaps_publish_interval` (gaps cadence = 60s → 120s).
- Behaviours: absent → planner status `BLOCKED_INPUT` (skip, warn, retry next tick, no output); invalid → `BLOCKED_INVALID_INPUT`;
  stale (age > max) → `BLOCKED_STALE_GAPS`; Redis unavailable → component fault + backoff retry (never blocks other runners);
  `overall_gap_state` change → treated as a new digest → recompute. **Never mutates the gaps key.**

## C. Existing-coverage acquisition — DESIGN (honesty-critical)
**Key risk (WO §C):** the governed Redis candle histories are a **rolling retention window** (M1–H4 = 35 days, D1 = 120 days),
NOT a complete historical record. Feeding that raw as `ExistingCoverageSnapshot` would make the planner treat pre-retention
candles as "missing" when they are merely aged out.

**DECISION (see `coverage_completeness_analysis.md`):**
1. **Bound the planning window to the governed retention window.** The planner only reasons within retention, where the
   governed history index is authoritative-enough for *presence* (forward writer + governed backfill maintain it). Anything
   older is `OUT_OF_RETENTION` — already the gaps surface's doctrine — and is **excluded, not planned**.
2. The `ExistingCoverageSnapshot` for v1 is derived **read-only** from the governed per-tf history index
   (`hermes:candles:XAU_USD:{tf}:history:v1:index`) with an explicit `provenance="governed_redis_history_index"`,
   `retention_window` field, and a hard `completeness="RETENTION_BOUNDED"` marker — so downstream can never mistake it for
   full-history completeness.
3. **Do not allow a partial retention window to masquerade as complete coverage.** If planning beyond retention is ever
   required, a **separate coverage-truth surface WO** must establish completeness — this design recommends that rather than
   inventing it. D1 history floor = policy `d1_history_floor_seconds` (governed, ≈120d). M1–H4: within 35d retention the index
   is sufficiently complete for safe *within-window* planning; beyond it, insufficient — do not plan.

## D. Market-closure acquisition — DECISION (Option A, FROZEN for the first wiring implementation)
**Binding decision: the FIRST wiring implementation consumes ONLY the HERMES-owned deterministic regular XAU_USD schedule
(Friday 22:00 UTC → Sunday 22:00 UTC).** It does NOT consume ARES exceptional-closure data, vendor holiday calendars, broker
maintenance calendars, manually inferred holidays, or any ungoverned exceptional-closure source. **The first implementation has
no ARES dependency, performs no ARES import, assumes no ARES contract, and contacts no vendor.**

- **HERMES owns the deterministic regular schedule** via policy `regular_market_schedule` (XAU_USD: **Fri 22:00 → Sun 22:00
  UTC**). Regular-closure periods → `INTENTIONALLY_UNAVAILABLE`. D1 anchor **22:00 / NY5PM** preserved; 00:00 remains a boundary defect.
- **Exceptional or suspected closures without accepted governed evidence → `UNCLASSIFIED_MARKET_STATE`.** The planner MUST NOT
  classify them `INTENTIONALLY_UNAVAILABLE`, MUST NOT guess a holiday, and MUST NOT silently discard the gap. An unresolved
  exceptional-closure intersection with proposed segments yields the blocking status **`BLOCKED_UNCLASSIFIED_MARKET_STATE`** — the
  affected planning scope **cannot** become `PROPOSAL_READY`; the period is preserved for later review under a stable warning/fault code.
- **Exceptional-closure transport is a separate prerequisite DESIGN WO** — `WO-HERMES-PH2-EXCEPTIONAL-MARKET-CLOSURE-CONTRACT-DESIGN-0001`
  — which must decide contract ownership, HERMES-vs-external-publisher responsibility, schema, authority, provenance, freshness,
  conflict resolution, transport, fallback, and cross-application governance. This is **NOT** decided in the wiring implementation
  WO. Until that contract exists, exceptional periods stay unclassified/blocking. (ADR-0004.)

## E. Planning-policy acquisition — DECISION (governed mounted JSON, FROZEN)
**Binding decision: the first runtime wiring loads `RecoveryPlanningPolicy` (+`CostModel`) from a READ-ONLY JSON file mounted
into the HERMES container from externally governed configuration, at the exact canonical container path
`/app/config/recovery_planner_policy.v1.json`.** No other transport may be invented by the implementation.

- **Forbidden transports:** `/etc`, Redis, SQL, environment-embedded JSON, control-plane API, vendor API, shared application
  config, ARES config, host-global hidden files. Config-in-code and material source defaults remain forbidden.
- **Schema/version/ownership:** the full JSON Schema is `candidate_policy_schema.json` (evidence); `contract_version="1"`,
  `instrument="XAU_USD"`, timeframe vocab `{M1,M5,M15,H1,H4,D1}`, required + optional fields, numeric bounds, UTC conventions,
  canonical digest (`policy.digest()` over the canonical policy dict). Ownership HELM. Read-only mount; secret-free.
- **Load/validation phase:** read + validate at runner initialisation. **Default-deny** — missing file / invalid JSON / schema
  failure / unsupported version / non-`XAU_USD` instrument / invalid D1 anchor or floor → the planner component is
  **BLOCKED/disabled** with a stable fault code (`POLICY_FILE_MISSING`, `POLICY_JSON_INVALID`, `POLICY_SCHEMA_INVALID`,
  `POLICY_VERSION_UNSUPPORTED`, `POLICY_INSTRUMENT_INVALID`, `POLICY_DIGEST_FAILED`). **No fallback to code defaults; no fallback
  to stale cached policy** (unless a separate governance decision authorises last-known-good). The planner may not produce
  `PROPOSAL_READY` without a valid policy.
- **Reload doctrine:** policy read at runner init; a periodic metadata/digest check may detect an **atomic replacement**; only a
  fully valid replacement becomes active; an invalid replacement does **not** silently replace the last valid policy and is
  reported in planner health; **a present-but-invalid policy change blocks new planning cycles until corrected** (preferred
  fail-closed). Stale policy content must never be represented as newly valid. Dev/prod parity: same canonical path + schema.
- **No actual policy values are installed in this WO** (schema + fixtures are documentation only). (ADR-0011.)

## F. Gate failure domain — DECISION (see `gate_failure_domain.md`)
Gates: `HERMES_RECOVERY_PLANNER_ENABLED`, `HERMES_RECOVERY_PLANNER_AUTHORISED`. Truth table (already implemented):
F/F→DISABLED · F/T→DISABLED · T/F→`SystemExit(105)` fail-closed · T/T→plan-only.

**DECISION: component-level fail-closed, NOT whole-container.** A non-critical planning feature must not crash the HERMES
market-data spine (tick/candle/quote/feed-health/gaps/backfill). The wiring WO evaluates the gate **inside the planner runner's
own construction/first-tick**, converting enabled-without-authorised into a **loud, binding planner-component fault** (fault code
`GATE_FAILCLOSED_105`, planner disabled, health surface RED, log ERROR) — while the supervisor and all critical runners keep
running. This is a **deliberate divergence** from the gaps/backfill surfaces (whose `*_publish_enabled()` raise `SystemExit(105)`
during `default_runner_specs()` assembly and would abort boot): those are governed data surfaces; the planner is advisory. The
`105` code remains visible and binding at the component level. Whole-container exit would only be chosen if governance explicitly
mandates it — and this design does **not** recommend it (risk analysis: crashing tick/candle/quote/feed-health because an
advisory planner is misconfigured is disproportionate and unsafe). No gate change is made in this WO.

## G. Cadence & idempotency — DECISION (see `cadence_and_idempotency.md`)
**Hybrid, digest-gated.** The runner ticks on the supervisor interval (60s) but **recomputes only when the idempotency tuple
changes**: `(gaps_payload_digest, coverage_snapshot_digest, closure_snapshot_digest, policy_digest, planner_version)`. Unchanged
tuple → no work (return cached status). This avoids continuously recomputing an identical proposal (deterministic `proposal_id`
already excludes the clock). Debounce on rapid gaps refreshes; skip recompute when market-closed and nothing to plan; single
in-process invocation (mutex) prevents concurrent/duplicate runs; long calls are bounded by the planner's own workload limits.

## H. Staleness & freshness — see `staleness_matrix.md`
Per-input max age, block behaviour, fault code, observability. **No stale-as-live.** A proposal from stale/incomplete inputs is
**never** `PROPOSAL_READY` — it is `BLOCKED_STALE_GAPS` / `BLOCKED_STALE_COVERAGE` / `BLOCKED_CLOSURE_TRUTH` / `BLOCKED_POLICY` /
`BLOCKED_INPUT_INCONSISTENCY`.

## I. Failure containment — see `failure_mode_matrix.md`
The caller **must never** block ticks/candles/quotes/feed-health, alter gaps/backfill truth, trigger repair, delete keys, write
SQL, call a vendor, or invoke an executor. Containment: bounded retry with exponential backoff, max-retry → fault state,
heartbeat, alert threshold, anti-spam (dedupe identical faults), operator visibility, automatic recovery once inputs are valid.
A planner crash restarts only that runner and **cannot make market data stale**.

## J. Output disposition — DECISION (see `output_disposition_decision.md`)
**Phase-1 wiring: IN-MEMORY result + structured health telemetry ONLY. No publication.** Proposal publication requires its own
**publication-contract WO**; persistence requires its own governance. Explicit truths: a proposal **is not** an execution
request; a published proposal **must not** be auto-consumed by an executor; any publication key needs schema/TTL/freshness/
consumer governance; **no executor may infer authorisation from proposal existence.**

## K. Candidate proposal contract — DESIGN-ONLY (not created; see `candidate_proposal_schema.json`)
Candidate key `hermes:recovery:proposal:XAU_USD:v1`; **TTL-bound (positive TTL, fail-loud expiry ≈ `2 × cadence`)** — a stale
plan is dangerous, so persistence is rejected by default. Exact-one-key invariant; fixed truths `execution_enabled=false`,
`backfill_executed=false`, `repair_executed=false`, `consumer_live=false`, no active job, no completion %, no executable payload.

## L. Immediate planner health — DECISION (Option A, FROZEN: logs + supervisor state only)
**Binding decision: the first wiring implementation publishes NO Redis planner-health key.** It uses ONLY structured UTC logs,
existing internal supervisor runner state, and the process-local component status the supervisor framework already provides. It
**must not** create `hermes:recovery:planner_health:XAU_USD:v1` or any alternative planner-health Redis key, **must not** write
planner status into `hermes:backfill:status:XAU_USD:v1`, and **must not** overload feed_health / gaps / backfill_status /
instrument_catalogue / any existing contract.
- **Immediate observability fields (logs + supervisor state):** component name, planner version, gate state, policy version,
  policy digest, last invocation UTC, last success UTC, source digests, current planning status, last fault code, consecutive
  failure count, invocation duration, retry/backoff state, `execution_enabled=false`, `publication_enabled=false`,
  `consumer_live=false`.
- **Log content is SUMMARY-ONLY** — proposal_id, status, segment count, deferred count, unclassified count, intentionally-
  unavailable count, estimated_request_units. Full/sensitive proposal segment contents MUST NOT be dumped into logs.
- Any future Redis planner-health contract is a **separate publication-contract WO** —
  `WO-HERMES-PH2-RECOVERY-PLANNER-HEALTH-CONTRACT-DESIGN-0001` — which must define key, schema, TTL, freshness, publication
  gates, exact-one-key invariant, failure handling, consumer governance. This is **not** a choice left to the wiring
  implementation. (ADR-0012; `candidate_health_schema.json` is retained as DESIGN-ONLY for that future WO, not the first impl.)

## F.1 Component-level fault 105 — concrete mechanism (consistency)
`ENABLED=true` + `AUTHORISED=false` → terminal **planner-runner** fault code `105`: the planner runner does **not** invoke the
planner; the runner is marked failed/blocked; **all other critical HERMES runners remain active**; the supervisor exposes the
planner component fault; structured logs record the UTC fault; the planner does **not** silently fall back to a "disabled
healthy" state. Correction requires governed gate repair + component restart/reload per the accepted lifecycle. **This is never
converted into whole-container exit `105`.**

## M. Startup & lifecycle — DESIGN
Startup after publisher runners; wait for gaps-key availability (readiness) with an initial delay; in-memory digest-dedup +
mutex prevent duplicate invocation; graceful cancellation on shutdown; on restart the planner **recomputes** (does not trust a
prior in-memory/published proposal); planner failure never affects live market-data freshness.

## N. Pre-existing subsystem separation — see `subsystem_separation.md`
Fully-qualified imports only; no shared classes/executor/gates/Redis keys/status/config; no fallback new→old; no automatic
handoff; never invoke `RecoveryLibrary` or the pre-existing executor. Recommendation: the legacy subsystem should later be
**isolated into a legacy package + deprecated via a separate discovery/removal WO** — **not touched here**.

## O. Executor boundary — see `executor_boundary.md`
Hard 6-stage separation: planner → publication → operator approval → executor → verification → recovery-state update. A future
executor cannot execute merely because a proposal exists; needs separate execution gates, explicit job identity, approved
provenance, bounded segments, idempotency, rollback/abort doctrine, recorded writes, result validation, honest status; it may
**never** mutate gaps truth to hide failures. **No executor code is authorised.**

## P. Security & access
Least privilege: read gaps + policy only; no SQL write; no Redis write until a publication WO authorises exactly one key; no
vendor credential; no shell; no executor permission. Secrets policy: none in code/evidence; log redaction; provenance integrity.

## Q. Docker absorption
Config/policy mounted/supplied externally (secret-free); runner lifecycle entirely in-container; **no host cron/systemd/`/etc`**;
health exposed in-container; reproducible build; dev-host behaviour maps to cloud production unchanged.

## R. Test strategy — see `failure_mode_matrix.md` (test column) — full required future-test list enumerated there.

## S. Rollout sequence — see `rollout_sequence.md` (12 gated stages; not collapsed).

## Unresolved-question register — BINDING dispositions (no item is "decided in the wiring WO")
| Question | Binding disposition |
|---|---|
| Beyond-retention coverage truth | **Retention-only initial boundary**; a separate future **coverage-truth WO** establishes beyond-retention completeness. |
| Exceptional-closure source | **Regular schedule only** (Option A); exceptional periods → `UNCLASSIFIED` / `BLOCKED_UNCLASSIFIED_MARKET_STATE`; separate **`WO-HERMES-PH2-EXCEPTIONAL-MARKET-CLOSURE-CONTRACT-DESIGN-0001`**. |
| External-policy transport | **Governed read-only mounted JSON** at `/app/config/recovery_planner_policy.v1.json` (frozen; no alternative). |
| Planner-health behaviour | **Structured logs + supervisor state only**; **no Redis health key**; future health key via **`WO-HERMES-PH2-RECOVERY-PLANNER-HEALTH-CONTRACT-DESIGN-0001`**. |

The wiring implementation WO must contain **no architecture-choice branch** for these four matters — they are frozen here.
