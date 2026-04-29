# WO-TRADING-SIGNALS-UTC-SWEEP-0001 — Evidence Pack

**Persona:** Helm
**Date:** 2026-04-29
**Branch:** `wo/WO-TRADING-SIGNALS-UTC-SWEEP-0001`
**Repo:** `tradingSignals` (`maff0000/hermes`)

## Architect ratification

R2D2 audit (`r2d2:audit:code_sweep:2026-04-29`) graded UTC/BST drift as
**BLOCKER class** — "platform truth defect" affecting the live signal/level
path that Falcon and Helios consume. Architect addendum 2026-04-29:

> If WO-HELIOS-V2-LEGACY-RETIRE-0001 is already in-flight and remains narrow,
> finish it. Immediately after V2 retirement, run
> WO-TRADING-SIGNALS-UTC-SWEEP-0001.
> R2D2 states the first priority WO bundles B1/B2/B3/H6/L2 into one small PR
> with ~6 single-line changes and can ship without disturbing the
> migration/sunset queue.

V2 retirement (PR #569) closed green at 12:24Z. This WO follows immediately.

## R2D2 priority bundle

| ID | Defect | File / line | Live impact |
|----|--------|-------------|-------------|
| B1 | SQL `NOW()` in healthcheck against UTC-stored `signals.timestamp` | `healthcheck/signal_health.py:236` | ATR-baseline health window 1h-skewed in BST |
| B2 | SQL `NOW()` against UTC-stored `hermes_levels.valid_until` | `utils/break_detector.py:350` | Hermes levels expire 1h early in BST |
| B3 | Same pattern as B2 in level retrieval | `utils/level_engine.py:471` | Falcon strategies + Helios consume hermes levels via this path; same 1h drift |
| H6 | Naked `datetime.now()` cutoffs vs UTC-stored timestamps | `utils/db_writer.py:123,142,259` and `scripts/archive_old_data.py:116` | Archive cutoffs BST-skewed |
| L2 | `datetime.now()` cache TTL | `utils/atr_calculator.py:255,299` | Internal-relative; works today but rule violation |

## Fix pattern

The architect's UTC rule explicitly forbids both `NOW()` AND `UTC_TIMESTAMP()`.
The fix is **not** `NOW() → UTC_TIMESTAMP()`; it is to compute a timezone-aware
UTC cutoff Python-side and pass it as a parameterized query argument. Same
pattern Helm applied for the V3 daemon (folded into
WO-FALCON-HEALTH-METADATA-PATCH-0001 evidence pack).

| Old | New |
|-----|-----|
| `WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 1 HOUR)` | `cutoff_utc = datetime.now(timezone.utc) - timedelta(hours=1)` then `WHERE timestamp >= %s, ..., (instrument, cutoff_utc)` |
| `WHERE valid_until > NOW()` | `now_utc = datetime.now(timezone.utc)` then `WHERE valid_until > %s, ..., (instrument, now_utc)` |
| `cutoff = datetime.now() - timedelta(...)` | `cutoff = datetime.now(timezone.utc) - timedelta(...)` |
| `(datetime.now() - cached_time)` | `(datetime.now(timezone.utc) - cached_time)` |

## Files changed

| File | Edit summary |
|------|--------------|
| `healthcheck/signal_health.py` | + `timezone` import; B1 fix (parameterized UTC cutoff replaces `DATE_SUB(NOW(), INTERVAL 1 HOUR)`) |
| `utils/break_detector.py` | + `timezone` import; B2 fix (parameterized `now_utc` replaces `NOW()`) |
| `utils/level_engine.py` | + `timezone` import; B3 fix (same as B2) |
| `utils/db_writer.py` | + `timezone` import; three `datetime.now()` → `datetime.now(timezone.utc)` (last_flush, batch flush trigger, archive cutoff) |
| `scripts/archive_old_data.py` | + `timezone` import; cutoff `datetime.now()` → `datetime.now(timezone.utc)` |
| `utils/atr_calculator.py` | + `timezone` import; two L2 cache-TTL `datetime.now()` → `datetime.now(timezone.utc)` |
| `tests/test_utc_compliance.py` | NEW — 7-test regression suite asserting no banned tokens + canonical UTC patterns |

## Tests

```
$ cd <repo> && python3 -m unittest tests.test_utc_compliance -v
test_archive_old_data_cutoff (TestArchiveCutoffIsUTC) ... ok
test_db_writer_archive_cutoff (TestArchiveCutoffIsUTC) ... ok
test_cache_uses_utc_aware_now (TestAtrCalculatorCacheIsUTC) ... ok
test_break_detector (TestHermesLevelsCutoffIsUTC) ... ok
test_level_engine (TestHermesLevelsCutoffIsUTC) ... ok
test_protected_files_are_clean (TestNoBannedTimeCallsInProtectedFiles) ... ok
test_atr_baseline_uses_utc_cutoff (TestSignalHealthCutoffIsUTC) ... ok

Ran 7 tests in 0.025s
OK
```

## Architect-required check results

| Required proof | Status |
|----------------|--------|
| PR URL | _TO FILL POST-PR-OPEN_ |
| PR state | _TO FILL POST-MERGE_ |
| Merge commit SHA | _TO FILL POST-MERGE_ |
| origin/main HEAD SHA | _TO FILL POST-MERGE_ |
| dell-debian local main HEAD SHA | _TO FILL POST-MERGE_ (note: local has pre-existing uncommitted changes from operator session, untouched by this WO; the WO branch was cut from `origin/main` via `git worktree` for clean isolation) |
| Deployed source matches canonical main | _TO FILL POST-MERGE — sha256 of post-merge `<protected>` files vs `origin/main`_ |
| Tests pass | YES — 7/7 |
| Evidence pack | this file |

## UTC time rule compliance

This WO is the UTC time rule fix. Each protected file post-edit:
- Uses `datetime.now(timezone.utc)` (timezone-aware UTC) — explicitly approved form, distinct from forbidden naked `datetime.now()`.
- Eliminates SQL `NOW()` from queries against UTC-stored columns by passing parameterized cutoffs.
- Includes regression test asserting no `NOW()`, `UTC_TIMESTAMP()`, `datetime.now()`, or `time.time()` outside comments / strings in any of the protected files.

## Out of scope (deferred to follow-up)

Pre-existing `datetime.utcnow()` calls remain in the protected files outside the R2D2 priority bundle:

| File | Lines | Note |
|------|-------|------|
| `healthcheck/signal_health.py` | 175, 198, 218, 286, 368 | non-blocker per R2D2 |
| `utils/break_detector.py` | 124, 142 | non-blocker per R2D2 |
| `utils/level_engine.py` | 105, 120, 189, 325, 546 | non-blocker per R2D2 |

`datetime.utcnow()` returns a naive datetime in Python and should ultimately be `datetime.now(timezone.utc)`. R2D2 did not bundle these into the priority sweep; the architect addendum says "narrow" and "do not bundle". Proposed follow-up WO ID: `WO-TRADING-SIGNALS-UTCNOW-CLEANUP-0001` (queue under remaining-R2D2-defects triage).

## Out of scope (architect-stated)

- No Falcon strategy logic changes.
- No Helios decision logic changes.
- No R2D2 scoring changes.
- No Solo / Agent Smith execution changes.
- No threshold changes.
- `tradingScalp.helios_decisions` retired-table reads (B4) — flagged by R2D2 but in `/srv/NEO/scripts/neo_auditor.py`, separate WO.

## CLOSED GREEN

_Filled post-merge._
