# Evidence-source trace — LIVE surfaces feeding the Phase-1 core (canonical 8355044b)

Confirmed by reading `adapters/oanda.py`, `adapters/base.py`, `utils/watchdog.py`, `main.py oanda_stream_task`.
Every claim below is code-supported; where the code has NO source, the field is marked UNAVAILABLE (never defaulted
healthy).

## Transport surfaces
- **socket_state** — `adapters/base.AdapterState` via `_set_state` (`DISCONNECTED` set by `oanda.disconnect()`).
  CAVEAT (load-bearing): `main.oanda_stream_task` runs its OWN loop; `oanda.connect()` **never** calls
  `_set_state(CONNECTED)`. So the positive "connected" flag is UNPROVEN — only DISCONNECTED/FAILED are
  authoritative. `watchdog._current_stream_state` (CONNECTED_UNPROVEN/FLOWING/RECOVERING) is the actively-maintained
  control-plane truth. => AVAILABLE_AUTHORITATIVE for down transitions; AMBIGUOUS for "connected".
- **heartbeat** — `adapters/base.AdapterHealth.last_tick_at`, bumped in `oanda.stream()` on `HEARTBEAT` (~5 s) AND
  `PRICE` (`oanda.py:141`). GENUINE stream-liveness surface, instrument-independent. heartbeat_available /
  heartbeat_age_s = AVAILABLE_DERIVED (REAL, not invented).
- **shared_progress** — SAME `last_tick_at` read as any-message liveness (distinct from per-instrument freshness).
  shared_stream_silent = `main` `asyncio.TimeoutError` on `stream_iter.__anext__` (main.py:707). AVAILABLE_DERIVED.
- **provider_disconnect_event** — `main` outer `except Exception` (main.py:912) with retry_count/reconnect_attempt;
  exponential backoff; retry_count reset on connect. AVAILABLE_DERIVED. Each `connect()` = a new connection
  generation.
- **auth_failure** — `oanda.connect()` 200-check on `/v3/accounts` (oanda.py:83); `oanda.stream()` status ≠ 200
  (oanda.py:126) → 401/403. AVAILABLE_DERIVED.
- **parser_fatal** — `oanda.stream()`: ordinary `json.JSONDecodeError` → `warning; continue` (RECOVERABLE, not
  fatal); other `except` → `_record_error; continue` (recoverable). FATAL = the `async for` / task itself
  terminating. So parser_fatal is distinguishable; ordinary malformed cannot manufacture a bypass. AVAILABLE_DERIVED.
- **reconnect_in_progress** — `watchdog.stream_state == RECOVERING`; `main.retry_count>0`. AVAILABLE_DERIVED.
- **error_count_delta** — `AdapterHealth.error_count`. AVAILABLE_DERIVED (advisory).
- **provider_maintenance_indication** — OANDA emits NO maintenance signal → NOT_CURRENTLY_AVAILABLE / UNSAFE_TO_INFER
  → value False, never inferred.

## Instrument + control surfaces
- **instruments** — `watchdog._instrument_health` / `_instrument_last_tick` / `_instrument_last_m1`, classified by
  the injected governed `_market_truth_checker` (`DstAwareMarketHours.is_truth_expected`). Advisory to transport.
  WTICO_USD / SPX500_USD schedule None → validated=False → no transport vote.
- **limiter** — `watchdog._recovery_attempts_window` (list[UTC], trimmed to last hour); config (migration 012):
  per_instrument_sustained_red_threshold_sec=300, per_instrument_recovery_cooldown_sec=600,
  per_instrument_max_recovery_attempts_per_hour=3. AVAILABLE_AUTHORITATIVE (control).

## Current-authority surfaces (PASSIVE observation only)
- `_per_instrument_red_since` (sustained-red set), `_recovery_request_pending` / `_recovery_request_reason`
  (parsed `instrument=…` → triggering instrument), `_last_recovery_request_at`, `_recovery_attempts_window`.
  The DEFECT: `_evaluate_per_instrument_recovery` authority = `sustained[0]` (a single instrument's freshness),
  with NO transport check (watchdog.py:691). Observed read-only; never called/wrapped.
