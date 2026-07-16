# Market-Hours-Aware Stream Health & Recovery Suppression — Design

WO-HELM-HERMES-MARKET-HOURS-AWARE-STREAM-HEALTH-AND-RECOVERY-SUPPRESSION-0001 · base `82701a8` · dev-only (PR; not deployed)

## Current-path diagnosis (Task 1)
- Stream-level stale detection: `utils/watchdog.py::HermesWatchdog._evaluate` gates STALE_TICK/STALE_CANDLE on
  `self._is_market_open(now)` — the checker wired in `main.py` is `utils/trading_hours.py::is_market_open`.
- **F-1 root cause:** `trading_hours.is_market_open` uses HARDCODED FIXED-UTC hours (Fri/Sun 22:00 UTC), has **NO daily
  rollover break**, and is **DST-blind**. The OANDA daily break is 17:00 New York time = 21:00 UTC (EDT) / 22:00 UTC (EST).
  On 2026-07-15 (EDT) the break at ~21:00 UTC was read as OPEN -> STALE_CANDLE fired (Incident #1104) despite market-hours
  logic being "present".
- Per-instrument path: `_evaluate_instruments` uses `utils/market_hours_policy.py::MarketHoursPolicy.is_truth_expected`
  (SQL `hermes_market_hours`, fixed-UTC columns — also DST-blind). Closed instruments already become AMBER (excluded from
  the RED count / per-instrument recovery), so **F-2** is a market-hours-correctness problem, not a counting problem: once
  the schedule is DST-correct, closed instruments never drive full-stream reconnect.
- Full-stream reconnect: `_evaluate_per_instrument_recovery` raises a reconnect request from sustained RED instruments.
  Closed (AMBER) instruments do not count once market-hours is correct.

## Design decision
- New PURE, DST-aware decision core `utils/hermes_market_hours_health_v1.py` (stdlib `zoneinfo`; no heavy dependency).
  Governed schedule `config/market_hours_schedule.v1.json` (NY-tz, weekly session + daily rollover break + reopening grace;
  versioned; container-ready; no secrets; no /etc; no live SQL mutation). Holidays fail closed (treated as open).
- 5-state instrument model (`MARKET_OPEN_FLOWING`, `MARKET_OPEN_STALE`, `MARKET_CLOSED_EXPECTED`, `MARKET_REOPENING_GRACE`,
  `MARKET_OPEN_MISSING_AFTER_GRACE`) + reason codes; decides incident / per-instrument-recovery / full-stream eligibility.
- **Fail closed = behave as OPEN** (conservative) on unknown/malformed/ambiguous schedule -> normal detection/recovery is
  NEVER suppressed. Genuine connection/session faults (`connection_fault` / `shared_stream_fault`) are NEVER suppressed.
- Integration seam (surgical, opt-in, zero-regression): a `DstAwareMarketHours` adapter exposes the existing
  `is_market_open(ts)->(bool,reason)` and `is_truth_expected(instrument,now)->(bool,reason)` interfaces. `main.py` builds it
  from the governed config and injects it as `market_hours_checker` (stream-level) + `market_truth_checker` (per-instrument),
  falling back to the legacy checkers on any load error. `watchdog.__init__` gains `market_truth_checker=None`
  (default = legacy behaviour); `_evaluate_instruments` prefers it when injected. No decision-path rewrite.

## Behaviour (proven by tests + Incident #1104 replay)
- Expected close (daily break / weekend): `MARKET_CLOSED_EXPECTED`, reason `EXPECTED_MARKET_CLOSED_NO_FLOW`, incident=NO,
  per-instrument recovery=NO, full-stream contribution=NO. Last-data timestamps stay truthful; no key refresh; no fabrication.
- Reopen within grace: `MARKET_REOPENING_GRACE`, incident=NO. Past grace still missing: `MARKET_OPEN_MISSING_AFTER_GRACE`,
  incident=YES, recovery=YES. Open+stale: eligible. Connection fault during close: NOT suppressed.
- Full-stream reconnect: eligible ONLY on `shared_stream_fault` OR >= quorum(2) expected-OPEN instruments stale; a single
  open-stale instrument -> per-instrument recovery, not full-stream; closed instruments never contribute.
- Gap contract (§9, non-mutating): `classify_absence_for_gap` -> EXPECTED_CLOSED_INTERVAL / RECOVERABLE_GAP /
  UNKNOWN_UNCLASSIFIED_ABSENCE. No interval hidden/deleted; no backfill; v1 contract unchanged.
- REST quote is NOT an input to the classifier -> a fresh REST quote can never mask a stale stream (§11).
- DST proven: July break 21:00-22:00 UTC (EDT), January break 22:00-23:00 UTC (EST); weekly Sun-open/Fri-close shift with DST.

## Not changed / out of scope
No new key family/schema/publisher; no v1 contract change; no recovery-planner change; no backfill/repair; no `consumer_live`
change; no runtime deploy. Holiday engine deferred (fail-closed placeholder).
