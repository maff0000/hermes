# 10 — No-I/O / no-deploy proof

## No Redis I/O / no publisher starts at import
- `utils/hermes_publisher_runtime_v1.py` — no top-level `import redis` / `from redis` (redis imported lazily only
  inside `_default_redis_client`, called only on the enabled path); no thread started at import; no `.start()` at
  module level. Tests: `test_no_module_level_redis_or_starts_at_import`, `test_no_publisher_threads_started_at_import`.
- `utils/hermes_runtime_publisher_steps_v1.py` — **no module-level redis client at all** (every step takes an
  injected `client`); no Redis/SQL/file I/O at import (SQL `pymysql` imported lazily inside the window/config
  readers only). Steps are **gate-first**: when a family gate is disabled they return `{"published": 0}` and never
  touch the client. Test `test_steps_noop_and_no_io_when_family_disabled` injects a `_BoomClient` that raises on
  **any** attribute access and asserts all four steps no-op without touching it.
- The supervisor `build_*_from_env` builds NO Redis client on the disabled path (`DisabledPublisherSupervisor`).

## No deploy / no restart / no activation / no Redis write / no SQL write (this PR)
- **Code-only.** No image build, no `docker compose up`, no container restart, no env-gate change on any container.
- The default env leaves `HERMES_PUBLISHER_RUNTIME_ENABLED` unset → supervisor is a logged no-op even if/when the
  image is later deployed, until the separate activate WO sets the gates + `OWNER=in_process`.
- No `redis.set/zadd/setex` executed by this WO; the step write-paths run only inside the (disabled) supervisor.
- No SQL write — the only SQL is the **read-only** `SELECT … FROM trading_windows` + classification config read
  (pre-existing governed reads), and only on the enabled path.
- Detached `/tmp` loops NOT stopped. `market_map.py` not killed. `market-map-dev.service` not stopped/modified.
  Legacy `hermes:market_map:*` not deleted/mutated. No consumer cutover.

## Verification commands run
- `python3 -m py_compile main.py utils/hermes_publisher_runtime_v1.py utils/hermes_runtime_publisher_steps_v1.py
  utils/hermes_levels_v1.py` → OK.
- `git diff --cached --stat origin/main` → 5 files (main.py, 2 new utils, 1 new test, hermes_levels_v1.py); no
  cross-app paths; no compose/env/Dockerfile changes.
