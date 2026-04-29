# WO-TRADING-SIGNALS-UTCNOW-CLEANUP-0001 — Evidence Pack

**Persona:** Helm
**Date:** 2026-04-29
**Branch:** `wo/WO-TRADING-SIGNALS-UTCNOW-CLEANUP-0001`
**Repo:** `tradingSignals` (`maff0000/hermes`)
**Parent WO:** WO-TRADING-SIGNALS-UTC-SWEEP-0001 (PR #5, merge `518ac529`)
**Baseline SHA:** `518ac529e1db48dfa5390423e869d58f346fb503`

## What changed

Files (Python runtime + data ingestion):
- `main.py`, `adapters/oanda.py`, `adapters/base.py`, `utils/redis_publisher.py`,
  `utils/break_detector.py`, `utils/level_engine.py`, `utils/compression_detector.py`,
  `utils/gap_scanner.py`, `utils/discord_alerts.py`, `utils/trading_hours.py`,
  `healthcheck/signal_health.py`, `services/market-map/market_map.py`,
  `scripts/backfill_signals_m15.py`, `scripts/backfill_oanda.py`,
  `scripts/backfill_signals.py`

Shell ops scripts:
- `ops/canonical_m1_parallel_writer.sh`, `ops/equivalence_check.sh`

Tests + harness:
- `tests/test_utc_compliance.py` extended (7 → 12 tests; banned `datetime.utcnow` repo-wide; protects 18 Python + 2 shell files)
- `ops/verify/Makefile` (NEW)
- `ops/verify/check_runtime.sh` (NEW)
- `ops/verify/check_canary.sh` (NEW)

Plus WO file + evidence pack.

## Submodule mark classification

Detailed in the WO file. **Verdict:** the `m` mark on `/srv-dev/tradingProteus/tradingSignals` is operator-state condition documented in WO-HERMES-LOCAL-MAIN-RECONCILIATION-0001 (REPORT delivered, awaiting architect call). NOT introduced by this WO. NOT a closure blocker for this WO. Classified as 4 components: 9 files Class A (auto-resolves on FF pull), 2 .jsonl Class B (gitignore candidate), 2 .py Class C (operator changes needing ratification).

## Substitution rule (architect-approved)

```
datetime.utcnow()                       -> datetime.now(timezone.utc)
time.time()                             -> datetime.now(timezone.utc).timestamp()
DATE_SUB(UTC_TIMESTAMP(), INTERVAL ...) -> shell-computed CUTOFF_UTC_* + parameterized SQL string
```

Numerically equivalent for UTC math; formally TZ-aware; satisfies architect's UTC rule.

## Discovery audit

Total banned-token sites across the repo before cleanup: 50.
After cleanup: 0 in protected files (regression test asserts).

| Site | Action |
|------|--------|
| 39 `datetime.utcnow()` across 14 Python files | replaced |
| 4 `time.time()` across 2 Python files | replaced |
| 2 `UTC_TIMESTAMP()` in 2 shell scripts | replaced via shell-side cutoff |
| 1 `NOW()` in `migrations/011_macro_instruments.sql` | OUT OF SCOPE — historical, applied |
| 2 `NOW()` in `ops/runbooks/hermes-recovery.md` | OUT OF SCOPE — runbook documentation |
| Multiple `NOW()` / `UTC_TIMESTAMP()` in `ops/evidence/*.md` | OUT OF SCOPE — historical evidence prose |

## Banned token scan output

```
$ python3 -m unittest tests.test_utc_compliance.TestNoBannedTimeCallsInPythonProtectedFiles -v
test_protected_python_files_are_clean ... ok

$ python3 -m unittest tests.test_utc_compliance.TestNoBannedTokensInShellOps -v
test_protected_shell_files_are_clean ... ok
```

## Tests

```
$ python3 -m unittest tests.test_utc_compliance -v
test_protected_python_files_are_clean ... ok
test_protected_shell_files_are_clean ... ok
test_all_python_protected_files_exist ... ok
test_all_shell_protected_files_exist ... ok
test_scan_catches_naive_utcnow ... ok
test_scan_catches_time_time ... ok
test_scan_does_not_false_positive_on_comment ... ok
test_scan_does_not_false_positive_on_docstring ... ok
test_iso_round_trip_preserves_utc ... ok
test_round_trip_naive_utcnow_to_aware_now ... ok
test_time_time_replacement_is_posix_seconds ... ok
test_utcnow_replacement_is_timezone_aware_utc ... ok

Ran 12 tests in 0.078s
OK
```

## Truth invariants (architect-required)

| Invariant | Test |
|-----------|------|
| `datetime.now(timezone.utc)` is timezone-aware UTC | `test_utcnow_replacement_is_timezone_aware_utc` |
| `datetime.now(timezone.utc).timestamp()` returns POSIX seconds (= old `time.time()`) | `test_time_time_replacement_is_posix_seconds` |
| Naive `datetime.utcnow` instant ≡ aware `datetime.now(timezone.utc)` instant after `replace(tzinfo=...)` | `test_round_trip_naive_utcnow_to_aware_now` |
| ISO round-trip of `datetime.now(timezone.utc).isoformat()` preserves UTC offset | `test_iso_round_trip_preserves_utc` |

## Canary (architect-required)

`TestScanLogicCanary` — synthetic-input checks that the banned-token scan
**actually catches** what it's supposed to AND doesn't false-positive on
comments or docstrings. Catches the failure mode where `_strip_comments_and_strings`
silently zaps real code or scan regex breaks.

## Tripwire (architect-required)

`TestProtectedFilesAllExist` — asserts every file listed in `PROTECTED_FILES`
and `PROTECTED_SHELL` exists on disk. Catches the failure mode where a file
gets renamed/removed without updating the scan list (silent shrink).

## CLOSED GREEN proof slots (filled post-merge)

- PR URL: _TO FILL_
- Merge commit SHA: _TO FILL_
- origin/main HEAD SHA: _TO FILL_
- dell-debian local main HEAD SHA: _TO FILL — same caveat as PR #5 (operator state pre-existing)_
- Deployed Python source matches canonical: _TO FILL — per-file SHA after targeted checkout, since local working tree carries operator state_
- signal-service-dev active post-restart: _TO FILL_
- hermes-canary GREEN post-restart: _TO FILL_
- `make verify-all` PASS: _TO FILL_

## Attestations

- **No strategy threshold tuning.** Cleanup is timestamp-correctness only; no strategy thresholds, scoring formulas, or decision logic touched.
- **No signal logic redesign.** `is_active` / `valid_until` / `freshness` / level retrieval / signal builder logic unchanged. Behaviour-equivalent under UTC.
- **No unrelated services changed.** Only files in `tradingSignals` repo. No tradingProteus / falcon / morpheus / NEO / TRADER changes.

## UTC time rule compliance

This WO **is** the rule fix for the remaining tradingSignals UTC violations. Comprehensive banned-token regression test extends to 18 Python + 2 shell files. Canary proves the scan catches; tripwire prevents silent list shrink; truth invariants prove substitution is UTC-equivalent.
