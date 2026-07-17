# HERMES Shared-Stream Recovery Authority — Architecture & Contract v1

**WO:** WO-HELM-HERMES-SHARED-STREAM-RECOVERY-CONTRACT-DESIGN-0001
**Authority:** HELM (HERMES market-data lane) · **Status:** DESIGN-ONLY / INERT / NOT WIRED / NOT FOR DEPLOYMENT
**Created (UTC):** 2026-07-17 · **Base:** canonical `main` `8de293de` · **Deployed ref traced:** `71ea3bd` (image `c5fc2a62f424`)

> This is a code-only design PR. No runtime code is modified. The pure decision core
> (`design/shared_stream_recovery_contract_v1.py`) is imported by NOTHING on any live path — proven by grep
> (§18, evidence bundle). It exists to make the contract demonstrably testable, not ceremonial.

---

## 0. Binding ruling implemented — Option C

**SEPARATE shared-stream reconnect authority from unvalidated per-instrument freshness.** Per-instrument
freshness may drive health / incidents / alerts / recovery-*proposals*, but MUST NOT alone mutate the shared
transport while independent transport evidence is healthy. A full-stream reconnect requires a
**TRANSPORT-AUTHORITY** condition. Per-instrument fail-loud health is retained (WTICO_USD / SPX500_USD stay
visibly stale). Provider schedules are never invented. Genuine faults are never silenced to reduce noise. Rate
limiting is retained. When transport truth is missing/contradictory the system **fails closed to safety** — it
does not mutate the shared transport on garbage, it escalates.

---

## 1. Current recovery call + state flow (the defect)

Traced from deployed `71ea3bd` (identical files at `8de293de`).

```
main.oanda_stream_task (main.py ~L667)
  └─ async for tick in oanda_adapter.stream():           # single SHARED stream, all instruments multiplexed
       ├─ __anext__() wrapped in asyncio.wait_for(timeout = tick_staleness_threshold_sec)
       │     └─ TimeoutError  -> StreamSilentStallError  -> outer except -> reconnect   [GENUINE transport signal]
       ├─ consume_recovery_request()  (watchdog flag)    -> StreamSilentStallError -> reconnect   [*** THE DEFECT ***]
       └─ record_tick(); candle aggregation; ...
  outer except Exception (main.py ~L912):
       set_stream_state(RECOVERING); record_recovery_attempt();
       exponential backoff; oanda_adapter.disconnect(); connect(); enter_proof_window(); backfill_gap()

utils.watchdog._run (watchdog.py L519) every watchdog_interval_sec:
  └─ _evaluate_instruments()  (L535)
       per instrument: is_truth_expected(inst, now)  [DstAwareMarketHours Mode-C seam]
         XAU in metals break -> truth_expected False -> AMBER/MARKET_CLOSED -> continue (NOT red)   [CORRECT]
         SPX500_USD / WTICO_USD schedule None -> truth_expected True -> tick/M1 freshness
             stale -> HealthState.RED, red_count++                                                   [fail-loud, CORRECT]
       └─ _evaluate_per_instrument_recovery(now, red_count)  (L618)
            track _per_instrument_red_since
            red_count>0 & FLOWING -> StreamState.PARTIAL_FLOWING
            sustained = instruments RED >= per_instrument_sustained_red_threshold_sec (=300)
            cooldown per_instrument_recovery_cooldown_sec (=600)
            rate-limit per_instrument_max_recovery_attempts_per_hour (=3)
            **AUTHORITY = sustained[0]  (a SINGLE instrument's freshness). NO transport check.**
            -> _recovery_request_pending = True; reason="sustained_red instrument=SPX500_USD ..."
```

**Where SPX500_USD authorised each of the 3 reconnects (July 16):** `_evaluate_per_instrument_recovery`
picked `sustained[0] == SPX500_USD` (WTICO co-red) at **21:09Z**, then again at **21:19Z** and **21:29Z**
(each after the 600 s cooldown, within the 3/hr cap). Each set `_recovery_request_pending`, consumed by
`main.oanda_stream_task` → `StreamSilentStallError("Per-instrument recovery: sustained_red …")` → full-stream
`disconnect()`/`connect()`. **No socket / heartbeat / shared-progress condition was ever consulted.** The socket
stayed `conn=CONNECTED, fault=NONE` and heartbeats kept flowing throughout — transport was demonstrably alive.

