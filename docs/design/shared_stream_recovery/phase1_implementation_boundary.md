# Shared-Stream Recovery — Phase 1 Implementation Boundary & Phase-2 Handover

**WO:** WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE1-PURE-DECISION-CORE-IMPLEMENTATION-0001
**Authority:** HELM (HERMES market-data lane) · **Status:** INERT / Phase 1 / NOT WIRED / NOT-FOR-MERGE-BY-IMPL-WO
**Created (UTC):** 2026-07-17 · **Base:** canonical `main` `ae80d113`
**Production core:** `utils/hermes_shared_stream_recovery_v1.py`

---

## 1. What Phase 1 IS

A PRODUCTION-owned, versioned, **pure** decision core that, given primitives (transport signals + instrument
observations + limiter state + an **injected** UTC clock), returns one immutable `DecisionEnvelope` carrying a
single action + reason codes + evidence. It is decision-equivalent to the audited PR#108 prototype
(`docs/design/shared_stream_recovery/prototype_parity_matrix_v1.md`). It executes nothing.

## 2. What Phase 1 explicitly does NOT do (all Phase 2+)

Phase 1 does **NOT**:

- observe or read the **live socket** state, provider disconnect callbacks, or any real transport;
- observe or read **live heartbeat** freshness (`AdapterHealth.last_tick_at`);
- observe or read **live shared-stream progress** (the `asyncio.TimeoutError` silent-stall seam);
- observe or read **live per-instrument health** (`hermes_instrument_health` / market-hours classifier output);
- **emit live decision envelopes** onto any bus, log, Redis key, SQL row, or file;
- **request or execute** a reconnect (no full-stream `disconnect()`/`connect()`, no `_recovery_request_pending`);
- **alter** incidents, alerts, the rate limiter, provider schedules, or any watchdog/main behaviour;
- get **imported** by `main.py`, `utils/watchdog.py`, the recovery-request consumer, the provider/OANDA client,
  any startup/runner, `docker-compose*.yml`, `Dockerfile`, cron, or systemd. (Proven: static-guard test
  `tests/test_hermes_shared_stream_recovery_v1.py::test_static_guard_no_runtime_or_infra_imports_the_core` and
  the grep in the evidence bundle. Only tests/schemas/docs may import it.)

The core has **no Redis/SQL contract, no migration, and invents no schedule** for `WTICO_USD` / `SPX500_USD`.
It reads **no wall-clock** (the evaluated timestamp is passed in) and reads **no environment**.

## 3. The three layers (only layer (a) exists in Phase 1)

| Layer | Responsibility | Status |
|---|---|---|
| (a) **pure decision core** | primitives in → `DecisionEnvelope` out; executes nothing | **THIS WO** — `utils/hermes_shared_stream_recovery_v1.py` |
| (b) **thin runtime adapter** | gather live signals, call `decide()`, log the envelope, emit proposal/incident (read-only in shadow) | Phase 2 |
| (c) **side-effect executor** | consume **only** `action == RECONNECT_AUTHORISED`; perform the single full-stream reconnect | Phase 3+ |

---

## 4. The 10 binding Phase-2 handover requirements

A Phase-2 shadow-adapter WO is bound by ALL of the following. None may be silently dropped.

1. **Bound the provider callbacks.** Repeated provider disconnect / reconnect callbacks MUST be bounded
   (debounce + backoff) by the adapter/executor; the pure core is single-shot and stateless and provides
   `adapter_bounding_required=True` on every emergency-bypass decision — the adapter MUST honour it.
2. **Governed heartbeat truth.** Live heartbeat availability/age MUST come from a governed adapter reading
   `AdapterHealth.last_tick_at` (bumped by HEARTBEAT and PRICE), NOT from any single instrument's freshness and
   NOT from REST quote freshness (which is explicitly not transport evidence).
3. **Governed shared-progress truth.** Live shared-stream progress (the silent-stall / no-line-at-all seam) MUST
   come from a governed adapter surfacing the `asyncio.TimeoutError` iterator stall, fed as `shared_stream_silent`
   / shared-progress inputs. Phase 1's shared-progress mirrors heartbeat; Phase 2 introduces the independent
   governed shared-progress adapter.
4. **Governed parser-fatal truth.** "Fatal parser" (every message failing) MUST be a governed adapter signal, not
   inferred from a single parse error (which is advisory).
5. **`RECOVERED` is adapter-emitted.** The core NEVER infers `RECOVERED` from a prior reconnect decision. The
   adapter emits `RECOVERED` only after a proof-window success (control-plane reconnect + resumed heartbeat/flow).
6. **Shadow must be comparable to live.** Every shadow decision MUST be logged in a form directly comparable to
   what the *current* (`sustained[0]`) authority would have done, so the July-16 SPX/WTICO transition can be
   observed proving `NOT_AUTHORISED` before any cutover.
7. **No duplicate authority path.** Phase 2 introduces exactly ONE new decision path (the shadow adapter calling
   the production core). It MUST NOT fork a second, divergent authority alongside the existing one.
8. **No shadow reconnect.** The shadow adapter MUST be strictly read-only: it emits proposals/incidents/logs only
   and performs NO `disconnect()`/`connect()` and sets NO recovery-request flag.
9. **Existing authority unchanged until authorised cutover.** The existing sustained-red (`sustained[0]`)
   authority continues to govern real reconnects throughout Phase 2 and until a signed-off Phase-3 cutover behind
   a rollback flag; Phase 2 changes NO reconnect behaviour.
10. **Every shadow decision carries comparison evidence.** Each emitted envelope MUST carry the `decision_id`,
    reason codes, transport_state, limiter_state and config_version needed to audit it against the live outcome —
    no secrets, UTC only.

---

## 5. Rollback / compatibility

Phase 1 adds no schema, no migration, no required config key, and no runtime import. Immediate rollback to the
deployed image carries no incompatibility: removing the (unimported) module and its test is a no-op for the
running application. The envelope JSON schema was extended additively and remains back-compatible with the
audited prototype envelope (proven by
`tests/test_hermes_shared_stream_recovery_v1.py::test_prototype_envelope_still_validates_backcompat`).
