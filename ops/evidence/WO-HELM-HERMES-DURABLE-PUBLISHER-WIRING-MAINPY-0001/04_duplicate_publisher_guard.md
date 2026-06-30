# 04 — Duplicate-publisher guard (Part B)

## The risk
If a future activation runs the in-process supervisor **while the detached `/tmp` loops are still running**, both
would publish the same governed key families (control-plane/indicators/candle_features/sessions/levels) — racing
writes, conflicting freshness, double cadence.

## The guard (defence in depth)
1. **Explicit process-ownership marker** — `HERMES_PUBLISHER_RUNTIME_OWNER` (default `detached`). The in-process
   supervisor `.start()` raises `ValueError GOV-HERMES-PUBRT-002` unless `OWNER=in_process`. So enabling the
   supervisor is **not enough** — operator must explicitly transfer ownership to the in-process path.
2. **Env switch disabling the detached path** — the documented operational sequence (below) sets
   `OWNER=in_process` only *after* the detached loops are stopped; the same marker is the single source of truth.
3. **Default-disabled supervisor** — `HERMES_PUBLISHER_RUNTIME_ENABLED` unset → no in-process publishing at all.

> Tested: `test_duplicate_guard_refuses_unless_in_process` (start refuses on `detached`) and
> `test_duplicate_guard_allows_in_process_owner` (start runs only on `in_process`).

## Documented operational sequence (for the future ACTIVATE WO — NOT executed here)
1. Deploy the image carrying this code (supervisor still disabled).
2. **Stop the detached `/tmp` loops** (`pkill -f control_plane_publisher.py` … for all four) — verify none alive.
3. Set on the container: `HERMES_PUBLISHER_RUNTIME_ENABLED=true`, `HERMES_PUBLISHER_RUNTIME_AUTHORISED=true`,
   `HERMES_PUBLISHER_RUNTIME_OWNER=in_process` (plus the existing per-family ENABLED/AUTHORISED gates already set).
4. Restart the service → supervisor starts its runners in-process; publishers now survive restart.
5. Verify single-writer (one publisher per key family) + heartbeat fault counters GREEN.

## What this PR does NOT do
Does **not** stop the detached loops, does **not** set `OWNER=in_process`, does **not** enable the supervisor.
The guard is code + a documented sequence; the cutover is a separate authorised WO.
