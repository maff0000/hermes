# WO-HELM-HERMES-PH2-GAPS-SURFACE-0001 — HELM verdict (CODE BUILD PR, dark/read-only)

## GREEN_PH2_GAPS_SURFACE_PR_READY
Tracking: HELM_HERMES_PH2_GAPS_SURFACE_PR::2026-07-12T14:00Z::GREEN_PH2_GAPS_SURFACE_PR_READY
Authority: R2D2_HERMES_PH2_RECOVERY_SURFACE_INVENTORY_DESIGN_AUDIT::2026-07-12T13:21:56Z::GREEN...APPROVED
Branch: wo/WO-HELM-HERMES-PH2-GAPS-SURFACE-0001   Base: a8b3d802c4495a239efa483873481ae9a5355dd9
Files changed (2, purely additive — NO PH1 file modified): utils/hermes_gaps_v1.py (NEW), tests/test_hermes_gaps_v1.py (NEW)

## Schema summary — hermes:gaps:XAU_USD:v1
Aggregate deterministic contract: top-level publisher/schema_version/instrument(XAU_USD)/generated_at_utc/source/
calendar_source(WEEKLY_WEEKEND_UTC)/consumer_live(false)/repair_executed(false)/backfill_executed(false)/
overall_gap_state/severity_order/timeframes{per-tf}/d1_boundary/caveats. Per-tf block carries timeframe/period_seconds/
history_key/latest_key/history_depth/min_required_depth/sufficient_depth/retention_policy/retention_floor_utc/oldest+newest_open/
expected_grid_policy/anchor/market_phase/expected_slots_open_market/present_slots/missing_slots/missing_open_epochs_sample(bounded 20)/
closed_market_missing_slots/out_of_retention_slots/invalid_anchor_count/latest_status/gap_state/status_reason.
D1 block: expected_anchor_utc=22:00/ny5pm_anchor/latest_open/history_newest_open/latest_matches_history_newest/sealed_complete/
source_count/expected_source_count=6/coverage/gap/invalid_anchor_count/non_22_anchor_count/forward_writer_enabled/forward_writer_authorised/
weekend_d1_buckets=NOT_EXPECTED/d1_boundary_state.

## Implementation summary
PURE data-injected core (classify_timeframe / classify_d1_boundary / build_gaps_contract / validate_gaps_contract) +
read-only analyze_gaps(client) (GET/ZRANGE/EXISTS only, NEVER SET/ZADD/DEL) + DARK build_gaps_publisher_from_env
(DisabledGapsPublisher default; enabled->GapsPublisher which STILL never writes the key in this WO). No config-in-code
that activates publication; dark gates HERMES_GAPS_PUBLISH_ENABLED/AUTHORISED.

## Findings
- Weekend/market-closed: market_phase() gold Sun 22:00Z->Fri 21:00Z; weekend missing slots -> MARKET_CLOSED never GAPS_FOUND (tested M1/H1 weekend + stale-during-closed -> MARKET_CLOSED).
- H1/H4 weekend candles: retained, anchor-valid (NOT flagged INVALID), annotated market_phase, and do NOT expand the open-market grid (_period_fully_open false) (tested).
- D1 boundary: 22:00 sealed 6/6 -> OK; 00:00 anchor -> INVALID_ANCHOR; latest!=history-newest -> LATEST_HISTORY_INCONSISTENT; XAUUSD -> not sealed + validate raises (tested).
- Retention: pre-floor missing -> OUT_OF_RETENTION not gap (tested); 35d/120d floors.
- Severity worst-of: SOURCE_MISSING>INVALID_ANCHOR>INSUFFICIENT_HISTORY>GAPS_FOUND>STALE>MARKET_CLOSED>OUT_OF_RETENTION>OK (tested).

## Boundary proofs
No runtime mutation (code-only PR). No hermes:gaps:* publish in this WO (analyze/GapsPublisher never SET). No Redis
writes/deletes (analyze_gaps uses only GET/ZRANGE/EXISTS; test fake asserts writes==[] deletes==[]). No SQL (no pymysql/
get_db_config). No backfill/repair (repair_executed=backfill_executed=false, no execute path). No market_map (no import/use;
only negative declaration in docstring). No Falcon/consumer-live (consumer_live hard-false; no falcon import). No XAUUSD
(validate raises; D1 XAUUSD -> not sealed). No strategy/risk/signal/trade semantics. PH1 D1 surfaces untouched.

## Tests
+18 focused (all 18 WO scenarios: weekend-MARKET_CLOSED, open-market GAPS_FOUND, SOURCE_MISSING, INVALID_ANCHOR,
INSUFFICIENT_HISTORY, STALE-open, STALE-closed->MARKET_CLOSED, OUT_OF_RETENTION, weekend-candle-retained, D1 22:00 OK,
D1 00:00 INVALID, D1 latest!=history, XAUUSD denied, worst-of severity, repair/backfill false, read-only no-writes, dark-by-default, no SQL/market_map/falcon/interpretive).
Touched-area 55 passed. FULL SUITE: branch == pristine main a8b3d80 -> 0 NEW failures; +18 passing (1175 vs 1157).
