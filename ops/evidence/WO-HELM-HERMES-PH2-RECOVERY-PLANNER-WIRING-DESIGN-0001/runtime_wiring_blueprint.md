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

## D. Market-closure acquisition — DESIGN
Separate five closure kinds: regular session, exceptional holiday, ad-hoc closure, broker maintenance, unknown/unverified.
- **HERMES owns the deterministic regular schedule** via policy `regular_weekend_closure_utc` (XAU_USD: **Fri 22:00 → Sun
  22:00 UTC**). D1 anchor **22:00 / NY5PM** preserved; 00:00 remains a boundary defect.
- **Exceptional closures are separately governed external evidence** (`MarketClosureSnapshot` with `authority` in the policy's
  accepted set). **No guessed holidays**; uncertain intervals stay `UNCLASSIFIED_MARKET_STATE` (warn). **No vendor call**, **no
  ARES import**, **no ARES behavioural assumption**. If ARES later publishes a market-event contract, HERMES may consume it
  **only** through an explicit cross-application governance step (future WO) — never by importing ARES code.
- Contract fields: source, authority, provenance, version, effective_at_utc, staleness; conflict handling = governed authority
  wins, ties → warn + UNCLASSIFIED; fallback = regular schedule only; blocking vs warning per policy.

## E. Planning-policy acquisition — DESIGN
`RecoveryPlanningPolicy` (+`CostModel`) supplied **explicitly, externally, versioned**. No config-in-code; no material default in
source. Recommended: a **governed external policy contract** (a versioned JSON policy document mounted/loaded via the governed
config mechanism, or a governed control-plane policy key — decided in the wiring WO), with: schema + `contract_version`,
ownership (HELM), validation, reload behaviour, change-control, external accessibility, **secret-free**, **default-deny** when
missing/invalid (→ `BLOCKED_POLICY`, planner idle), `policy_digest` (already implemented as `policy.digest()`), full
auditability. **No actual policy values are added in this WO.**

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

## L. Health/observability — DESIGN-ONLY (see `candidate_health_schema.json`)
A **dedicated** planner health surface (candidate `hermes:recovery:planner_health:XAU_USD:v1`) or control-plane telemetry —
**do NOT overload `hermes:backfill:status:XAU_USD:v1`** (that describes recovery readiness, not planner runtime health). Fields:
enabled/authorised, last_invocation_utc, last_success_utc, source digests, planner/policy versions, last_status, last_fault,
consecutive_failures, invocation_duration_ms, output_disposition, `execution_enabled=false` always. Not created in this WO.

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

## Unresolved questions
1. Authoritative source for **beyond-retention** coverage completeness (recommend a separate coverage-truth surface).
2. Whether exceptional-closure evidence will come from an ARES market-event contract (requires cross-app governance) or a
   HERMES-owned closure contract.
3. Final external-policy transport (mounted file vs governed control-plane key).
4. Whether planner health lives in a dedicated key vs control-plane telemetry.
These are ADR-tracked (see `docs/adr/`) and must be resolved in the wiring WO before implementation.
