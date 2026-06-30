# 11 — Final report

**WO:** WO-HELM-HERMES-DURABLE-PUBLISHER-WIRING-MAINPY-0001 · **Persona:** HELM (HERMES lane) · **Mode:** CODE-ONLY
**Verdict:** `GREEN_PR_OPEN_HERMES_DURABLE_PUBLISHER_WIRING_MAINPY_CODE_ONLY`
**Base SHA:** `2add81d053a0d36a21491fd3c80f10c698c84a85`
**Branch:** `wo/WO-HELM-HERMES-DURABLE-PUBLISHER-WIRING-MAINPY-0001`

## What shipped (code-only, inert)
1. **Durable in-process publisher supervisor** (`utils/hermes_publisher_runtime_v1.py`) — generic, governed, gated,
   exception-isolated, bounded, graceful start/stop, fault counters. DISABLED by default; enabled-without-authorised
   → `SystemExit(101)`.
2. **Durable publish steps** (`utils/hermes_runtime_publisher_steps_v1.py`) — repo-resident, client-injected ports
   of the four detached `/tmp` scripts (control-plane, indicators, candle_features, sessions+levels). Gate-first;
   no module-level redis; no I/O at import.
3. **main.py lifespan wiring** — supervisor built+started in startup (gated disabled → no-op), stopped in shutdown.
4. **Duplicate-publisher guard** (Part B) — `HERMES_PUBLISHER_RUNTIME_OWNER=in_process` required to start +
   documented operational cutover sequence.
5. **Closed-H1 level semantics** (Part C) — `level_semantics` block on every level payload; validation hardened.

## Tests — 104 passed (see 08_tests.log)
New `tests/test_hermes_publisher_runtime_v1.py` (28) + existing sessions/levels (22) + indicators + candle_features
+ control-plane all green. Covers: disabled-default, enabled-without-authorised→101, no-Redis-IO-at-import,
no-starts-at-import, D1-gate preserved, duplicate guard (refuse/allow), fault isolation + counters + graceful join,
SystemExit propagation, family no-op + no-client-touch when disabled, closed-H1 semantics + validation, no-auth,
no-ARES-interpretation.

## Explicit statements
- **No deploy. No restart. No activation. No Redis write. No SQL write.** Code-only PR.
- **No auth / ACL / NOAUTH / credential / Redis-security-posture change. No new secret.**
- **No regime / risk / decision / trade / signal fields. No ARES interpretation.**
- Detached `/tmp` loops NOT stopped. `market_map.py` not killed. `market-map-dev.service` untouched. Legacy
  `hermes:market_map:*` not deleted/mutated. No consumer cutover. No D1 history / no D1-derived levels.
- No opportunistic refactor. No cross-app edits (5 files, all HERMES).

## Next recommended gate
Separate **ACTIVATE** WO (build + deploy the image, then the documented cutover in 04): stop the four detached
loops → set `HERMES_PUBLISHER_RUNTIME_ENABLED/AUTHORISED=true` + `OWNER=in_process` → restart → verify single-writer
per family + heartbeat fault counters GREEN. (Then, separately: consumer-cutover to retire `hermes:market_map:*`;
feed_health + instrument_catalog families; D1-derived surfaces once D1 latest GREEN.)
