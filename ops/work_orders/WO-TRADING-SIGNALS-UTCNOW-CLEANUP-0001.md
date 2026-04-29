# WO-TRADING-SIGNALS-UTCNOW-CLEANUP-0001 — tradingSignals UTC cleanup + verify harness

**Persona:** Helm
**Date:** 2026-04-29
**Branch:** `wo/WO-TRADING-SIGNALS-UTCNOW-CLEANUP-0001`
**Repo:** `tradingSignals` (`maff0000/hermes`)
**Parent:** WO-TRADING-SIGNALS-UTC-SWEEP-0001 (PR #5, merge `518ac529`)

## Submodule mark classification (architect-required)

The `m` mark on `/srv-dev/tradingProteus/tradingSignals` reflects the dell-debian
local working tree of the tradingSignals submodule. Classified:

| Component | State | Class |
|-----------|-------|-------|
| Local main HEAD `dabff828` is behind origin/main `518ac529` | local hasn't fast-forwarded since PR #5 merged | A — auto-resolves on FF pull |
| `utils/healthcheck.py` (+11/-8) | IRIS dispatch stubbed; live operator change | C — needs ratification (covered in WO-HERMES-LOCAL-MAIN-RECONCILIATION-0001 report) |
| `utils/watchdog.py` (+13/0) | HERMES-RESILIENCE-001 auto-recovery; live operator change | C — needs ratification |
| Two `.jsonl` evidence logs (+7,400 lines) | append-only operational log accretion | B — gitignore candidate |
| 8 WO #5 sweep files post-checkout | match origin/main, just unstaged | A — auto-resolves on FF pull |

**Verdict:** the submodule mark is **operator-state condition documented in a paused-pending-ratification WO** (WO-HERMES-LOCAL-MAIN-RECONCILIATION-0001), NOT an unrelated coincidence and NOT introduced by this WO. This WO uses a `git worktree` cut from clean `origin/main` to avoid disturbing operator state, exactly like PR #5 did.

## Motivation

WO-TRADING-SIGNALS-UTC-SWEEP-0001 (PR #5) cleaned the R2D2 priority bundle (B1/B2/B3/H6/L2). Multiple `datetime.utcnow()` and `time.time()` sites remained in protected runtime + data-ingestion files outside that bundle. Architect's new closure-compliance bar requires automated banned-token scans + canary + tripwire + truth-invariant proof.

## Discovery — classification of every banned-token site

Audited the whole tradingSignals tree for `datetime.utcnow`, `datetime.now()`, `time.time()`, SQL `NOW()` / `UTC_TIMESTAMP()` / `CURDATE()`, pandas `Timestamp.now`, and shell `date`.

| Class | Count | Action |
|-------|-------|--------|
| Unsafe runtime usage (Python) | 39 utcnow + 4 time.time across 14 files | **Fix** — replace with `datetime.now(timezone.utc)` / `.timestamp()` |
| Unsafe DB-runtime SQL via shell | 2 `UTC_TIMESTAMP()` in `ops/canonical_m1_parallel_writer.sh` + `ops/equivalence_check.sh` | **Fix** — shell-side `CUTOFF_UTC_*` via `date -u --date=...` interpolation |
| Out-of-scope: tests | `tests/test_utc_compliance.py` | **Allowed test-only** (and itself the scan) |
| Out-of-scope: historical migrations | `migrations/011_macro_instruments.sql` `NOW()` in INSERT | **Allowed** — one-shot historical migration, already applied |
| Out-of-scope: documentation | `ops/runbooks/hermes-recovery.md`, `ops/evidence/*.md` | **Allowed** — docs, not runtime |

## Files cleaned

```
main.py                                 utcnow=6 time.time=2
adapters/oanda.py                       utcnow=3
adapters/base.py                        utcnow=2
utils/redis_publisher.py                utcnow=7
utils/break_detector.py                 utcnow=2
utils/level_engine.py                   utcnow=5
utils/compression_detector.py           utcnow=2
utils/gap_scanner.py                    utcnow=1
utils/discord_alerts.py                 utcnow=2
utils/trading_hours.py                  utcnow=7
healthcheck/signal_health.py            utcnow=5
services/market-map/market_map.py       utcnow=1 time.time=2
scripts/backfill_signals_m15.py         utcnow=1
scripts/backfill_oanda.py               utcnow=1
scripts/backfill_signals.py             utcnow=1
ops/canonical_m1_parallel_writer.sh     UTC_TIMESTAMP=1 (-> CUTOFF_UTC_5M)
ops/equivalence_check.sh                UTC_TIMESTAMP=1 (-> CUTOFF_UTC_1H)

Total: 46 utcnow + 4 time.time + 2 UTC_TIMESTAMP — all fixed.
```

## Substitution rules

| Old | New | Rationale |
|-----|-----|-----------|
| `datetime.utcnow()` | `datetime.now(timezone.utc)` | architect-approved TZ-aware UTC; deprecated in Python 3.12+ replaced |
| `time.time()` | `datetime.now(timezone.utc).timestamp()` | numerically identical (POSIX seconds since UTC epoch); formally TZ-aware |
| `DATE_SUB(UTC_TIMESTAMP(), INTERVAL N MINUTE/HOUR)` | shell-computed `CUTOFF_UTC_*` via `date -u --date=...` interpolated as parameterized SQL string | architect rule forbids both NOW() AND UTC_TIMESTAMP() |
| `from datetime import datetime, ...` | adds `, timezone` to existing line; idempotent | required for tz argument |

## Tests

Extended `tests/test_utc_compliance.py` from 7 to 12 tests, plus repo-wide ban on `datetime.utcnow()`:

| Class | Count | Coverage |
|-------|-------|----------|
| `TestNoBannedTimeCallsInPythonProtectedFiles` | 1 | scans 18 protected Python files for 5 banned tokens |
| `TestNoBannedTokensInShellOps` | 1 | scans 2 protected shell scripts for `UTC_TIMESTAMP`/`NOW` |
| `TestScanLogicCanary` | 4 | proves the scan catches utcnow + time.time AND doesn't false-positive on comments / docstrings |
| `TestUTCSubstitutionTruthInvariant` | 4 | proves substitution is timezone-aware UTC, POSIX-seconds equivalent, naive→aware roundtrip preserves instant, ISO roundtrip preserves UTC |
| `TestProtectedFilesAllExist` | 2 | tripwire that catches a renamed/removed protected file from silently shrinking the scan |

```
$ python3 -m unittest tests.test_utc_compliance -v
Ran 12 tests in 0.078s
OK
```

## Verify harness

New `ops/verify/` directory mirroring the helios_v3 closure pattern:

| Target | Verifies |
|--------|----------|
| `make verify-all` | run every check |
| `test-utc-compliance` | 12-test compliance suite (banned tokens + canary + tripwire + truth invariant) |
| `verify-trading-signals-utc-clean` | alias of test-utc-compliance |
| `verify-trading-signals-runtime` | signal-service-dev active + recent journal lines |
| `verify-trading-signals-canary` | hermes-canary GREEN |

Files:
- `ops/verify/Makefile`
- `ops/verify/check_runtime.sh`
- `ops/verify/check_canary.sh`

## UTC time rule compliance

This WO **is** the rule fix for the remaining tradingSignals runtime UTC violations. All replacements use `datetime.now(timezone.utc)` (architect-approved) for Python and shell-computed `date -u --date=...` (timezone-aware UTC) for SQL ops scripts. Regression test asserts no banned tokens remain across 18 Python + 2 shell protected files.

## Out of scope (architect-explicit)

- No strategy logic / threshold tuning / signal redesign / unrelated services.
- No new strategy logic.
- Only timestamp correctness; behaviour-equivalent for UTC math.
- WO-HERMES-LOCAL-MAIN-RECONCILIATION-0001 still paused (separate ratification needed).
- `migrations/011_macro_instruments.sql` historical migration left untouched.

## Acceptance criteria

- AC-1: 12/12 tests pass.
- AC-2: `make -f ops/verify/Makefile verify-all` PASS post-deploy.
- AC-3: signal-service-dev active post-restart with new code; no banned tokens in protected files.
- AC-4: hermes-canary GREEN post-restart.
- AC-5: deployed source matches canonical post-merge.
- AC-6: submodule mark explicitly classified (this section).

## Evidence

See `ops/evidence/WO-TRADING-SIGNALS-UTCNOW-CLEANUP-0001/closed_green.md`.
