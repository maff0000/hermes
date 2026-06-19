# Evidence — WO-HELM-HERMES-CANDLE-FORWARD-RUNTIME-WIRE-INERT-0001

**Verdict:** GREEN_CANDLE_FORWARD_RUNTIME_WIRE_INERT_PR_READY · **UTC:** 2026-06-19T14:10Z · Repo maff0000/hermes
**Inert wire only. No deploy, no restart, no Redis/SQL write, no backfill, no consumer work.**

## Base
origin/main e818bc8e (PR #28 foundation). Branch wo/WO-HELM-HERMES-CANDLE-FORWARD-RUNTIME-WIRE-INERT-0001. Runtime checkout /srv-dev/tradingSignals on 6eeb590 (UNTOUCHED).

## Runtime seam added
- utils/candle_runtime_seam_v1.py — build_candle_forward_seam_from_env(): master gate HERMES_CANDLE_FORWARD_ENABLED
  (default false -> DisabledCandleEmitter, no sink, no write, no warning spam). Enabled requires explicit
  HERMES_CANDLE_FORWARD_SINK (no hidden default -> fail-loud); only none/inert (NoWriteCandleSink) permitted this WO;
  any write mode -> fail-loud GOV-CANDLE-FWD-SEAM-002. No Proteus/stale-SQL fallback, no canonical writer.
- main.py (minimal, mirrors the proven tick-shadow seam):
  * import build_candle_forward_seam_from_env as _build_candle_forward_seam
  * ServiceState.candle_forward_emitter = None
  * startup: state.candle_forward_emitter = _build_candle_forward_seam() (try/except fail-loud boot log)
  * candle-complete site: guarded inert state.candle_forward_emitter.emit(candle=candle) (no-op; exception-swallowed; never affects persistence)

## Config added (governed; default disabled)
.env.example: HERMES_CANDLE_FORWARD_ENABLED=false (+ commented HERMES_CANDLE_FORWARD_SINK). Live .env NOT changed.
ops/config/candle_forward_runtime_seam.md: description + JSON-valid llm_reasoning + owner (HELM/HERMES) + default_disabled + activation gate + fault codes.

## Default state
DISABLED. Runtime constructs DisabledCandleEmitter (no-op). Normal tick path unaffected.

## Tests added
tests/test_candle_forward_runtime_seam_v1.py (6): disabled->no-op emitter; disabled needs no sink config;
enabled-without-sink fails loud; enabled-with-write-sink (shadow/canonical/live/redis) forbidden fail-loud;
enabled-inert -> InertCandleForwardSeam + NoWriteCandleSink, emit writes nothing; no canonical writer / no Proteus fallback.

## Test results
pytest tests/test_candle_forward_runtime_seam_v1.py tests/test_candle_contract_v1.py tests/test_candle_publisher_v1.py
=> 46 passed. main.py AST OK. (maff0000/hermes has no CI workflow; local pytest is the gate.)

## No-write proof
Disabled emitter (default) + InertCandleForwardSeam both no-op; uses NoWriteCandleSink (never holds a live client).
Seam contains no canonical/shadow writer, no hermes:candles: live-writer literal, no SerializingCandleShadowWriter.

## Proteus fallback scan
No 'tradingProteus' / '/srv-dev/tradingProteus' / 'proteus_shared'/'shared' import / 'structure_engine' fallback in changed files.

## Redis candle keys (read-only, before==after; WO writes nothing)
proteus-redis(6379): hermes:candles:*=0, hermes:shadow:candles:*=0. proteus-redis-dev(6380): candles=0 shadow=0.

## SQL mutation proof
No SQL executed by seam/tests. No migration applied. No candle-table mutation.

## Runtime / deploy / restart
No deploy. signal-service-dev NOT restarted (PID 2929503 active; runtime checkout 6eeb590 unchanged). No live .env change.

## Candle-truth status
Still OPEN — forward candle production not activated; this WO only makes the runtime structurally aware (inert).

## Pre-existing observation (NOT this WO)
.env.example carries 2 non-empty password placeholders (DB_PASSWORD/REDIS_PASSWORD) pre-existing on main; my diff is +4 clean candle lines only. Recommend HERMES scrub .env.example to empty placeholders separately (out of scope here).

## R2D2 audit request: helm:comms:r2d2:hermes_candle_forward_runtime_wire_inert_audit_request