**Root cause (one line):** `instrument RED (freshness) → shared transport mutation`, with no independent
transport-fault evidence and no distinction between "this instrument is quiet" and "the pipe is broken".

**Constraint that shapes the fix:** OANDA v20 has **no per-instrument resubscribe** (confirmed in adapter code
comments). The only recovery action available is a **full-stream reconnect**. The design must therefore make
that single lever *harder to pull* (require transport authority) rather than invent a finer lever.

---

## 2. Transport-truth input model

Signals available in the real code (`adapters/base.py` `AdapterHealth`, `adapters/oanda.py`, `main.py`):

| Signal | Source in code | Class | Notes |
|---|---|---|---|
| socket_state (connected/…/failed) | `AdapterHealth.state` / `AdapterState` | **AUTHORITATIVE** | explicit control-plane state |
| provider disconnect event | outer `except` / `StopAsyncIteration` (main.py ~L703, ~L912) | **AUTHORITATIVE** | provider-initiated drop |
| auth / session reject | `connect()` returns False (401/403); `stream` status ≠ 200 (oanda.py L83, L126) | **AUTHORITATIVE** | |
| heartbeat present/age | `AdapterHealth.last_tick_at` bumped on **HEARTBEAT and PRICE** (oanda.py ~L141) | **AUTHORITATIVE** | shared transport-alive proxy, instrument-independent |
| shared-stream silent (no line at all) | `asyncio.TimeoutError` on `__anext__` (main.py ~L707) | **AUTHORITATIVE** | genuine full-stream stall |
| parser failure (fatal) | `_record_error` on parse (oanda.py L152) | **AUTHORITATIVE** *(fatal only)* | single parse errors are advisory |
| reconnect-in-progress | `StreamState.RECONNECTING` / retry loop | **AUTHORITATIVE** | suppresses stacking |
| limiter state | watchdog `_recovery_attempts_window` | **AUTHORITATIVE** (control) | |
| per-instrument tick/M1 freshness | watchdog `_instrument_last_tick/_m1` | **ADVISORY** | health/incident/proposal; never alone a transport vote |
| provider maintenance indication | (not currently emitted) | **ADVISORY** | never alone |
| **REST quote freshness** | `get_current_price` | **NOT PROOF** | explicitly rejected as transport evidence |

Key insight: because `last_tick_at` is bumped by heartbeats, **heartbeat freshness is a shared transport-alive
signal that is independent of any single instrument's freshness.** That is the seam Option C stands on.

---

## 3. Transport-authority state machine (pure, deterministic)

Implemented in `resolve_transport_state()` + `decide()`. Clock is injected (`evaluated_at_utc`, must be tz-aware
UTC). Horizons: heartbeat soft (default 15 s) / hard (default 45 s); these are design defaults, real values come
from governed config at wire-time.

| State | Entry inputs | Reconnect eligibility | Limiter | Incident | Operator visibility | Fail-safe |
|---|---|---|---|---|---|---|
| TRANSPORT_HEALTHY | socket connected + heartbeat ≤ soft | **No** | n/a | per-instrument only | normal | — |
| TRANSPORT_DEGRADED | connected + soft < hb ≤ hard | No (advisory) | n/a | per-instrument | warn | watch |
| SILENT_UNCONFIRMED | connected + hb > hard **or** hb missing **or** socket unknown | **Only if** all *validated* expected-flow instruments stale (corroboration) | applies (non-emergency) | yes | alert | conflict → fail closed |
| FAULT_CONFIRMED | shared_stream_silent **or** parser_fatal | **Yes (emergency)** | bypass | yes | alert | — |
| AUTH_FAILED | auth/session reject | **Yes (bounded, emergency)** | bypass | yes | alert + escalate | — |
| DISCONNECTED | socket down/failed **or** provider disconnect event | **Yes (emergency)** | bypass | yes | alert | — |
| RECONNECTING | reconnect_in_progress / connecting | No (already acting) | n/a | — | info | — |
| RATE_LIMITED | authority present + limiter exhausted (non-emergency) | throttled | blocks | yes | alert + escalate | — |
| RECOVERED | post-proof-window success | No | reset window | resolve | info | emitted by adapter, not core |

