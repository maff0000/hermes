# Shared-Stream Recovery — Prototype ↔ Production Parity Matrix v1

**WO:** WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE1-PURE-DECISION-CORE-IMPLEMENTATION-0001
**Authority:** HELM (HERMES market-data lane) · **Status:** INERT / Phase 1 / NOT WIRED / NOT-FOR-MERGE-BY-IMPL-WO
**Created (UTC):** 2026-07-17 · **Base:** canonical `main` `ae80d113` (contains merged PR#108 design contract)

This matrix proves the PRODUCTION-owned pure core is **decision-equivalent** to the audited PR#108 prototype on
the shared fixtures. It is enforced by a parametrised parity test, not asserted by prose.

| Artefact | Path | Role |
|---|---|---|
| Audited PROTOTYPE (historical, unchanged) | `design/shared_stream_recovery_contract_v1.py` | The parity target. Stays as the audited artefact; NOT edited, NOT deleted, NOT wired. |
| PRODUCTION-owned pure core (this WO) | `utils/hermes_shared_stream_recovery_v1.py` | The single future production authority. Matches the `utils/hermes_<domain>_v1.py` convention. INERT in Phase 1. |
| Parity test (executable proof) | `tests/test_hermes_shared_stream_recovery_v1.py::test_prototype_parity_action_reconnect_and_reasons` | Runs both cores over 14 shared scenarios; asserts identical action, reconnect-authority, reason-code set, transport state. |

## 1. Why a production module (production-ownership rationale)

HELM ruled the production pure core lives at `utils/hermes_shared_stream_recovery_v1.py`, matching the two
existing production pure-domain cores (`utils/hermes_market_hours_health_v1.py`,
`utils/hermes_proposal_validator_v1.py`): frozen dataclasses, stdlib-only, injected clock, module header with
change history, versioned. `design/` is for audited design prototypes; a permanently-`design/`-resident core
would never be the thing a Phase-2 adapter imports. Promoting it now — while still INERT — gives one clear
production home and one audited parity baseline before any wiring.

## 2. Decision-equivalence on the shared fixtures

Both cores are exercised with the SAME transport signals, instrument states and limiter state. Equivalence is
asserted on **action + reconnect-authority + reason-code SET + transport_state**.

| # | Shared scenario | action (both) | reconnect authority (both) | transport_state (both) |
|---|---|---|---|---|
| 1 | July-16: XAU closed, SPX/WTICO stale-unvalidated, socket conn, hb fresh, FX flowing | `RECOVERY_PROPOSAL_ONLY` | **False** | `TRANSPORT_HEALTHY` |
| 2 | socket disconnected | `RECONNECT_AUTHORISED` | True (emergency) | `DISCONNECTED` |
| 3 | provider disconnect event | `RECONNECT_AUTHORISED` | True (emergency) | `DISCONNECTED` |
| 4 | auth/session failure | `RECONNECT_AUTHORISED` | True (emergency) | `AUTH_FAILED` |
| 5 | fatal parser | `RECONNECT_AUTHORISED` | True (emergency) | `FAULT_CONFIRMED` |
| 6 | hb hard-stale + ALL validated expected-flow stale | `RECONNECT_AUTHORISED` | True (derived) | `SILENT_UNCONFIRMED` |
| 7 | hb hard-stale but a validated FX pair still flowing | `RECOVERY_PROPOSAL_ONLY` | **False** | `SILENT_UNCONFIRMED` |
| 8 | shared-stream silent (no line at all) | `RECONNECT_AUTHORISED` | True (emergency) | `FAULT_CONFIRMED` |
| 9 | only unvalidated stale + transport healthy | `RECOVERY_PROPOSAL_ONLY` | **False** | `TRANSPORT_HEALTHY` |
| 10 | single validated open stale + peer flowing | `RECOVERY_PROPOSAL_ONLY` | **False** | `TRANSPORT_HEALTHY` |
| 11 | all governed instruments closed | `NO_ACTION` | **False** | `TRANSPORT_HEALTHY` |
| 12 | limiter exhausted + application-derived authority | `RECONNECT_RATE_LIMITED` | authority present, throttled | `SILENT_UNCONFIRMED` |
| 13 | limiter exhausted + genuine provider disconnect | `RECONNECT_AUTHORISED` | True (emergency bypass) | `DISCONNECTED` |
| 14 | reconnect already in progress | `NO_ACTION` | **False** | `RECONNECTING` |

All 14 pass identically (see test output in the evidence bundle).

## 3. Intentional, documented representational differences (NOT decision differences)

These differ in *shape/vocabulary* only; they do NOT change the action, the reconnect authority, or the reason
codes. They are the reason the parity test compares semantics, not raw dicts.

| Aspect | Prototype (`design/`) | Production (`utils/`) | Note |
|---|---|---|---|
| version field | `decision_version` | `contract_version` + `envelope_version` | schema validates both (back-compat) |
| authority string | `AUTHORISED` / `NOT_AUTHORISED` / `ESCALATE` | `authorised` / `not_authorised` / `fail_closed` / `rate_limited` | richer taxonomy (Task 2); same underlying action |
| authority bool | `transport_authority` (True even when throttled) | `reconnect_authorised` (True only when `action==RECONNECT_AUTHORISED`) | production separates "authority exists" from "execute now" |
| emergency bool | `emergency_bypass` | `emergency_bypass_eligible` + `emergency_bypass_reason` + `adapter_bounding_required` | narrower, reason-coded boundary (Task 5) |
| stale-instrument field | `stale_instruments` / `unvalidated_stale_instruments` | `stale_governed_instruments` / `stale_unvalidated_instruments` | same contents |
| decision_id basis | hash of decided output fields | hash of the **canonical input** (or supplied correlation id) | stronger determinism; still no randomness/Date.now |
| input shape | 3 args (`TransportSignals`, `[InstrumentState]`, `LimiterState`) | one frozen `RecoveryDecisionInput` (21 fields) | production convention |
| taxonomy encoding | module-level string constants | `str`-valued `enum.Enum` (stable string values, never ordinals) | Task 2 |
| clock error | returns `OPERATOR_ESCALATION` envelope | **rejected at construction** (`RecoveryContractError`) + defensive fail-safe in `decide()` | production rejects malformed input at the boundary (convention: cf. `hermes_proposal_validator_v1`) |

## 4. When the prototype ceases to be executable authority

There must be exactly **ONE** future production authority: `utils/hermes_shared_stream_recovery_v1.py`.

- **Now (Phase 1):** NEITHER core is wired. Both are inert. The prototype remains the audited historical record;
  the production module is the audited production baseline. The prototype is the parity oracle only.
- **Phase 2 (shadow):** the shadow adapter imports **only** `utils/hermes_shared_stream_recovery_v1.py`. From the
  first line of Phase-2 code, the prototype is **frozen documentation** and is never imported by runtime or by the
  adapter — only by the parity test.
- **Phase 3+ (authority cutover):** the executor consumes envelopes from the production module only. The prototype
  is never a runtime import at any phase.

The prototype is not deleted (it preserves the audit trail and the parity oracle), but it is **never** the
executable authority. Any future change to the contract lands in the `utils/` module (with the prototype parity
test updated deliberately, or the parity test retired with sign-off if the contract intentionally diverges).
