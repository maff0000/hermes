# 07 — Fault isolation, fault counters, graceful shutdown (Part A)

## Exception isolation (one publisher fault never kills the app)
`PublisherRunner._loop` wraps each `step_fn` call:
- `except SystemExit: raise` — a gate fail-loud (`enabled-without-authorised`) propagates, never swallowed.
  Tested: `test_systemexit_from_step_propagates_not_swallowed`.
- `except Exception:` — increments `metrics["faults"]`, records `metrics["last_fault"]` (repr, truncated),
  rate-limited `logger.warning [PUB_RUNTIME_FAULT]`, and **continues the loop**. A successful run is never claimed
  on a fault (`runs` not incremented). The thread (and the app) keep running.
  Tested: `test_runner_exception_isolation_counts_and_survives` (faults≥1, runs==0, thread alive→graceful join).

A faulting runner is **isolated** to itself: the supervisor starts each runner in its own daemon thread, so one
family's fault does not stop the others.

## Fault counters surfaced to control-plane (health/heartbeat)
- `PublisherRunner.status()` → `{name, interval_seconds, runs, faults, published, last_fault}`.
- `HermesPublisherSupervisor.status()` → `{enabled, owner, started, runners:[…]}`;
  `fault_summary()` → `{name: faults}`. These are designed to feed `control_plane_step`'s heartbeat
  `fault_counters_summary` / health surfaces in the activate WO (so a silently-faulting publisher is visible, per
  the fail-loud-resilience doctrine). Tested: `test_duplicate_guard_allows_in_process_owner` asserts
  `fault_summary() == {"x": 0}` for a healthy runner.

## Bounded loops
Each runner sleeps via `self._stop.wait(interval)` (interval floor 1s) — bounded cadence, never a tight spin.

## Graceful startup
Supervisor built in lifespan startup; build/guard faults (other than `SystemExit`) are logged loud and the service
continues **without** the supervisor (`state.publisher_supervisor=None`) rather than aborting boot — so a misconfig
(e.g. `OWNER` wrong) degrades visibly, not catastrophically.

## Graceful shutdown
Lifespan shutdown calls `supervisor.stop()` → sets each runner's stop Event (waking it immediately from
`wait`) and `join(timeout=5)`. Tested: runners report `not _thread.is_alive()` after `stop()`
(`test_runner_counts_published_and_graceful_join`, `test_runner_exception_isolation_counts_and_survives`).