Transitions are total: every input tuple maps to exactly one state (see `resolve_transport_state`). Reason codes
are attached per branch in `decide()`.

---

## 4. Instrument-health model (separated from transport)

Per-instrument health is produced by the existing `utils/hermes_market_hours_health_v1.py`
`classify_instrument_health()` and is **advisory to transport**. The contract restates:
**`instrument RED != transport reconnect authority`.**

Preserved instrument states (mapped onto `InstrumentState` primitives `expected_flow/stale/validated/governed_closed`):

| Instrument condition | expected_flow | stale | validated | governed_closed | Effect |
|---|---|---|---|---|---|
| expected-open + stale (validated) | true | true | true | false | incident; contributes to shared-progress quorum |
| expected-closed absence (metals break/weekend) | false | false | * | true | suppressed, no incident, no vote |
| unvalidated no-policy stale (WTICO/SPX) | true | true | **false** | false | fail-loud incident; **no transport vote** |
| connection-fault-inherited | true | true | * | false | folded into transport signal, not per-instrument |
| reopening grace | false | false | true | false | tolerate absence, no incident |
| unknown / malformed / missing-config | true (fail-closed=open) | as measured | false | false | behave as open; fail-loud |

---

## 5. Full-stream reconnect authority contract

A full-stream reconnect is authorised **iff at least one TRANSPORT condition holds**:

