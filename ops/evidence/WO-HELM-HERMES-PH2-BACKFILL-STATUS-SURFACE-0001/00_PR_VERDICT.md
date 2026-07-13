# WO-HELM-HERMES-PH2-BACKFILL-STATUS-SURFACE-0001 — HELM PR verdict

## GREEN_PH2_BACKFILL_STATUS_SURFACE_PR_READY
Tracking: HELM_HERMES_PH2_BACKFILL_STATUS_SURFACE_PR::2026-07-13T08:05Z::GREEN_PH2_BACKFILL_STATUS_SURFACE_PR_READY

Mode: CODE BUILD PR ONLY. No runtime mutation, no deploy, no activation, no publication. Branch off main 23fbf31.
Authority: R2D2_HERMES_PH2_GAPS_SURFACE_ACTIVATION_AUDIT::2026-07-13T07:27:40Z::GREEN_PH2_GAPS_SURFACE_ACTIVATION_AUDIT_APPROVED

## Design choice (stated plainly, per WO §5)
PREFERRED path: pure builder + validation + DISABLED publisher only. NO SET path, NO runtime wiring in this WO. The enabled
BackfillStatusPublisher is an analyser with analyze() only (no publish/SET) — same inert shape PR#89 shipped, proven auditable.
Publication + runner wiring are a SEPARATE later WO. This WO cannot and does not publish hermes:backfill:status:XAU_USD:v1.

## Changed files (additive; exact.diff)
- utils/hermes_backfill_status_v1.py (NEW) — pure status builder + validation + read-only reader + disabled publisher.
- tests/test_hermes_backfill_status_v1.py (NEW) — 15 status-only tests.

## Schema summary (single aggregate key hermes:backfill:status:XAU_USD:v1)
Top-level: schema_version, publisher, instrument, canonical_instrument, generated_at_utc, source, consumer_live,
execution_enabled, backfill_executed, repair_executed, deterministic_only, status_order, overall_status, active_job,
last_completed_job, blocked_reason, rate_limit_tokens, completed_pct, timeframes, d1, gaps_source, caveats.
Per-tf: timeframe, gap_state, history_depth, min_required_depth, sufficient, retention_policy, backfill_path_available,
backfill_gates, forward_writer_gates, last_backfill_run_marker, status, blocked_reason.
D1: history_depth, min_required_depth, latest_open_utc, history_newest_open_utc, d1_boundary_state,
forward_writer_enabled, forward_writer_authorised, backfill_path_available, status.
status vocab (WO-suggested; R2D2 may amend): SOURCE_MISSING, GAPS_SURFACE_MISSING, GAPS_FOUND, READY_FOR_BACKFILL_DESIGN,
IDLE, INSUFFICIENT_HISTORY, STALE, RATE_LIMITED, STALLED, BLOCKED, OK.

## Status interpretation (fail-closed; NEVER OK in this WO)
- gaps key missing/unparseable/invalid -> overall_status=GAPS_SURFACE_MISSING (never OK)
- gaps overall_gap_state=GAPS_FOUND -> overall_status=READY_FOR_BACKFILL_DESIGN, blocked_reason=NO_BACKFILL_EXECUTOR_CONFIGURED
- gaps OK/MARKET_CLOSED/OUT_OF_RETENTION -> IDLE (no live recoverable gap); SOURCE_MISSING/INSUFFICIENT_HISTORY/STALE mirror
- overall_status is REJECTED by validation if OK (GOV-HERMES-BFS-006) — no completion claim without governed evidence (none)

## Source inputs
- hermes:gaps:XAU_USD:v1 (GET, read-only) = primary input
- injected/read-only env gate VALUES for telemetry: D1 seed/backfill gates (HERMES_D1_HISTORY_BACKFILL_*), D1 forward gates
  (HERMES_CANDLE_D1_HISTORY_*), M1-H4 candle history forward gates (HERMES_CANDLE_HISTORY_FORWARD_*). Reported, NEVER acted on.
- gaps constants reused (TIMEFRAMES, MIN_REQUIRED_DEPTH, RETENTION_DAYS). No SQL, no vendor, no manifest write.

## Findings (all PROVEN by tests)
- status-only invariants: consumer_live/execution_enabled/backfill_executed/repair_executed = False; active_job/completed_pct = null
- no backfill/repair path: module CODE (docstrings+comments stripped) contains no execute_backfill/run_backfill/repair(
- no Redis delete / candle-history write: NO .set/.zadd/.delete/.expire/.zrem/.hset/.lpush/.rpush anywhere in module (status-only)
- analyze_backfill_status uses ONLY .get( (read-only)
- no SQL (pymysql/get_db_config/sqlalchemy/cursor) ; no vendor (requests/urllib/oanda/vendor) ; no market_map ; no Falcon ;
  no consumer_live=True
- XAUUSD denied (GOV-HERMES-BFS-002) ; missing/invalid gaps -> GAPS_SURFACE_MISSING (fail-closed) ; GAPS_FOUND never OK
- disabled publisher by default ; enabled-without-authorised -> SystemExit(101)
- D1 reports its governed backfill gates (dry-run default) but backfill_path executes nothing here ; M1-H4 -> NO_BACKFILL_EXECUTOR_CONFIGURED

## Tests
15/15 new. Curated governed regression (backfill-status + gaps + gaps-wiring + control-plane + catalog + D1 history/backfill) = 105 passed.

## Next gate
R2D2 cold audit of this PR. (No merge/deploy/activate/publish in this WO.)
