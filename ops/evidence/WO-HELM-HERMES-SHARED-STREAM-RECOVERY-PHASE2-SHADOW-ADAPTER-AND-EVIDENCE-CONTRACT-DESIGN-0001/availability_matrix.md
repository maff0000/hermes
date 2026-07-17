# Evidence availability matrix — all 21 RecoveryDecisionInput fields (one line each)

Class ∈ {AVAILABLE_AUTHORITATIVE, AVAILABLE_DERIVED, AVAILABLE_ADVISORY, NOT_CURRENTLY_AVAILABLE, AMBIGUOUS,
UNSAFE_TO_INFER}. Missing ≠ favourable default.

1.  evaluated_at_utc               — AVAILABLE_AUTHORITATIVE (auth) — snapshot capture UTC, injected clock.
2.  provider                       — AVAILABLE_AUTHORITATIVE (auth) — constant "OANDA".
3.  contract_version               — AVAILABLE_AUTHORITATIVE (auth) — constant "1".
4.  config_version                 — AVAILABLE_AUTHORITATIVE (auth) — governed market-hours config "3".
5.  socket_state                   — AVAILABLE_AUTHORITATIVE for DISCONNECTED/FAILED; AMBIGUOUS for "connected" (UNPROVEN — connect() never sets CONNECTED on main loop).
6.  provider_disconnect_event      — AVAILABLE_DERIVED (auth) — main outer-except / StopAsyncIteration.
7.  auth_failure                   — AVAILABLE_DERIVED (auth) — connect() False / stream 401/403.
8.  heartbeat_available            — AVAILABLE_DERIVED (auth) — AdapterHealth.last_tick_at presence (HEARTBEAT+PRICE).
9.  heartbeat_age_s                — AVAILABLE_DERIVED (auth) — now − last_tick_at (live-observed; INFERRED in July-16 replay, never logged then).
10. shared_stream_silent          — AVAILABLE_DERIVED (auth) — asyncio.TimeoutError silent-stall.
11. parser_fatal                  — AVAILABLE_DERIVED (auth) — stream async-for/task termination (NOT ordinary malformed).
12. reconnect_in_progress         — AVAILABLE_DERIVED (auth) — StreamState.RECOVERING / retry_count>0.
13. shared_progress_available     — AVAILABLE_DERIVED (auth) — last_tick_at any-message liveness (Phase-1 mirrors heartbeat).
14. shared_progress_age_s         — AVAILABLE_DERIVED (auth) — same surface as #13.
15. provider_maintenance_indication— NOT_CURRENTLY_AVAILABLE / UNSAFE_TO_INFER (adv) — OANDA emits no signal → value False, never inferred.  [UNAVAILABLE]
16. error_count_delta             — AVAILABLE_DERIVED (adv) — AdapterHealth.error_count delta.
17. heartbeat_soft_horizon_s      — AVAILABLE_AUTHORITATIVE value (auth); BASIS PROVISIONAL (OANDA HB ~5 s → soft ~15 s), Phase-2 calibration.
18. heartbeat_hard_horizon_s      — AVAILABLE_AUTHORITATIVE value (auth); BASIS PROVISIONAL (~45–90 s), Phase-2 calibration.
19. instruments[]                 — AVAILABLE_DERIVED (advisory to transport) — watchdog _instrument_health + governed DstAwareMarketHours; WTICO/SPX validated=False.
20. limiter                       — AVAILABLE_AUTHORITATIVE (control/auth) — watchdog _recovery_attempts_window (+ store availability).
21. evidence_summary             — AVAILABLE_ADVISORY (adv) — adapter-composed (snapshot_id/source_sha/gen/completeness).

## UNAVAILABLE fields (+ why)
- provider_maintenance_indication — OANDA provides no explicit maintenance signal; classifying it from a metals
  break would be UNSAFE_TO_INFER → marked unavailable, inert value False. NEVER inferred.
- socket_state "connected" (positive) — AMBIGUOUS/UNPROVEN on the current deployment (connect() does not set
  CONNECTED on main.py's custom loop); treated as UNPROVEN → the design leans on heartbeat, not the connected flag.

Every AUTHORITY-BEARING field (#5–#14, #17–#20) has a truthful source. The only genuinely-unavailable field is
advisory (#15), so no authority-bearing decision depends on an invented signal.
