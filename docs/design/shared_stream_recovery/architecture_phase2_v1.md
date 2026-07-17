# HERMES Shared-Stream Recovery — Phase-2 Shadow Adapter & Evidence Contract (Architecture v1)

**WO:** WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE2-SHADOW-ADAPTER-AND-EVIDENCE-CONTRACT-DESIGN-0001
**Authority:** HELM (HERMES market-data lane) · **Status:** DESIGN-ONLY / INERT / NOT WIRED / NOT FOR DEPLOYMENT / NOT FOR MERGE
**Created (UTC):** 2026-07-17 · **Base:** canonical `main` `8355044b` (Phase-1 pure core merged: `utils/hermes_shared_stream_recovery_v1.py`)
**Deployed ref:** image `c5fc2a62f424` (`71ea3bd`). The shadow adapter, when built, feeds truthful LIVE evidence to the **unchanged** Phase-1 core and produces a comparable SHADOW decision. It changes **no** reconnect behaviour.

> Design + inert schemas/fixtures/interfaces + a pure OFFLINE replay harness. No runtime code is added, imported,
> or executed. Proven inert by grep + `tests/test_sss_phase2_shadow_design_v1.py::test_static_guard_no_runtime_imports_phase2_design`.

---

## 0. Scope, non-negotiable boundary, and the one insight

The Phase-1 core is a **pure** function `decide(RecoveryDecisionInput) -> DecisionEnvelope`. Phase-2's job is to
build, from **real LIVE surfaces**, a truthful `RecoveryDecisionInput`, call the **unchanged** core, and record a
**shadow** decision that is **directly comparable** to what the current sustained-red (`sustained[0]`) authority
did — **without touching that authority, without emitting a reconnect, and without adding a runner**.

**The insight this stands on (July-16):** the adapter bumps `AdapterHealth.last_tick_at` on **every** provider
message — `HEARTBEAT` (~5 s cadence) *and* `PRICE`. So a **heartbeat-freshness** signal exists that is
**independent of any single instrument's freshness**. Heartbeats keep flowing through the metals break while
`XAU`/`SPX500`/`WTICO` prices stop — transport is provably alive while some instruments are quiet. The current
authority never consulted it; the shadow does.

**Hard boundary (mechanical, not aspirational):** the shadow layer has **no** `disconnect()`/`connect()` handle,
sets **no** recovery-request flag, and terminates at an **immutable record** whose `shadow_only` is always `True`
and `consumer_live` always `False`. The only object standing where an executor would be is a
`NoOpShadowExecutorBoundary` that **raises** if asked to act (§16).

---

## 1. Evidence-source inventory — the 21 `RecoveryDecisionInput` fields

Traced from canonical `8355044b`. Per field: source module/function · owner · representation · clock · freshness ·
availability class (§2) · authoritative-vs-advisory · directly-observed-vs-inferred · read-only-safe. HERMES owns
every surface. **No field is invented; where the code has no source, the field is UNAVAILABLE (§2), never defaulted
healthy.**

| # | Field | Source module → symbol | Repr | Clock | Freshness basis | Availability | Auth/Adv | Observed/Inferred |
|---|---|---|---|---|---|---|---|---|
| 1 | `evaluated_at_utc` | snapshot builder (`datetime.now(UTC)` at capture) | tz-aware UTC | UTC wall | instant of eval (injected) | AVAILABLE_AUTHORITATIVE | auth | DIRECTLY_OBSERVED |
| 2 | `provider` | constant `"OANDA"` | str | n/a | n/a | AVAILABLE_AUTHORITATIVE | auth | DIRECTLY_OBSERVED |
| 3 | `contract_version` | constant `"1"` | str | n/a | n/a | AVAILABLE_AUTHORITATIVE | auth | DIRECTLY_OBSERVED |
| 4 | `config_version` | `get_hermes_config` / market-hours `DstAwareMarketHours` version (`"3"`) | str | n/a | governed at wire-time | AVAILABLE_AUTHORITATIVE | auth | DIRECTLY_OBSERVED |
| 5 | `socket_state` | `adapters/base.AdapterState` via `_set_state` **+** `watchdog._current_stream_state` | enum str | n/a (control-plane) | explicit transition | **AVAILABLE_AUTHORITATIVE** for DISCONNECTED/FAILED; **AMBIGUOUS** for "connected" (see caveat) | auth | DIRECTLY_OBSERVED |
| 6 | `provider_disconnect_event` | `main.oanda_stream_task` outer `except` / `StopAsyncIteration` (~L703/L912) | bool | UTC (log) | provider-initiated drop | AVAILABLE_DERIVED | auth | DIRECTLY_OBSERVED |
| 7 | `auth_failure` | `oanda.connect()`→False (`/v3/accounts` ≠ 200) · `oanda.stream()` status ≠ 200 (401/403) | bool | UTC (log) | auth/session reject | AVAILABLE_DERIVED | auth | DIRECTLY_OBSERVED |
| 8 | `heartbeat_available` | `adapters/base.AdapterHealth.last_tick_at` (bumped on HEARTBEAT+PRICE) | bool | UTC wall (adapter) | presence of a bump surface | AVAILABLE_DERIVED | auth | DIRECTLY_OBSERVED (presence) |
| 9 | `heartbeat_age_s` | `now − AdapterHealth.last_tick_at` | s float | UTC wall (adapter) | age since last **any** message | AVAILABLE_DERIVED | auth | DIRECTLY_OBSERVED live (**inferred** in July-16 replay — never logged then) |
| 10 | `shared_stream_silent` | `main` `asyncio.TimeoutError` on `stream_iter.__anext__` (~L707) | bool | UTC (log) | no line at all past stall horizon | AVAILABLE_DERIVED | auth | DIRECTLY_OBSERVED |
| 11 | `parser_fatal` | `oanda.stream()` `async for`/task **termination** (NOT ordinary `json.JSONDecodeError`) | bool | UTC (log) | enumerated FATAL only | AVAILABLE_DERIVED | auth | DIRECTLY_OBSERVED |
| 12 | `reconnect_in_progress` | `watchdog.stream_state == RECOVERING` · `main.retry_count>0` / `CONNECTED_UNPROVEN` | bool | n/a | reconnect loop active | AVAILABLE_DERIVED | auth | DIRECTLY_OBSERVED |
| 13 | `shared_progress_available` | `AdapterHealth.last_tick_at` **any-message liveness** (distinct from per-instrument) | bool | UTC wall (adapter) | any provider msg advances it | AVAILABLE_DERIVED | auth | DIRECTLY_OBSERVED (Phase-1 mirrors heartbeat; §6) |
| 14 | `shared_progress_age_s` | same as #13 | s float | UTC wall (adapter) | age since any message | AVAILABLE_DERIVED | auth | DIRECTLY_OBSERVED |
| 15 | `provider_maintenance_indication` | **(none — OANDA emits no maintenance signal)** | bool | n/a | n/a | **NOT_CURRENTLY_AVAILABLE / UNSAFE_TO_INFER** | adv | **UNAVAILABLE** (value `False`, never inferred) |
| 16 | `error_count_delta` | `AdapterHealth.error_count` (delta since last snapshot) | int | UTC | error counter movement | AVAILABLE_DERIVED | adv | DIRECTLY_OBSERVED |
| 17 | `heartbeat_soft_horizon_s` | governed config (design default 15 s) | s float | n/a | OANDA HB ~5 s cadence → soft ~15 s (PROVISIONAL) | AVAILABLE_AUTHORITATIVE (config) | auth | DIRECTLY_OBSERVED (value); **basis PROVISIONAL** |
| 18 | `heartbeat_hard_horizon_s` | governed config (design default 45 s) | s float | n/a | ~60–90 s upper (PROVISIONAL) | AVAILABLE_AUTHORITATIVE (config) | auth | DIRECTLY_OBSERVED (value); **basis PROVISIONAL** |
| 19 | `instruments[]` | `watchdog._instrument_health` / `_instrument_last_tick`/`_m1` classified by `DstAwareMarketHours.is_truth_expected` | tuple | UTC | tick/M1 freshness + governed schedule | AVAILABLE_DERIVED (per-instrument) | **adv to transport** | DIRECTLY_OBSERVED |
| 20 | `limiter` | `watchdog._recovery_attempts_window` (+ availability of store) | struct | UTC | rolling 3600 s window | AVAILABLE_AUTHORITATIVE (control) | auth | DIRECTLY_OBSERVED |
| 21 | `evidence_summary` | adapter-composed (snapshot_id, source_sha, gen, completeness) | map | n/a | n/a | AVAILABLE_ADVISORY | adv | DIRECTLY_OBSERVED |