1. socket disconnected / failed;
2. provider disconnect event;
3. authentication / session failure;
4. heartbeat expiry (hb > hard horizon) **AND corroborated** by all *validated* expected-flow instruments stale;
5. shared-stream progress failure across expected-flow instruments (= #4 corroboration, or silent stall);
6. parser exception (fatal — all messages failing);
7. confirmed transport fault (silent stall = no line incl. heartbeat).

It is **NOT** authorised when: socket connected + heartbeat healthy + ≥1 expected-flow *validated* instrument
progressing — even if 1–2 unvalidated instruments, one stale CFD, or one maintenance window are stale. Those
yield `RECOVERY_PROPOSAL_ONLY`.

**Quorum decision:** authority does **not** require a numeric instrument quorum when a direct transport signal
exists (#1–#3, #6–#7 stand alone). For the *derived* case (#4/#5) the quorum is **all validated expected-flow
instruments stale** — a single validated instrument still progressing proves the transport is alive and vetoes
reconnect. This is stricter and safer than a percentage threshold and needs no tuning.

---

## 6. Multi-instrument shared-progress / quorum design

- **Primary transport-alive proof = heartbeat freshness** (instrument-independent). This alone defeats
  "one illiquid instrument forces reconnect" and "one active instrument masks transport failure": the master
  signal is the heartbeat, not any instrument.
- **Corroboration set = VALIDATED, expected-flow (governed-open, past-grace) instruments only.** Unvalidated
  instruments (WTICO/SPX) are excluded from the vote; closed/grace instruments are excluded.
- **Shared-progress failure** is declared only when the heartbeat is hard-stale/missing **and every** member of
  the corroboration set is stale. A non-empty set with any member progressing ⇒ transport alive.
- Safeguards: one active instrument masking failure → heartbeat is master, not that instrument; one illiquid
  instrument → single-instrument staleness never authorises; unvalidated dominating → excluded from vote;
  all-governed-closed / weekend / holiday → empty corroboration set → cannot derive authority (only direct
  transport faults reconnect); mixed open/closed → only open-validated count; provider maintenance → advisory.

**Audit of the existing library-only quorum helper** (`utils/hermes_market_hours_health_v1.full_stream_recovery_eligible`,
PR#101, not runtime-consumed): **ADAPT, do not adopt as-is.** Its `min_open_stale_quorum=2` over
`contributes_to_full_stream` instruments is a sound *market-hours* gate and correctly excludes closed instruments,
but it has two gaps for this contract: (a) it takes `shared_stream_fault` as an *opaque boolean input* — it does
not itself model heartbeat/socket/shared-progress truth, so on its own it would still have authorised the July 16
reconnects had `shared_stream_fault` been wrongly derived from instrument freshness; (b) its count-quorum (≥2) does
not distinguish validated from unvalidated instruments — WTICO+SPX stale (count 2) would have met the quorum. This
contract therefore **keeps its "closed instruments never contribute" principle**, but **replaces the opaque
`shared_stream_fault` input with the explicit transport-authority state machine** (§3) and **requires the
corroboration set to be validated instruments** and **all** (not a count) stale. Recommended: at wire-time,
`full_stream_recovery_eligible` is superseded by `decide()`; the market-hours core remains the instrument
classifier feeding `InstrumentState`.

---

## 7. Unvalidated-instrument doctrine (WTICO_USD / SPX500_USD)

| Capability | Allowed? |
|---|---|
| Fail-loud per-instrument health (visibly stale) | **YES** |
| Visible incidents / alerts | **YES** |
| Advisory recovery *proposal* | **MAY** |
| Transport vote (corroboration of shared-progress loss) | **NO** (alone) |
| Reconnect authority | **NO** (alone) |
| Diagnosis contribution | **ONLY** when corroborated by independent transport evidence |

This doctrine applies to **ALL** current and future unvalidated instruments (any instrument whose schedule is
`None` / `fail_closed_unvalidated` / unmapped). Validation is the gate to a transport vote — not liquidity, not
importance.

---

## 8. Recovery action hierarchy

```
observe (watchdog cadence)
  -> per-instrument health incident            [automatic, fail-loud]
  -> operator alert                            [automatic]
  -> propose recovery                          [advisory: RECOVERY_PROPOSAL_ONLY]
  -> verify transport evidence                 [decide(): transport-authority gate]
      -> per-instrument recovery IF provider supports   [N/A for OANDA v20 — no per-instrument resubscribe]
      -> shared reconnect ONLY with transport authority  [automatic: RECONNECT_AUTHORISED]
          -> rate-limit (application-derived authority)   [RECONNECT_RATE_LIMITED]
          -> emergency bypass (genuine provider/socket/auth/shared fault)
  -> escalate                                  [OPERATOR_ESCALATION]
```

Automatic: incidents, alerts, transport-authorised reconnect, limiter. Advisory (never mutates transport):
proposals, unvalidated-stale diagnostics.

---

## 9. Provider-initiated disconnect path

Genuine provider/socket disconnects (`transport_state ∈ {DISCONNECTED, AUTH_FAILED}` and `FAULT_CONFIRMED`)
carry `emergency_bypass=True` and MUST reconnect promptly **even when** the 3/hr application limiter is exhausted,
freshness votes are suppressed, or the market is in a governed closure. They use the **outer-except reconnect
path with its own exponential backoff** (main.py ~L936), which is *separate* from the freshness-driven
per-instrument limiter — this is the path that correctly handled the 21:01/21:03Z natural disconnects. The
contract keeps them separate: the freshness limiter never gates a genuine disconnect, and a genuine disconnect is
never hidden to reduce noise (it always raises an incident + alert).

---

## 10. Rate-limiter contract

| Aspect | Contract |
|---|---|
| Scope | application-derived reconnects only (heartbeat/shared-progress/parser). **Not** genuine provider/socket/auth faults. |
| Counter key | in-memory `_recovery_attempts_window` (list of UTC timestamps), per-process |
| Time basis | rolling 3600 s window, UTC |
| Reset | window trim on each evaluation; process restart empties it (documented, fixture #17) |
| Persistence | none today (in-memory). §17 assesses whether durable audit is warranted (prefer no) |
| Provider vs application attempts | tracked separately; provider/socket faults bypass |
| Emergency bypass | `emergency_bypass=True` reconnects regardless of counter (reason `EMERGENCY_BYPASS_LIMITER`) |
| Visibility | limiter count + exhausted flag in every envelope + logs |
| Escalation on exhaustion | non-emergency authority + exhausted ⇒ `RECONNECT_RATE_LIMITED` + operator escalation |
| Reason codes | `RECONNECT_RATE_LIMITED`, `EMERGENCY_BYPASS_LIMITER` |
| 3/hr appropriate? | **Assessed, unchanged here.** 3/hr was the only thing that stopped the July 16 churn becoming unbounded — but it is a *symptom* limiter, not the cure. With transport authority in place, application-derived reconnects should be rare; 3/hr remains a reasonable backstop. **The limiter must not be the only protection** — transport authority is the primary guard. Any change to the number is a separate WO. |

---

## 11. Fail-safe / fail-closed semantics

"Fail closed to safety" for a shared-transport mutation means **do not mutate on bad data**, and **never go
silent**. Matrix:

| Condition | Behaviour |
|---|---|
| missing heartbeat (hb None) + socket connected | SILENT_UNCONFIRMED; authorise only if validated-set corroborates; else escalate |
| heartbeat impl unavailable | as above (hb None) |
| transport-state parser failure (fatal) | FAULT_CONFIRMED → emergency reconnect |
| stale transport state | treated as hard-stale heartbeat → SILENT_UNCONFIRMED |
| malformed decision inputs | clock guard / defensive → OPERATOR_ESCALATION, no reconnect |
| unknown instrument | validated=False → no vote; fail-loud |
| empty expected-flow set | cannot derive authority; only direct transport faults reconnect |
| all-governed-closed | `ALL_GOVERNED_INSTRUMENTS_CLOSED`, NO_ACTION unless direct transport fault |
| only-unvalidated-active/stale | no vote; proposal/escalation only |
| clock error (non-UTC / naive) | OPERATOR_ESCALATION, no reconnect (fixture #21) |
| Redis down (limiter unreadable) | `available=False` ⇒ treat as exhausted ⇒ non-emergency throttled; emergency still bypasses |
| SQL down | same as Redis — never blocks an emergency reconnect; escalate |
| provider callback missing | rely on socket_state + heartbeat; if both unknown → SILENT_UNCONFIRMED → escalate |
| conflicting socket-connected vs stale-heartbeat | SILENT_UNCONFIRMED + `TRANSPORT_SIGNAL_CONFLICT`; corroborate or escalate |

Safety over silence: every fail-closed path still raises an incident/alert.

---

## 12. Reason-code contract

Defined in the core (`MANDATED_REASON_CODES`, 14 mandated + 4 supplementary). Mandated:
`SOCKET_DISCONNECTED`, `PROVIDER_DISCONNECT_EVENT`, `AUTHENTICATION_FAILURE`, `HEARTBEAT_STALE`,
`SHARED_STREAM_PROGRESS_STALE`, `PARTIAL_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY`,
`UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY`, `ALL_EXPECTED_FLOW_INSTRUMENTS_STALE`,
`ALL_GOVERNED_INSTRUMENTS_CLOSED`, `TRANSPORT_SIGNAL_CONFLICT`, `RECONNECT_RATE_LIMITED`,
`RECONNECT_AUTHORISED`, `RECONNECT_NOT_AUTHORISED`, `RECOVERY_PROPOSAL_ONLY`.
Supplementary: `PARSER_EXCEPTION_FATAL`, `RECONNECT_IN_PROGRESS`, `EMERGENCY_BYPASS_LIMITER`,
`NO_TRANSPORT_TRUTH_FAILCLOSED`.

---

## 13. Decision matrix (24 rows)

Columns: **INC** = instrument incident · **ALERT** = operator alert · **PROP** = recovery proposal ·
**AUTH** = transport authority · **ACTION** = reconnect action · **LIM** = limiter treatment · reason + required evidence.

| # | Scenario | INC | ALERT | PROP | AUTH | ACTION | LIM | Reason (primary) | Required evidence |
|---|---|---|---|---|---|---|---|---|---|
| 1 | XAU closed + WTICO/SPX stale + socket conn + hb healthy + FX flowing | Y(SPX/WTICO) | Y | Y | **N** | RECOVERY_PROPOSAL_ONLY | n/a | UNVALIDATED_…_NO_TRANSPORT_AUTHORITY | hb fresh; FX progressing |
| 2 | as #1 but socket disconnected | Y | Y | — | Y | RECONNECT_AUTHORISED | bypass | SOCKET_DISCONNECTED | socket state |
| 3 | provider disconnect event, hb otherwise ok | Y | Y | — | Y | RECONNECT_AUTHORISED | bypass | PROVIDER_DISCONNECT_EVENT | except path |
| 4 | auth/session failure | Y | Y+esc | — | Y | RECONNECT_AUTHORISED | bypass | AUTHENTICATION_FAILURE | 401/403 |
| 5 | parser exception fatal | Y | Y | — | Y | RECONNECT_AUTHORISED | bypass | PARSER_EXCEPTION_FATAL | all msgs fail |
| 6 | hb hard-stale + ALL validated expected-flow stale | Y | Y | — | Y | RECONNECT_AUTHORISED | applies | SHARED_STREAM_PROGRESS_STALE | hb+all-stale |
| 7 | hb hard-stale but FX progressing | Y(SPX) | Y | Y | **N** | RECOVERY_PROPOSAL_ONLY | n/a | TRANSPORT_SIGNAL_CONFLICT | FX progressing |
| 8 | shared-stream silent (no line at all) | Y | Y | — | Y | RECONNECT_AUTHORISED | bypass | SHARED_STREAM_PROGRESS_STALE | silent stall |
| 9 | only-unvalidated stale + hb healthy | Y | Y | Y | **N** | RECOVERY_PROPOSAL_ONLY | n/a | UNVALIDATED_…_NO_TRANSPORT_AUTHORITY | hb fresh |
| 10 | single validated open stale + peer flowing + hb healthy | Y | Y | Y | **N** | RECOVERY_PROPOSAL_ONLY | n/a | PARTIAL_INSTRUMENT_STALE_… | peer progressing |
| 11 | all-governed-closed (weekend) + hb healthy | — | — | — | **N** | NO_ACTION | n/a | ALL_GOVERNED_INSTRUMENTS_CLOSED | schedules closed |
| 12 | all-governed-closed + genuine disconnect | — | Y | — | Y | RECONNECT_AUTHORISED | bypass | SOCKET_DISCONNECTED | socket state |
| 13 | holiday all-closed + hb healthy | — | — | — | **N** | NO_ACTION | n/a | ALL_GOVERNED_INSTRUMENTS_CLOSED | schedules closed |
| 14 | reopening grace absence + hb healthy | — | — | — | **N** | NO_ACTION | n/a | RECONNECT_NOT_AUTHORISED | within grace |
| 15 | limiter exhausted + application authority (hb-stale all-flow) | Y | Y+esc | — | Y | RECONNECT_RATE_LIMITED | blocks | RECONNECT_RATE_LIMITED | limiter=3/3 |
| 16 | limiter exhausted + genuine provider disconnect | Y | Y | — | Y | RECONNECT_AUTHORISED | bypass | EMERGENCY_BYPASS_LIMITER | provider event |
| 17 | restart resets in-memory limiter (0/3) + application authority | Y | Y | — | Y | RECONNECT_AUTHORISED | applies | RECONNECT_AUTHORISED | count=0 |
| 18 | Redis down (limiter unreadable) + application authority | Y | Y+esc | — | Y | RECONNECT_RATE_LIMITED | fail-closed | RECONNECT_RATE_LIMITED | available=false |
| 19 | transport-state down: socket unknown + hb None + FX flowing | Y | Y | (maybe) | **N** | RECOVERY_PROPOSAL/NO_ACTION | n/a | TRANSPORT_SIGNAL_CONFLICT | FX progressing |
| 20 | conflicting socket-connected + hb missing + ALL validated stale | Y | Y | — | Y | RECONNECT_AUTHORISED | applies | SHARED_STREAM_PROGRESS_STALE | corroborated |
| 21 | clock error (naive/non-UTC) | — | Y | — | **N** | OPERATOR_ESCALATION | n/a | NO_TRANSPORT_TRUTH_FAILCLOSED | bad clock |
| 22 | empty expected-flow + hb hard-stale (not silent) | — | Y | — | **N** | NO_ACTION/ESCALATE | n/a | ALL_GOVERNED_INSTRUMENTS_CLOSED | nothing expected |
| 23 | genuine disconnect after limiter exhaustion (=16) | Y | Y | — | Y | RECONNECT_AUTHORISED | bypass | EMERGENCY_BYPASS_LIMITER | provider event |
| 24 | TRANSPORT_DEGRADED (soft-stale hb) + no stale instruments | — | watch | — | **N** | NO_ACTION | n/a | RECONNECT_NOT_AUTHORISED | hb in soft band |

The 20 automated fixtures cover rows 1–21 (rows 23≡16, 22/24 are documented degenerate/soft variants).

---

## 14. Acceptance criteria (objective)

1. XAU closure ⇒ no XAU incident and no XAU reconnect vote. ✔ (fixture #1)
2. WTICO_USD / SPX500_USD visibly stale / fail-loud. ✔ (§7, envelope `unvalidated_stale_instruments`)
3. WTICO/SPX stale alone **cannot** force reconnect while transport healthy. ✔ (fixtures #1, #9)
4. Provider disconnect still reconnects. ✔ (#3, #12, #16)
5. Auth failure reconnects-or-fails-loud. ✔ (#4)
6. Heartbeat failure (corroborated) authorises a bounded reconnect. ✔ (#5→fixture, #20)
7. All-expected-open progress loss can authorise reconnect. ✔ (#6/#20)
8. Partial stale does not mutate transport without corroboration. ✔ (#6, #10)
9. Limiter visible in every decision. ✔ (`limiter_state`)
10. No false GREEN; no fabricated freshness; no schedule guessing. ✔ (core never fabricates; schedules external)
11. Runner count unchanged (9); no Redis/SQL contract change; `consumer_live=false`. ✔ (design-only, §17/§18)

---

## 15. Test strategy

Deterministic unit fixtures in `tests/test_shared_stream_recovery_contract_v1.py` (25 tests, 20+ fixtures).
Fixture **#1 is the exact July 16 transition** and asserts `RECOVERY_PROPOSAL_ONLY` / `NOT_AUTHORISED`.
Genuine-fault fixtures (#2–#8, #12, #16, #20) assert `RECONNECT_AUTHORISED`. Fail-safe fixtures (#18, #19, #21)
assert closed-to-safety. Invariant tests assert: only `RECONNECT_AUTHORISED` carries authority; determinism
(same input → same `decision_id`); every action/reason in-contract. Expected outcome + reason code per fixture is
encoded in the assertions. Integration/shadow testing is deferred to Phase 2 (§21).

---

## 16. Observability contract

Every decision emits (logs + envelope, UTC, no secrets): transport_state, socket_connected, heartbeat_state+age,
shared_progress_state+age, expected_flow_instruments, stale_instruments, unvalidated_stale_instruments,
market-hours-derived instrument states (upstream), recovery proposal, authority decision + reason_codes,
reconnect result (executor), limiter count/exhausted, config_version, decision_id. The envelope
(`schemas/shared_stream_recovery/decision_envelope.v1.schema.json`) is the canonical record; a fabric/evidence
pointer may reference `decision_id`. No credentials, account ids, or tokens are ever included.

---

## 17. Redis / SQL contract assessment

**PREFER no new contract.** The decision core is a pure in-process function; it reuses existing surfaces
(`hermes_instrument_health`, `hermes_incidents`, in-memory limiter). No new table, no new Redis key is required
for the contract itself. *If* durable decision-audit storage is later wanted, it would be:
owner HERMES; table `hermes_stream_recovery_decisions` (decision_id PK, evaluated_at_utc, transport_state, action,
reason_codes JSON, config_version); version 1; retention 90 days; append-only migration; back-compat additive;
activation-gated behind a config flag. **Not implemented here** (no schema shipped).

---

## 18. Runtime-wiring boundary (what a FUTURE impl PR would change)

**Would change:** a recovery **decision module** (promote/port the pure core into a wired location), a
**shared-stream reconnect authority function** replacing `_evaluate_per_instrument_recovery`'s `sustained[0]`
authority, a **transport-health adapter** exposing socket/heartbeat/silent-stall/auth signals from
`AdapterHealth` + main loop into `TransportSignals`, tests, reason codes, logs, and DI in `main.py` lifespan.

**Protected (untouched):** market-hours schedule loader, candle/indicator code, SQL schema, Redis contracts,
runner count (9), provider client (unless a genuine signal must be surfaced), downstream apps.

**Three layers:** (a) **pure decision core** — primitives in, `DecisionEnvelope` out, executes nothing (this WO);
(b) **thin runtime adapter** — gathers signals, calls the core, logs the envelope, emits proposal/incident
(read-only in shadow); (c) **side-effect executor** — consumes **only** `action == RECONNECT_AUTHORISED` and
performs the single available action (full-stream reconnect via the existing path).

---

## 19. Decision envelope

Immutable `DecisionEnvelope` (dataclass, `to_dict()`), JSON-schema’d. Fields: decision_version, decision_id,
evaluated_at_utc, provider, transport_state, socket_connected, heartbeat_state+age, shared_progress_state+age,
expected_flow_instruments, stale_instruments, unvalidated_stale_instruments, authority_status,
transport_authority, emergency_bypass, action, reason_codes, limiter_state, evidence_summary, config_version.
Actions: `NO_ACTION`, `INCIDENT_ONLY`, `RECOVERY_PROPOSAL_ONLY`, `RECONNECT_AUTHORISED`,
`RECONNECT_RATE_LIMITED`, `OPERATOR_ESCALATION`. The executor consumes only an AUTHORISED decision.

---

## 20. Backward compatibility

Compatible with current per-instrument incident generation, recovery-request records, the rate limiter, the
watchdog, the market-hours checker, and provider disconnect callbacks — the contract is *additive* and, until
Phase 3, decides nothing that acts. Startup is unchanged (nothing new imported). **Immediate rollback to
`71ea3bd` (image `c5fc2a62f424`) carries no schema incompatibility** — no schema is added.

---

## 21. Implementation phasing

- **Phase 1 (this WO):** pure contract + fixtures. No wiring. Inert.
- **Phase 2:** shadow decision adapter — gather signals, run `decide()`, **log the envelope + emit proposals
  only**. No action change; old authority still governs reconnects. **Shadow is genuinely useful here** (not
  ceremonial): it lets us observe a real daily metals transition and confirm the core would have said
  `NOT_AUTHORISED` on the exact SPX/WTICO stale, before we let it act.
- **Phase 3:** guarded authority cutover — executor obeys the new contract; **old `sustained[0]` authority
  retained behind a rollback flag**; observe across ≥1 daily transition.
- **Phase 4:** retire old authority after soak + audit.

---

## 22. Rollback design

Rollback preserves: code version (revert the wiring commit), **image `c5fc2a62f424`**, config (no new required
keys until Phase 3; Phase-3 flag defaults to old authority), decision-contract compat (envelope is additive),
limiter state (in-memory, unaffected), incident continuity (same tables), evidence. **No data migration** is
required for rollback at any phase ≤ 3.

---

## 23. Canonical-portfolio interaction

Base `8de293de` cumulatively contains undeployed PR#103 (config-v2-retirement — `SUPPORTED_CONFIG_VERSIONS={3}`),
PR#104 (predeploy-harness), PR#105 (CI baseline), PR#106 (OANDA evidence), PR#107 (transition-validator). This
correction branches from that cumulative source. **The eventual DEPLOYMENT design must explicitly gate the
correction so shipping it does not accidentally deploy all five portfolio additions unreviewed-for-deploy** — the
deploy WO must diff the deployed image (`c5fc2a62f424` / `71ea3bd`) against the merge base and obtain per-PR
deploy sign-off, or cherry-pick the correction onto a deploy-approved base. Note config v2 is already retired on
`8de293de`; this contract assumes `config_version == "3"`.

---

## 24. Evidence & PR

Design-only PR against `main`. Evidence bundle at
`ops/evidence/WO-HELM-HERMES-SHARED-STREAM-RECOVERY-CONTRACT-DESIGN-0001/`: architecture pointer, current-state
call-flow trace, July 16 reconnect chronology, decision matrix, acceptance criteria, risk register, test output
capture, `verdict.txt`, `SHA256SUMS`.

### Risk register (top)

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | Heartbeat horizon mis-tuned → transport declared silent while alive (false reconnect) | Med | soft/hard bands + validated-corroboration requirement; Phase-2 shadow tunes horizons on real data |
| R2 | Genuine provider fault throttled by limiter → prolonged outage | High | emergency bypass for direct provider/socket/auth/shared faults (fixtures #16/#23) |
| R3 | Deploying the correction ships 5 unreviewed portfolio PRs | High | §23 deploy-gate: per-PR deploy sign-off / cherry-pick onto approved base |
| R4 | Adapter does not yet expose all transport signals | Med | Phase-2 adapter surfaces socket/heartbeat/silent/auth from existing `AdapterHealth`; none invented |
| R5 | Future unvalidated instrument silently gains a transport vote | Med | doctrine (§7) applies to the CLASS; validation is the only gate |
```
