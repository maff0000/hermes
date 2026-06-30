# 03 — Publisher wiring plan (Part A)

## Components
### `utils/hermes_publisher_runtime_v1.py`
- `PublisherRunner(name, step_fn, interval_seconds)` — one governed step on a bounded interval in a **daemon
  thread**. `_loop` catches `Exception` (counts `faults`, records `last_fault`, rate-limited log; loop continues —
  exception isolation) but **re-raises `SystemExit`** (gate fail-loud must propagate). `_stop = threading.Event`;
  `self._stop.wait(interval)` → bounded + interruptible (graceful). `start(client)` / `stop(timeout=5)` (join).
  `status()` → `{name, interval, runs, faults, published, last_fault}`.
- `HermesPublisherSupervisor(runners, owner, redis_client)` — `start()` enforces the duplicate-publisher guard
  (`owner == in_process` else `ValueError GOV-HERMES-PUBRT-002`), then starts each runner; `stop()` stops all;
  `status()` / `fault_summary()` for control-plane heartbeat/health.
- `DisabledPublisherSupervisor` — no-op default (no threads, no client, no I/O).
- `build_publisher_supervisor_from_env(redis_client_factory=None, runner_specs=None)` — the gate:
  disabled→Disabled; enabled-without-authorised→`SystemExit(101)`; enabled+authorised→supervisor (NOT started).
  Redis client built lazily **only** on the enabled path. `default_runner_specs()` → the 4 governed families.

### `utils/hermes_runtime_publisher_steps_v1.py`
Client-injected step functions (ports of the detached scripts), each gate-first (no-op when its family disabled):
| Step | Builds via | Reads (bounded) | Writes |
|---|---|---|---|
| `control_plane_step` | `cp.build_control_plane_from_env()` | family `:exists`, candle `:latest` freshness | manifest/catalog/health (persistent) + heartbeat (TTL 180) |
| `indicator_step` | `ind.build_indicator_publisher_from_env()` | last 60 closed H-istory per TF | `hermes:indicators:XAU_USD:{TF}:v1` |
| `candle_feature_step` | `feat.build_candle_feature_publisher_from_env()` | last 2 closed candles per TF | `hermes:candle_features:XAU_USD:{TF}:v1` |
| `sessions_levels_step` | `sess.*` + `lvl.build_level_publisher_from_env()` | `trading_windows` (SQL read) + today's H1 | `hermes:sessions:…` + `hermes:levels:…:{session,intraday}:v1` |

### `main.py` lifespan
- Startup (after `candle_h4_producer`): `state.publisher_supervisor = _build_publisher_supervisor()`; if
  `enabled` → `.start()` (logs owner + runner count) else log disabled no-op. `SystemExit` re-raised (fail loud);
  any other build/guard error logged loud and the service continues **without** the supervisor (no app crash).
- Shutdown (after healthcheck reporter stop): `state.publisher_supervisor.stop()` (graceful thread join).
- `ServiceState.publisher_supervisor = None` default added.

## Requirement coverage
preserve env gates ✓ · authorised flags ✓ · instrument allowlists ✓ · timeframe/scope allowlists ✓ · D1 gates ✓ ·
no Redis I/O at import ✓ · no publisher starts at import ✓ · no hidden defaults ✓ · fail loud if enabled w/o
authorised ✓ · bounded loops ✓ · graceful startup/shutdown ✓ · exception isolation ✓ · fault counters exposed
(`status()`/`fault_summary()` for heartbeat/health) ✓ · no duplicate publishing (Part B guard) ✓.
