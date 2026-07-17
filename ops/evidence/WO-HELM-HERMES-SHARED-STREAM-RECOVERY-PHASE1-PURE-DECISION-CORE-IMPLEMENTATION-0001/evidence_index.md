# Evidence Index — SSR Phase 1 Pure Decision Core (PRODUCTION-OWNED, INERT)

**WO:** WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE1-PURE-DECISION-CORE-IMPLEMENTATION-0001
**Authority:** HELM (HERMES market-data lane) · **Created (UTC):** 2026-07-17
**Base:** canonical `main` `ae80d113` (contains merged PR#108 design contract)
**Production core:** `utils/hermes_shared_stream_recovery_v1.py` · **Tests:** `tests/test_hermes_shared_stream_recovery_v1.py`

## Bundle contents

| File | Purpose |
|---|---|
| `evidence_index.md` | This index + production-ownership rationale + contract models + decision precedence + determinism. |
| `reason_code_matrix.md` | The 18 audited reason codes + actions + authority states + 9 transport states. |
| `fixture_envelopes.json` | Rendered envelopes for July-16, all genuine-fault, and fail-safe fixtures. |
| `inertness_proof.txt` | Grep proof the core is imported by NO runtime/infra path (only module + test). |
| `test_output.txt` | pytest capture: 90 core+design PASS; 181 regression PASS. |
| `verdict.txt` | Verdict + scope statement. |
| `SHA256SUMS` | Integrity digests for this bundle. |
| `../../../docs/design/shared_stream_recovery/prototype_parity_matrix_v1.md` | Prototype↔production parity matrix (Task 11). |
| `../../../docs/design/shared_stream_recovery/phase1_implementation_boundary.md` | Phase-1 boundary + 10 Phase-2 handover reqs (Task 17/18). |

## 1. Production-ownership rationale (why `utils/`, not `design/`)

HELM decided the production pure core lives at `utils/hermes_shared_stream_recovery_v1.py`, matching the two
existing production pure-domain cores (`utils/hermes_market_hours_health_v1.py`,
`utils/hermes_proposal_validator_v1.py`): frozen dataclasses, stdlib-only, injected clock, versioned, module
header with change history. `design/` holds audited prototypes; the audited PR#108 prototype
(`design/shared_stream_recovery_contract_v1.py`) is preserved UNCHANGED as the historical artefact and the parity
oracle. A permanently-`design/`-resident core could never be the module a Phase-2 adapter imports; promoting it
now — while still INERT — establishes one production home and one audited parity baseline before any wiring.

## 2. Contract models summary

- **Input** `RecoveryDecisionInput` (frozen, 21 fields): contract_version, evaluated_at_utc (tz-aware UTC,
  rejected otherwise), provider, config_version, socket_state, provider_disconnect_event, auth_failure,
  heartbeat_available+age, shared_stream_silent, parser_fatal, reconnect_in_progress, shared_progress_available+age
  (Phase-1 mirrors heartbeat), advisory maintenance/error-delta, heartbeat soft/hard horizons, `instruments`
  (tuple of frozen `InstrumentObservation`, canonically sorted by name), `limiter` (`LimiterState`),
  evidence_summary. Derived canonical collections: expected_flow / stale_governed / stale_unvalidated /
  expected_closed / reopening_grace instruments.
- **Output** `DecisionEnvelope` (frozen, 24 fields incl. contract_version + envelope_version): decision_id
  (deterministic sha256[:16] over the canonical INPUT, or a supplied correlation id — no randomness/Date.now),
  transport_state, socket_connected, heartbeat_state+age, shared_progress_state+age, the three instrument
  collections, authority_status, action, ordered reason_codes, reconnect_authorised, emergency_bypass_eligible +
  emergency_bypass_reason + adapter_bounding_required, limiter_state, evidence_summary, config_version.
  `to_json()` is stable; `envelope_from_dict()` fails safely on an unsupported envelope version.
- **Taxonomies** (`str`-valued `enum.Enum`, stable string values, never ordinals): Action (6), AuthorityStatus (4),
  TransportState (9; RECOVERED is adapter-supplied, never core-inferred), ReasonCode (18), SocketState (6),
  HeartbeatState (4).

## 3. Decision precedence (ordered)

0. malformed / unsupported contract-version → **fail-safe** `OPERATOR_ESCALATION` (no reconnect).
1. reconnect already in progress → `NO_ACTION` (no duplicate authority).
2. genuine socket-down / provider-disconnect → `RECONNECT_AUTHORISED` (emergency; narrow limiter bypass).
3. auth/session failure → `RECONNECT_AUTHORISED` (emergency).
4. fatal parser / shared-stream silent → `RECONNECT_AUTHORISED` (emergency).
5. `SILENT_UNCONFIRMED` (hb hard-stale/missing or socket unknown):
   a. ALL validated expected-flow instruments stale → `RECONNECT_AUTHORISED` (application-derived; limiter applies);
   b. all-governed-closed / no validated expected-flow → **fail closed** (`OPERATOR_ESCALATION` if unvalidated stale, else `NO_ACTION`);
   c. some validated instrument still progressing → transport alive → `RECOVERY_PROPOSAL_ONLY` / `NO_ACTION`.
6. transport proven alive (`TRANSPORT_HEALTHY`/`TRANSPORT_DEGRADED`): all-governed-closed → `NO_ACTION`; else stale
   instruments → `RECOVERY_PROPOSAL_ONLY` (never a transport mutation).

Genuine transport evidence OUTRANKS ordinary freshness. Ordinary freshness NEVER invokes the emergency bypass and
NEVER alone authorises a shared reconnect. Missing/contradictory transport truth FAILS CLOSED.

## 4. Determinism & validation results (all PASS)

- identical canonical input → identical `decision_id` and identical `to_json()`.
- input instrument-collection ORDER does not change the decision (instruments canonically sorted).
- reason-code order deterministic (dedup, stable insertion order per branch).
- naive timestamp / non-UTC timestamp / negative age / malformed limiter → **rejected** at construction
  (`RecoveryContractError`).
- unsupported contract-version → fail-safe `OPERATOR_ESCALATION`; unsupported envelope-version → decode rejected.
- no input mutated; output frozen; serialisation stable; envelope round-trips through dict.

## 5. Prototype parity (Task 11)

14 shared scenarios: production and prototype yield **identical action + reconnect authority + reason-code set +
transport_state**. Representational fields differ by design (documented in the parity matrix); decisions do not.
Enforced by `test_prototype_parity_action_reconnect_and_reasons`.

## 6. Redis/SQL boundary

NONE. No new Redis key, no SQL table, no migration, no schedule invention. Pure in-process function; stdlib only.
