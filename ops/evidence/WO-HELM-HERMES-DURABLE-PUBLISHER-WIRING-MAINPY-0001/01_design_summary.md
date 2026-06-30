# 01 — Design summary

**WO:** WO-HELM-HERMES-DURABLE-PUBLISHER-WIRING-MAINPY-0001
**Persona:** HELM (HERMES lane) · **Mode:** CODE-ONLY · **Base:** `2add81d053a0d36a21491fd3c80f10c698c84a85`

## Goal
Move the currently **detached dev-loop** governed publishers (control-plane, indicators, candle_features,
sessions, levels) into **durable in-process HERMES runtime wiring** so they live/die with the service and survive
container restart — preserving every existing gate, ownership boundary, and Redis contract. Plus add explicit
**closed-H1 level semantics** (R2D2 finding). No deploy, no restart, no activation, no Redis/SQL write.

## Shape delivered
1. **Supervisor framework** (`utils/hermes_publisher_runtime_v1.py`) — generic, governed, gated, exception-isolated,
   bounded, graceful-start/stop. DISABLED by default → `DisabledPublisherSupervisor` (no-op, no threads, no client).
   ENABLED-without-AUTHORISED → `SystemExit(101)`. ENABLED+AUTHORISED → `HermesPublisherSupervisor` (built, NOT
   started). `.start()` enforces the **duplicate-publisher guard** (`HERMES_PUBLISHER_RUNTIME_OWNER=in_process`).
2. **Durable publish steps** (`utils/hermes_runtime_publisher_steps_v1.py`) — repo-resident, client-injected ports
   of the four `/tmp` detached scripts. Each step builds its family publisher via the **existing `*_from_env`
   factory** (all gates preserved), reads governed candle keys (bounded), computes via existing HERMES modules +
   merged builders (validated), writes only governed versioned keys. No module-level redis client; no I/O at import.
3. **main.py lifespan wiring** — supervisor built in startup (gated disabled), `.start()`ed only when enabled;
   `.stop()`ed (joins threads) in shutdown. Default path is a logged no-op.
4. **Closed-H1 level semantics** (`utils/hermes_levels_v1.py`) — `level_semantics` block on every level payload.

## Inert by construction (code-only)
- Default env leaves the supervisor **disabled** → DisabledPublisherSupervisor no-op (the live container is
  unaffected by this PR even after a future deploy of the image, until the activate WO sets the gates).
- The duplicate-publisher guard means even if someone sets ENABLED+AUTHORISED, the supervisor **refuses to start**
  unless `OWNER=in_process` — so the detached loops (still running) can never be double-published against.
- D1 stays GATED (LATEST_TFS excludes D1; levels D1-derived scopes gated until D1 latest GREEN).
- No auth/ACL/credential/NOAUTH work. No regime/risk/decision/trade/signal fields. No legacy mutation.