**Caveat on #5 (load-bearing honesty):** on the current deployment `main.oanda_stream_task` runs its **own** loop
and `oanda.connect()` **never calls** `_set_state(CONNECTED)` — only `disconnect()` sets `DISCONNECTED`. So the
adapter's positive "connected" flag is **UNPROVEN**; only DISCONNECTED/FAILED transitions are authoritative. The
design therefore **never treats "connected" as flow proof** — it leans on the heartbeat (#8/#9), exactly the
Option-C seam. `watchdog._current_stream_state` (CONNECTED_UNPROVEN/FLOWING/RECOVERING) is the more actively
maintained control-plane truth and is the primary source for #5/#12.

---

## 2. Availability classification (complete mapping)

Six classes; **missing ≠ favourable default**.

| Class | Meaning | Example fields | Rule when this class |
|---|---|---|---|
| **AVAILABLE_AUTHORITATIVE** | explicit control-plane truth | `socket_state` (DISCONNECTED/FAILED), `limiter`, `config_version`, horizons (value) | trust directly |
| **AVAILABLE_DERIVED** | computed from a real observed surface | heartbeat #8/#9, silent-stall #10, disconnect #6, auth #7, parser #11, shared-progress #13/#14, instruments #19 | trust the derivation, record basis |
| **AVAILABLE_ADVISORY** | real but never alone a transport vote | `error_count_delta`, per-instrument freshness, `evidence_summary` | may inform incident/proposal; never authorises |
| **NOT_CURRENTLY_AVAILABLE** | no source in current code | `provider_maintenance_indication` | mark UNAVAILABLE; value inert `False` |
| **AMBIGUOUS** | source exists but truth unclear | `socket_state == "connected"` (UNPROVEN) | fail closed → SILENT_UNCONFIRMED, corroborate via heartbeat |
| **UNSAFE_TO_INFER** | could be guessed, must NOT be | maintenance from a metals break; heartbeat from REST quote | never infer; mark UNAVAILABLE |

An authority-bearing field (#5–#14, #17–#20) that resolves to NOT_CURRENTLY_AVAILABLE / AMBIGUOUS / UNSAFE_TO_INFER
with no safe corroboration → the snapshot's `evidence_completeness = INCOMPLETE` → shadow **fails closed** (§21) and
the comparison is `EVIDENCE_INCOMPLETE` (§15). Advisory fields missing → `PARTIAL`, decision still proceeds.

---

## 3. Socket-state contract

`socket_state ∈ {connected, connecting, reconnecting, disconnected, failed, unknown}` (mirrors `AdapterState` +
`watchdog.StreamState`, decoupled from both). Rules:

- **CONNECTED ≠ flow.** `connect()` proves only auth/API reachability (main.py:947); a positive connected flag is
  UNPROVEN and is corroborated only by heartbeat freshness. Never treated as liveness.
- **disconnected / failed** are authoritative → `DISCONNECTED` transport state → reconnect authority (emergency).
- **unknown** (truth missing entirely) → `SILENT_UNCONFIRMED` → fail closed, corroborate or escalate.
- **Callback-vs-polling race:** the socket state is captured from an **immutable copy** taken at snapshot build
  (§19). A `_set_state` occurring mid-capture is scoped to the **connection_generation** (§4); a generation change
  during capture invalidates the snapshot (retry-on-change), it does not silently mix.
- **Stale-state detection:** a "connected" flag with a hard-stale heartbeat is a **conflict** →
  `SILENT_UNCONFIRMED` + `TRANSPORT_SIGNAL_CONFLICT`, represented explicitly in `contradictory_evidence`, never
  resolved in favour of health.

---

## 4. Provider-disconnect callback contract + BOUNDING

**Fields per event:** provider · event_type · occurred_at (provider) · observed_at (UTC) · connection_generation ·
sequence · reason · auth_affected · reconnect_started · duplicated.

**Bounding (the Phase-1 core is single-shot/stateless; the adapter MUST bound — handover req #1, and the core sets
`adapter_bounding_required=True` on every emergency-bypass decision):**

| Control | Rule |
|---|---|
| Dedup key | `(provider, connection_generation, event_type)` |
| Debounce | `callback_dedup_window_sec` (default 2.0 s); repeated identical events within the window collapse to one shadow event |
| Generation scoping | a new `connect()` = a **new** `connection_generation`; a disconnect in a newer generation is **never** deduped against an older one |
| Max rate | `max_callback_shadow_events_per_min` (default 20) caps callback-driven shadow evals |
| Suppressed visibility | every suppressed duplicate is **counted + logged** (`callback_dedups` metric); suppression is never silent |
| Safety | **dedup must NOT hide a genuinely new disconnect** — a distinct generation or a distinct event_type always passes |

---

## 5. Heartbeat-truth contract

- **Source:** `AdapterHealth.last_tick_at`, bumped on OANDA `HEARTBEAT` **and** `PRICE` (`oanda.py:141`). This is a
  **real** transport-alive surface (handover req #2), **not** invented, and **independent** of any instrument.
- **Event type:** any provider message. **observed_at:** UTC. **age:** `now − last_tick_at`.
- **Horizons (PROVISIONAL — flagged for Phase-2 empirical calibration):** OANDA pricing-stream heartbeat cadence
  ≈ 5 s ⇒ **soft ≈ 15 s** (≈3 missed heartbeats), **hard ≈ 45 s** (design default; empirical band 60–90 s). These
  are the values in `RecoveryDecisionInput.heartbeat_soft/hard_horizon_s`. **Basis stated; provisional until
  calibrated on real Phase-2 data.**
- **missing (`heartbeat_available=False` / age `None`) vs stale (age > hard):** missing → `UNKNOWN` → fail closed;
  stale → `HARD_STALE`. Both are represented, never coerced to healthy.
- **Startup grace / reconnect grace:** for the first N seconds after process start or after a reconnect, an absent
  heartbeat is tolerated (adapter-side grace) so a cold start does not manufacture a fault.
- **Market-closure implication (the July-16 core insight):** heartbeats **continue** during the metals break →
  transport healthy while metals prices stop. The core reads `HEARTBEAT.FRESH` → `TRANSPORT_HEALTHY` even though
  `XAU` is silent (governed_closed) and `SPX/WTICO` are stale (unvalidated).
- **Clock skew:** `last_tick_at` is set with `datetime.now(UTC)` on the same host as evaluation; age is a same-host
  delta, so provider-vs-local skew does not enter. All comparisons are UTC.

---

## 6. Shared-progress-truth contract

- **Source:** `AdapterHealth.last_tick_at` **any-message liveness** — the SAME surface as the heartbeat, but read as
  "did *any* instrument or heartbeat advance the stream", which is **INDEPENDENT of per-instrument freshness**
  (handover req #3). Phase-1 mirrors heartbeat (`shared_progress_available=None ⇒ mirror`); Phase-2 introduces the
  independent governed shared-progress adapter surfacing the `asyncio.TimeoutError` silent-stall seam separately.
- **Expected-flow filtering / authority set:** the **corroboration quorum** for *derived* authority is instrument-
  based — **ALL validated expected-flow instruments stale** — not a raw progress threshold. **Excluded** from the
  authority set: expected-closed (governed_closed), reopening-grace, and **unvalidated** (WTICO/SPX) instruments.
- **Distinguished states:** partial-instrument-stale (some validated stale, others progressing → transport alive →
  proposal) · broad-silence (silent stall / no line → `FAULT_CONFIRMED`) · all-flow-closed
  (`ALL_GOVERNED_INSTRUMENTS_CLOSED` → no derived authority) · unknown-flow-set (empty validated expected-flow →
  cannot derive authority; only direct transport faults reconnect).
- **Safeguards:** illiquid single instrument → single-instrument staleness never authorises (heartbeat is master);
  active-instrument masking → heartbeat is master, not the active instrument; all-governed-closed / weekend /
  holiday → empty corroboration set → no derived authority; provider maintenance → advisory only.

---

## 7. Parser-health-truth contract

| Condition | Source | Classification | Effect |
|---|---|---|---|
| ordinary malformed line | `oanda.stream()` `json.JSONDecodeError` → `warning; continue` | **recoverable-malformed** | incident/advisory; **cannot** bypass; `parser_fatal=False` |
| isolated instrument parse fail | `_parse_price` returns None / `_record_error; continue` | isolated-instrument-fail | advisory; per-instrument only |
| repeated malformed (rate) | error_count_delta rising | repeated | advisory escalation; still not fatal alone |
| schema-incompatible payload | parse consistently fails | schema-incompat | advisory; raise incident; not fatal alone |
| **stream task / `async for` termination** | outer-except / StopAsyncIteration | **thread-termination** → **FATAL** | `parser_fatal=True` → `FAULT_CONFIRMED` |

Only the enumerated FATAL (task/iterator termination) sets `parser_fatal=True → PARSER_EXCEPTION_FATAL`. Ordinary
malformed **cannot manufacture a bypass** (handover req #4).

---

## 8. Auth / session-truth contract

| Fault | Source | Distinct input | Reason code |
|---|---|---|---|
| credential reject / 401 / 403 | `connect()` 200-check; `stream()` status ≠ 200 | `auth_failure=True` | `AUTHENTICATION_FAILURE` |
| session expiry | stream status ≠ 200 mid-stream | `auth_failure=True` | `AUTHENTICATION_FAILURE` |
| subscription / instrument reject | (advisory — not a whole-stream auth fault) | per-instrument advisory | `PARTIAL/UNVALIDATED_..._NO_TRANSPORT_AUTHORITY` |
| throttle / rate-limit (provider 429) | status ≠ 200 → treat as transient | advisory + backoff (NOT auth) | `RECONNECT_NOT_AUTHORISED` unless corroborated |
| transient timeout | `asyncio.TimeoutError` | `shared_stream_silent` path | `SHARED_STREAM_PROGRESS_STALE` |

Auth-fail is kept **separate** from disconnect, rate-limit, instrument-reject, and transient stall. Auth-fail →
`AUTH_FAILED` → emergency reconnect (bounded re-auth).

---

## 9. Instrument-evidence mapping

Reuses the **governed** `utils/hermes_market_hours_health_v1.py` classifier (`DstAwareMarketHours.is_truth_expected`)
and the authoritative **14-instrument inventory**: `XAU_USD, XAG_USD, XPT_USD, XCU_USD, GBP_USD, EUR_USD, USD_JPY,
AUD_USD, NZD_USD, USD_CAD, USD_CHF, EUR_GBP, WTICO_USD, SPX500_USD`.

| Instrument condition | expected_flow | stale | validated | governed_closed | reopening_grace | Effect |
|---|---|---|---|---|---|---|
| expected-open + stale (validated) | T | T | T | F | F | incident + contributes to corroboration quorum |
| expected-closed (metals break / weekend) | F | F | * | T | F | suppressed; no incident; no vote |
| unvalidated stale (WTICO/SPX) | T | T | **F** | F | F | fail-loud incident; **no transport vote** |
| reopening grace | F | F | T | F | **T** | absence tolerated |
| unknown / malformed / missing-policy / loader-failure | T (fail-closed=open) | as measured | **F** | F | F | behave as open; fail-loud; no vote |

**No substring parsing. No invented schedule. Unknown-policy ≠ closed. WTICO/SPX are NOT expected-closed.** Handles
DST via the governed checker; invalid schedule version → fail-closed=open + fail-loud. `validated` is the **only**
gate to a transport vote — not liquidity, not importance (doctrine, architecture_v1 §7, applies to the CLASS).

---

## 10. Current-authority observation (PASSIVE)

The adapter observes, **read-only**, what the existing `sustained[0]` authority did — **never** calls, wraps,
gates, or re-orders it (handover req #9). Observed surfaces (all existing):

| Observed | Source |
|---|---|
| evaluation time | watchdog cycle timestamp |
| triggering instrument | `_recovery_request_reason` (parsed `instrument=…`) |
| sustained-red set | `_per_instrument_red_since` (passive read) |
| recovery-request creation + reason | `_recovery_request_pending` / `_recovery_request_reason` |
| cooldown / limiter | `_last_recovery_request_at` / `_recovery_attempts_window` |
| reconnect requested / executed / suppressed / result | `consume_recovery_request` outcome + `main` reconnect log |

Captured into the immutable `CurrentAuthorityObservation` (`evaluated`, `triggering_instrument`,
`recovery_requested`, `reconnect_executed`, `reconnect_suppressed`, `reason`). **Observation MUST NOT change the
authority's ordering or timing** — it reads state/events already produced; it does not call `_evaluate_per_instrument_recovery`
nor consume the request flag.

---

## 11. Shadow-eval trigger

**Options considered:** (a) every watchdog cycle · (b) on the current-authority recovery evaluation · (c) on
evidence-change · (d) on a recovery-request · (e) fixed low-frequency · (f) combined.

**Chosen: (f) combined — a low-frequency cadence tick (aligned to `watchdog_interval_sec`, default 15 s) PLUS a
bounded event hook on provider-disconnect callbacks (§4) and on an observed current-authority recovery-request.**
Justification: the cadence tick guarantees we sample the daily metals transition and normal open market; the event
hooks ensure we capture genuine faults and the exact instants the current authority acts (so the comparison is
meaningful). Bounded by §4 so callbacks cannot storm.

| Constraint | Value |
|---|---|
| Max frequency | ≤ 1 / `shadow_eval_cadence_sec` (15 s) + ≤ `max_callback_shadow_events_per_min` (20) |
| Dedup | by `(connection_generation, snapshot_id)`; identical snapshot within a cadence window collapses |
| Concurrency | single-flight per process; a running eval is never re-entered |
| Ordering vs current authority | shadow runs **after** observing current-authority state; it **never precedes, delays, or blocks** it |
| Perf budget | §20 |
| Backpressure | if an eval overruns the cadence, the next tick is **skipped** (drop, never queue-grow) |
| Failure isolation | an eval exception is caught, recorded as `ADAPTER_ERROR`, and **never** propagates to watchdog/main (§21) |

**Invariant:** the shadow path MUST never delay or block the current authority or the market-data loop.

---

## 12. Adapter architecture — 8 separated parts

| # | Part | Responsibility | Seam (interface) |
|---|---|---|---|
| 1 | Evidence collectors | read ONE live surface each (socket, heartbeat, shared-progress, parser, auth, instrument, current-authority), read-only | `EvidenceCollector` |
| 2 | Snapshot builder | atomically assemble an immutable `EvidenceSnapshot` (generation-checked) | `SnapshotBuilder` |
| 3 | Phase-1 input mapper | snapshot → `core.RecoveryDecisionInput` (pure) | `Phase1InputMapper` / `EvidenceSnapshot.to_recovery_input()` |
| 4 | Pure-core invocation | call the **unchanged** `core.decide()` | `PureCoreInvoker` |
| 5 | Current-authority observer | PASSIVELY read the existing authority (§10) | `CurrentAuthorityObserver` |
| 6 | Comparator | classify shadow vs current → `ComparisonClass` (§15) | `ShadowComparator` |
| 7 | Shadow evidence emitter | persist the immutable `ShadowDecisionRecord` (append-only, read-only re: transport) | `ShadowEvidenceEmitter` |
| 8 | NO-OP shadow-executor boundary | **refuses** to act — the mechanical proof (§16) | `NoOpShadowExecutorBoundary` |

DI + test seams throughout (`design/sss_phase2_interfaces_v1.py`, doubles in `design/sss_phase2_test_doubles_v1.py`).
**The core is untouched** (parts 3–4 only call it). **No part holds a reconnect-executor handle** (part 8 is where
one would be in Phase 3+, and it raises).

---

## 13. Evidence snapshot schema

Immutable, versioned `EvidenceSnapshot` (`design/sss_phase2_evidence_snapshot_v1.py`,
`schemas/shared_stream_recovery/evidence_snapshot.v1.schema.json`). **34 top-level fields** (incl. derived
`snapshot_id`) covering the 21 core inputs plus provenance: `snapshot_version, contract_version, provider, captured_at_utc, evaluated_at_utc, config_version,
source_sha, adapter_version, runtime_identity, connection_generation, socket_state, provider_disconnect_event,
auth_failure, heartbeat_available, heartbeat_age_s, shared_stream_silent, parser_fatal, reconnect_in_progress,
shared_progress_available, shared_progress_age_s, provider_maintenance_indication, error_count_delta,
heartbeat_soft/hard_horizon_s, instruments[], limiter_*, field_provenance[], unavailable_fields[],
contradictory_evidence[], evidence_completeness` (+ derived `snapshot_id`). It is **sufficient to reproduce the
shadow decision OFFLINE** — proven by the deterministic replay harness (§23) and `snapshot_id` determinism.

---

## 14. Shadow decision record

Immutable, versioned `ShadowDecisionRecord` (`design/sss_phase2_shadow_record_v1.py`,
`schemas/.../shadow_decision_record.v1.schema.json`). **24 top-level fields** (incl. derived `record_id`): record/snapshot ids, shadow action/authority/
transport-state/reasons, `shadow_reconnect_authorised`, `shadow_emergency_bypass_eligible`, current-authority
observation (evaluated, triggering instrument, requested, executed, suppressed), `comparison_class`,
`divergence_reasons`, `evidence_completeness`, `adapter_version`, `source_sha`, `runtime_identity`, `config_version`,
`contract_version`, embedded full `envelope`, and the constants **`shadow_only=true`, `consumer_live=false`**
(validated at construction — a `False`/`True` respectively raises). Impossible to mistake for a reconnect authority.

---

## 15. Comparison taxonomy (10 classes)

`design/sss_phase2_comparator_v1.ComparisonClass` — total, deterministic. Each: meaning · severity · investigation-
required · phase-3 contribution (see `COMPARISON_META`).

| Class | Meaning | Sev | Investigate | Phase-3 |
|---|---|---|---|---|
| `AGREE_NO_ACTION` | neither reconnects | info | no | baseline confidence |
| `AGREE_RECONNECT` | both reconnect (genuine fault) | info | no | preserves genuine-fault reconnects |
| `SHADOW_DENIES_CURRENT_RECONNECT` | current reconnected; shadow would NOT (**July-16 class**) | high | yes | **primary target divergence** |
| `SHADOW_AUTHORIZES_CURRENT_NO_RECONNECT` | shadow authorises a reconnect current missed | high | yes | safety divergence to understand |
| `SHADOW_PROPOSAL_CURRENT_RECONNECT` | granular label of the above where shadow = PROPOSAL_ONLY | high | yes | refines the denial |
| `CURRENT_AUTHORITY_NOT_EVALUATED` | current ran no recovery eval | info | no | excluded from divergence accounting |
| `SHADOW_INDETERMINATE` | shadow fail-closed (escalation), current didn't reconnect | warn | yes | horizon/quorum tuning needed |
| `EVIDENCE_INCOMPLETE` | authority-bearing field UNAVAILABLE | warn | yes | coverage gap |
| `EVIDENCE_CONFLICT` | contradictory transport evidence → fail closed | warn | yes | source disagreement |
| `ADAPTER_ERROR` | shadow adapter errored; NO decision | error | yes | reliability gap (must be ≈0 pre-Phase-3) |

Precedence: `ADAPTER_ERROR > EVIDENCE_INCOMPLETE > EVIDENCE_CONFLICT > CURRENT_AUTHORITY_NOT_EVALUATED >
agreement/divergence > SHADOW_INDETERMINATE`. **July-16 → `SHADOW_DENIES_CURRENT_RECONNECT`** (binding; the coarse
class is returned by default, the `SHADOW_PROPOSAL_CURRENT_RECONNECT` granular label via `granular=True`).

---

## 16. Shadow-safety boundary (MECHANICAL)

Not comments — enforced by code + tests:

1. **No executor dependency:** no part imports or holds a `disconnect()`/`connect()` object; part 8 is a pure
   `NoOpShadowExecutorBoundary` with **no** connect/disconnect/recovery method (test asserts `not hasattr`).
2. **No reconnect callback / no recovery-request producer:** the observer only reads; it never calls
   `consume_recovery_request` nor sets `_recovery_request_pending`.
3. **Output type distinct from a command:** the terminal artefact is a `ShadowDecisionRecord`, not a reconnect
   instruction; `shadow_only=True` and `consumer_live=False` are **validated constants** (construction raises otherwise).
4. **Runtime-import guard:** static-guard test fails if `main.py`/`watchdog.py`/adapters/compose/systemd reference
   any Phase-2 module.
5. **Refuse-to-execute:** `RefusingShadowExecutor.refuse()` **raises** `ShadowExecutionForbidden` — the
   impossibility is testable.
6. **No side effect in tests:** the whole suite runs with zero I/O (no socket/DB/Redis).
7. **Flag defaults DISABLED:** `shadow_enabled` default `false`, no implicit enable (§17).
8. **Adapter failure never affects current authority:** every failure path is caught and recorded (§21).

---

## 17. Configuration contract

`schemas/shared_stream_recovery/shadow_adapter_config.v1.schema.json`. **Config lives in governed `hermes_config`
(DB), not in code.** Each key: owner (HERMES) · type · allowed · default · fail-safe · source (`hermes_config`) ·
reload (lifespan-init) · validation.

| Key | Type | Default | Fail-safe |
|---|---|---|---|
| `shadow_enabled` | bool | **false** | absent/invalid → disabled; **no implicit enable** |
| `shadow_consumer_live` | bool (const) | **false** | `true` is a validation error |
| `shadow_eval_cadence_sec` | int 5–300 | 15 | invalid → disabled |
| `heartbeat_soft_horizon_s` / `_hard_` | num | 15 / 45 | PROVISIONAL; hard ≥ soft; invalid → disabled |
| `shared_progress_soft/hard_horizon_s` | num | 15 / 60 | PROVISIONAL |
| `callback_dedup_window_sec` | num | 2.0 | new generation never deduped away |
| `max_callback_shadow_events_per_min` | int | 20 | cap |
| `quorum_requires_all_validated_expected_flow_stale` | bool (const true) | true | not tunable to a % |
| `snapshot_retention_days` | int | 30 | — |
| `log_sampling_ratio` | num 0–1 | 1.0 | divergences always logged |
| `evidence_output_path` | str | `ops/evidence/shadow_stream_recovery/decisions.jsonl` | project-local JSONL |
| `alert_on_comparison_classes` | array | DENIES/AUTHORIZES/CONFLICT/ADAPTER_ERROR | — |

No secrets in config. **Nothing here is read by any runtime today.**

---

## 18. Evidence-storage assessment

**PREFER project-local append-only JSONL** (`ops/evidence/shadow_stream_recovery/decisions.jsonl`) **and/or
structured logs** over Redis/SQL. Rationale: the shadow record is an immutable, replayable audit artefact; JSONL +
`source_sha` + `snapshot_id`/`record_id` (sha256) give offline replay, deterministic comparison, audit, retention,
and checksums with **no** new Redis key or SQL migration and **no** secrets. **No durable DB is assumed.** If a
durable audit store is later genuinely wanted, it is proposed **separately** (owner HERMES; append-only table
`hermes_stream_shadow_decisions`; 30-day retention; back-compat additive; activation-gated) — **not** claimed as
authority here, **not** shipped.

---

## 19. Concurrency / consistency

| Concern | Approach |
|---|---|
| Snapshot atomicity | build from an **immutable copy** of each surface taken in one pass; the `EvidenceSnapshot` is frozen |
| Thread safety | collectors read only; **no lock is held across a market-data or reconnect op** |
| Partial reads | a field that cannot be read atomically → UNAVAILABLE → completeness downgraded, never torn |
| Callback races | scoped by `connection_generation`; debounced (§4) |
| Generation change mid-capture | **retry-on-change**: if generation changes during build, the snapshot is discarded and rebuilt |
| Reconnect during snapshot | detected via generation change → retry; never mixes pre/post-reconnect state |
| Clocks | evaluation + ages are **UTC** same-host deltas; monotonic used only for cadence pacing, never for evidence timestamps |
| Instrument-state updates during capture | copied under a short read; no long lock |
| Duplicate eval | deduped by `(generation, snapshot_id)` |
| Stale mixing | forbidden — a snapshot carries a single generation |

**Invariant:** the shadow path holds **no lock** that could block the market-data or recovery paths.

---

## 20. Performance budget (measurable)

| Metric | Bound |
|---|---|
| Eval time (collect + map + decide + classify + emit) | ≤ 25 ms p99 (pure-core `decide` is O(instruments), no I/O) |
| Added latency to watchdog cycle | **0** (runs off-cadence / single-flight; a skipped tick on overrun) |
| Added reconnect latency | **0** (never on the reconnect path) |
| Memory | ≤ 1 snapshot + 1 record in flight; O(1) |
| Snapshot size | ≤ ~8 KB JSON |
| Log/JSONL volume | ≤ 1 record / cadence + bounded callbacks; `log_sampling_ratio` trims AGREE_* |
| CPU | negligible; no busy loop |
| Unbounded growth | none — retention-capped JSONL, no in-memory accumulation |

**Must not** delay watchdog cycles, block the stream, or increase reconnect latency — guaranteed by §11/§19 (off
the hot path, no shared lock).

---

## 21. Failure-behaviour matrix

For every failure: **current authority continues unchanged · no shadow reconnect · failure visible · evidence not
falsely completed · adapter may self-disable safely · no restart loop.**

| Failure | Behaviour |
|---|---|
| adapter exception | caught → `ADAPTER_ERROR` record; current authority untouched |
| evidence-source unavailable | field UNAVAILABLE → completeness `PARTIAL`/`INCOMPLETE` |
| partial snapshot | `EVIDENCE_INCOMPLETE`; decision withheld |
| clock error (naive/non-UTC) | core fails closed → `OPERATOR_ESCALATION`; comparison `SHADOW_INDETERMINATE`/`ADAPTER_ERROR` |
| schema error | record not emitted; write failure counted; visible |
| serialization error | caught; `ADAPTER_ERROR`; no crash |
| storage unavailable | emit returns `False` (visible, counted); never raises; never blocks |
| excessive latency | overrun → skip next tick (backpressure), no queue growth |
| current-authority obs unavailable | `evaluated=False` → `CURRENT_AUTHORITY_NOT_EVALUATED` |
| pure-core exception | treated as `ADAPTER_ERROR`; no fabricated decision |
| unsupported contract/snapshot version | fail closed on decode (`SnapshotError`/core escalation) |
| config missing/invalid | shadow **disabled** (fail-safe default) |

Adapter self-disable is a config flip, not a crash. No restart loop (single-flight + skip-on-overrun).

---

## 22. Observability

Logs + metrics, **UTC, stable reason codes, no secrets/tokens/account-ids**: enabled/disabled state, adapter
heartbeat, snapshot count, successful/incomplete evals, conflicts, divergences, adapter errors, eval latency, write
failures, callback dedups, evidence ages, comparison-class counts, current reconnects observed, shadow-denied
reconnects (`SHADOW_DENIES_CURRENT_RECONNECT`), shadow-authorised-but-current-didn't
(`SHADOW_AUTHORIZES_CURRENT_NO_RECONNECT`). Each shadow decision emits `decision_id`, `snapshot_id`, `record_id`,
`comparison_class`, `reason_codes`, `transport_state`, `limiter_state`, `config_version` (handover req #10).

---

## 23. July-16 shadow replay (deterministic, offline)

`tests/fixtures/sss_phase2/july16_snapshot.json` + `design/sss_phase2_replay_harness_v1.py`. Replays the metals-
break transition at **21:09:00Z** (current authority reconnect #1, `triggering_instrument=SPX500_USD`):

- **current authority** = 3 SPX500-driven reconnects (21:09/21:19/21:29Z) — recorded as observed fact;
- **shadow** = `RECOVERY_PROPOSAL_ONLY` / `RECONNECT_NOT_AUTHORISED` (transport `TRANSPORT_HEALTHY`);
- **comparison** = **`SHADOW_DENIES_CURRENT_RECONNECT`** (granular `SHADOW_PROPOSAL_CURRENT_RECONNECT`);
- **XAU** (+ metals) expected-closed → suppressed + excluded; **WTICO/SPX** fail-loud + visible, no vote;
- socket connected; heartbeat/shared-progress **healthy-or-explicitly-represented**; **NO** shadow execution.

**Honesty caveat handling (load-bearing):** the historical observer log recorded socket CONNECTED, `conn=CONNECTED`/
`fault=NONE` throughout, and metals prices stopping 20:59→22:04 as **DIRECTLY_OBSERVED**. It did **NOT** record
heartbeat age. The fixture therefore marks `heartbeat_available`/`heartbeat_age_s`/`shared_progress_*` as
**`JUSTIFIED_INFERENCE`** in `field_provenance` (inferred from: adapter bumps `last_tick_at` on HEARTBEAT ~5 s +
socket CONNECTED + fault NONE + FX progressing) — **not** presented as a logged measurement. `provider_maintenance_indication`
is `NOT_CURRENTLY_AVAILABLE` (never inferred from the break). The replay is deterministic (same snapshot → identical
`record_id`).

---

## 24. Genuine-fault shadow scenarios

`tests/test_sss_phase2_shadow_design_v1.py` (all inert, offline):

| Scenario | Shadow | Comparison |
|---|---|---|
| provider disconnect | `RECONNECT_AUTHORISED` (emergency) | `AGREE_RECONNECT` |
| socket disconnect | `RECONNECT_AUTHORISED` | `AGREE_RECONNECT` |
| auth failure | `RECONNECT_AUTHORISED` | `AGREE_RECONNECT` |
| heartbeat stale (corroborated: all validated expected-flow stale) | `RECONNECT_AUTHORISED` | `AGREE_RECONNECT` |
| shared-progress failure (silent stall) | `RECONNECT_AUTHORISED` | `AGREE_RECONNECT` |
| parser fatal | `RECONNECT_AUTHORISED` | `AGREE_RECONNECT` |
| limiter-exhausted + provider disconnect | `RECONNECT_AUTHORISED` (emergency bypass) | `AGREE_RECONNECT` |
| genuine fault + current requested NO reconnect | `RECONNECT_AUTHORISED` | `SHADOW_AUTHORIZES_CURRENT_NO_RECONNECT` |
| adapter evidence incomplete | withheld | `EVIDENCE_INCOMPLETE` |
| conflicting evidence | fail closed | `EVIDENCE_CONFLICT` |

---

## 25. Transition observation plan

Once wired (Phase 2, `shadow_enabled=true` in DEV): observe ≥ 1 XAU daily closure **and** reopening; ≥ 1 normal
open-market window; ≥ 1 natural provider disconnect **or** a controlled fixture; record current reconnect behaviour,
shadow decision, evidence completeness, eval latency, and **zero** side effects. **Min sample:** ≥ 3 daily metals
transitions + ≥ 1 genuine disconnect. **Acceptance:** every metals transition → `SHADOW_DENIES_CURRENT_RECONNECT`
(or `AGREE_NO_ACTION` once the current churn is bounded by the limiter); every genuine fault → `AGREE_RECONNECT`;
`evidence_completeness=COMPLETE` ≥ 95 % of samples; zero shadow reconnects; latency within §20.

---

## 26. Phase-2 acceptance criteria

1. Read-only; current authority unchanged. 2. No shadow reconnect emittable (mechanical, §16). 3. July-16-equiv →
proposal-only (`SHADOW_DENIES_CURRENT_RECONNECT`). 4. WTICO/SPX fail-loud, no vote. 5. Genuine faults →
`RECONNECT_AUTHORISED`. 6. Callback dups bounded (§4). 7. Heartbeat/shared-progress/parser provenance-backed. 8.
Missing evidence → `INCOMPLETE`/fail-closed. 9. Deterministic comparison. 10. Snapshot offline-replayable. 11. Perf
within budget. 12. No false GREEN. 13. No Redis/SQL unless separately approved. 14. No new runner unless justified
(none added). 15. `consumer_live=false`.

## 27. Phase-3 readiness criteria (NOT pre-authorised here)

Soak ≥ N daily transitions; ≥ M evaluations; evidence-complete % ≥ threshold; divergence taxonomy counts reviewed;
**zero** shadow side effects; July-16 closure success; genuine-fault fixture success; latency within budget;
callback-bound proof; no regression; rollback proof; **R2D2 cold audit**. Phase 3 (executor cutover behind a
rollback flag) is a **separate signed-off WO** — this design does **not** authorise it.

## 28. Deployment boundary (design the sequence; no deploy now)

A future Phase-2 impl WO needs, in order: exact-head audit → merge → lineage check → deploy-readiness audit → CA
authority → image-content review of the cumulative canonical PRs (see §29) → the Phase-2 PR → config install
(`shadow_enabled` default off) → rollback plan → guarded deploy → post-deploy observation. **No deploy now.**

## 29. Canonical-portfolio implications

| PR | Nature | Enters image? | Affects runtime? |
|---|---|---|---|
| PR103 v2-retirement (`SUPPORTED_CONFIG_VERSIONS={3}`) | prod constant | **yes** | **yes** — config loader; must validate `config_version=="3"` |
| PR104 predeploy-harness | tool | no (tool) | no |
| PR105 CI baseline | not-runtime | no | no |
| PR106 OANDA evidence | docs | no | no |
| PR107 transition-validator | tool | no | no |
| PR108 design contract | inert | no | no |
| PR109 pure core | inert (enters image, unimported) | **yes (present, unimported)** | no |

Required pre-deploy validation: diff the deployed image (`c5fc2a62f424`) against the merge base; obtain per-PR
deploy sign-off; confirm PR103's config-v2 retirement is compatible with the running config; confirm PR109 stays
unimported. **A cumulative canonical deploy is safe ONLY with governed per-PR lineage + regression** — no unsafe
selective cherry-pick. Build exclusions are not required (inert modules are harmless in-image), but the deploy WO
must **not** ship the correction unreviewed alongside PR103–107.

## 30. Rollback

Disable the shadow by config (`shadow_enabled=false`) → instant no-op. Return to image `c5fc2a62f424` by reverting
the (future) wiring commit. **No DB migration, no Redis cleanup, no reconnect-authority state change** — the current
authority is intact throughout; evidence JSONL files are retained; **no stale shadow command can execute** (there is
none). **Triggers:** any shadow side effect observed, `ADAPTER_ERROR` rate above threshold, latency breach, or
operator call.

## 31. Test strategy

Every evidence adapter (via doubles); stale/unavailable/conflicting evidence; callback dup; connection-generation;
snapshot atomicity (frozen + deterministic id); shadow-only enforcement; current-authority observation; comparator
taxonomy (all 10 reachable); offline replay; **July-16**; genuine faults; perf budget (pure, no I/O); storage
failure (visible, non-raising); **disabled-by-default**; **runtime-import boundary (static guard)**;
no-reconnect-side-effect; no-Redis/SQL; rollback (config flip). Implemented in
`tests/test_sss_phase2_shadow_design_v1.py` (28 tests) + the Phase-1 suite stays green.

## 32. Security / privacy

Secrets in provider events → **never** captured (`runtime_identity` sanitised; no tokens/account-ids). Account ids
→ excluded. Log injection → reason codes are enumerated stable strings; free-text is bounded. Oversized payloads →
snapshot bounded (§20). Retention → `snapshot_retention_days`. File perms → JSONL under `ops/evidence` (repo perms).
Checksum/tamper → `snapshot_id`/`record_id` sha256. Path traversal → `evidence_output_path` is project-local + fixed.
Config injection → schema-validated, typed. Unsafe deserialization → plain dict access, bounded, **no eval**
(`snapshot_from_dict`/`envelope_from_dict` reject unsupported versions).

## 33. Runtime-wiring boundary (narrowest safe hook)

A future impl would add: the shadow-adapter module; a **transport evidence interface** (surfacing socket/heartbeat/
silent/auth/shared-progress/parser from `AdapterHealth` + main loop, read-only); an instrument evidence mapper
(reusing the market-hours classifier); the current-authority **observer** (passive); the comparator; the serializer;
and a **minimal invocation hook**. **The narrowest safe hook:** an `async def _shadow_tick()` called from the
**watchdog cadence loop** (or an independent asyncio task in `main` lifespan **without** a new process/container/
runner) that, when `shadow_enabled`, builds a snapshot, calls `decide()`, observes the current authority, classifies,
and emits a record — and **returns** without touching transport. **Protected (untouched):** the current recovery
authority (`_evaluate_per_instrument_recovery`/`sustained[0]`), the reconnect executor path, market-hours semantics,
the provider client's side effects, the runner count (9), Redis/SQL contracts, and downstream apps.

---

## Appendix A — Risk register (top 4)

| # | Risk | Sev | Mitigation |
|---|---|---|---|
| R1 | Heartbeat horizons PROVISIONAL (no logged July-16 age) → mis-tuned band | Med | horizons flagged provisional; validated-corroboration required; Phase-2 soak calibrates on real data |
| R2 | Inferred July-16 heartbeat presented as fact → false confidence | High | provenance marks it `JUSTIFIED_INFERENCE`; test asserts the distinction; directly-observed facts kept separate |
| R3 | Deploying the correction ships PR103–107 unreviewed-for-deploy | High | §29 per-PR deploy sign-off / governed lineage; no unsafe cherry-pick |
| R4 | Shadow adapter (future) blocks the market-data or recovery path | High | off the hot path, single-flight, no shared lock, skip-on-overrun, failure isolation (§11/§19/§21) |

## Appendix B — Deliverable index

- Inert code: `design/sss_phase2_{evidence_snapshot,shadow_record,comparator,interfaces,replay_harness,test_doubles}_v1.py`
- Schemas: `schemas/shared_stream_recovery/{evidence_snapshot,shadow_decision_record,shadow_adapter_config}.v1.schema.json`
- Fixture: `tests/fixtures/sss_phase2/july16_snapshot.json`
- Tests: `tests/test_sss_phase2_shadow_design_v1.py` (28) · Phase-1 suite stays green
- Evidence: `ops/evidence/WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE2-SHADOW-ADAPTER-AND-EVIDENCE-CONTRACT-DESIGN-0001/`
