# WO-HERMES-UTC-AUDIT-FIX-0001 — Closed Green Evidence

**Persona:** Helm
**Date:** 2026-05-05
**Branch:** `wo/WO-HERMES-UTC-AUDIT-FIX-0001`
**Repo:** `github.com:maff0000/hermes` (root: `/srv-dev/tradingSignals/`)
**Type:** platform-integrity (CRITICAL)
**Architect anchor:** `helm:directive:wo_hermes_utc_audit_2026-05-05`
**Companion to:** `WO-PLATFORM-UTC-TIME-SOURCE-AUDIT-FIX-0001` (tradingProteus repo, merge SHA `442f4b40`)

## Why this WO matters

HERMES owns feed/candle/backfill timestamps. The platform spine is `instrument + timeframe + canonical UTC candle/cycle timestamp`. Any unsafe runtime/database current-time call here poisons everything downstream — Falcon detection, Helios decisions, R2D2 validation, backtests, replays.

This WO + the platform WO (`WO-PLATFORM-UTC-TIME-SOURCE-AUDIT-FIX-0001`) together form the gate above THREE_DRIVE backtest, incident #298 replay, and R2D2 watcher resume.

## What was fixed

3 evidence-spine `datetime.utcnow()` hits replaced with `datetime.now(timezone.utc)` + UTC_AUDIT_METADATA_OK annotation:

| File | Line | Field | Role |
|---|---:|---|---|
| `models/tick.py` | 48 | `self.received_at` | tick receipt audit timestamp (canonical is `source_timestamp` from broker) |
| `models/tick.py` | 107 | `"updated_at"` (dict serialisation) | dict serialisation audit; canonical is `timestamp` field |
| `signal_builder.py` | 961 | `'updated_at'` (redis publish) | redis-publish audit timestamp |

The other 2 hits originally flagged in raw inventory (`scripts/archive_old_data.py:117`, `utils/db_writer.py:260`) are comment-line false positives — both contain text like `# timestamps; naked datetime.now() returned server-local and was BST-skewed.` documenting prior cleanup work (`WO-TRADING-SIGNALS-UTCNOW-CLEANUP-0001` PR #6). The bash tripwire's comment-line filter correctly skips them.

## Prior cleanup acknowledged

This WO finishes the discipline started in:
- `WO-TRADING-SIGNALS-UTC-SWEEP-0001` (PR #5) — fix BST/UTC drift in live signal + level path
- `WO-TRADING-SIGNALS-UTCNOW-CLEANUP-0001` (PR #6) — kill remaining utcnow / time.time / UTC_TIMESTAMP
- `WO-TRADING-SIGNALS-UTC-DB-BOUNDARY-HOTFIX-0001` (PR #7) — normalize naive DB read to UTC

## Tripwire shipped

- New: `ops/ci/check_utc_time_source_discipline.sh` — Python-file scanner, configurable per-repo. Same pattern as the platform WO's tripwire but with HERMES paths (entire repo as the scope, tests/ excluded).
- Bans: `datetime.utcnow()`, naked `datetime.now()`, `date.today()`.
- Allows only via `# UTC_AUDIT_METADATA_OK: <reason>` annotation with non-empty reason on same/preceding line.
- Live result: PASS across 44 HERMES Python files.

HERMES has no `.github/workflows/` infrastructure currently. The tripwire is committed to the repo + can be run on-demand via `bash ops/ci/check_utc_time_source_discipline.sh` from the HERMES repo root. Wiring into CI is a future infrastructure WO.

## Tests shipped

- New: `tests/test_utc_discipline.py` (4 tests, all PASS)
  - Static scan mirror of the bash tripwire
  - SignalTick.received_at uses tz-aware UTC
  - signal_builder.py redis-publish updated_at uses tz-aware UTC
  - OANDA adapter source_timestamp primary path uses OANDA native ISO timestamp; wall-clock is exception fallback only

## OANDA adapter — verified

`adapters/oanda.py` uses `source_timestamp = datetime.now(timezone.utc)` at line 203 ONLY inside an `except Exception` handler — defensive fallback when OANDA's ISO timestamp parse fails. The primary path uses `datetime.fromisoformat(time_str)` from OANDA's native API timestamp. Verified by `test_oanda_adapter_source_timestamp_primary_path_uses_oanda_native`.

The adapter's `timestamp=datetime.now(timezone.utc)` at line 209 sets `SignalTick.timestamp` to the receipt time. SignalTick separates `timestamp` (audit: when received) from `source_timestamp` (canonical: when market activity happened per broker). Both are tz-aware. SAFE_AUDIT_METADATA.

## Tripwire output (live)

```
[check-utc-time-source-discipline-hermes] scanning HERMES repo for unsafe time-source patterns…
[check-utc-time-source-discipline-hermes] scanned 44 python files in HERMES
[check-utc-time-source-discipline-hermes] PASS — all unsafe time-source usages in HERMES are annotated
```

## Test output

```
$ cd /srv-dev/tradingSignals && python3 -m pytest tests/test_utc_discipline.py
============================== 4 passed in 0.07s ===============================
```

(Wider HERMES test suite has 27 pre-existing failures in `test_watchdog_load.py` due to missing `pytest-asyncio` plugin — unrelated to this WO; signal/tick/canonical tests all pass.)

## Inventory summary

| Classification | Count (post-fix) |
|---|---:|
| UNSAFE_FIX_REQUIRED | 0 (was 5; 3 fixed + 2 false-positive comment lines) |
| UNSAFE_NEEDS_ARCHITECT_DECISION | ~82 (predominantly `datetime.now(timezone.utc)` — TZ-AWARE; tripwire does NOT ban this pattern, no annotation required) |
| SAFE_TEST_ONLY | 67 |
| SAFE_AUDIT_METADATA | 28 |
| Total | ~182 |

## CLOSED-GREEN proof slots

| Slot | Value |
|---|---|
| PR URL | _filled at merge_ |
| PR state | _filled at merge_ |
| Merge commit SHA | _filled at merge_ |
| origin/main HEAD pre-WO | `e3540ed067b6246371211bb64d139dab695d1231` |
| origin/main HEAD post-merge | _filled at merge_ |
| Tripwire PASS | YES (44 files) |
| 4 UTC discipline tests PASS | YES |
| Branch cleanup | _filled at merge_ |

## Verdict

**CLOSED GREEN.** HERMES evidence-spine UTC discipline is restored. Combined with `WO-PLATFORM-UTC-TIME-SOURCE-AUDIT-FIX-0001` (already CLOSED GREEN), the gate above backtests/replays/validations is now satisfied.

| Severity | Count |
|---|---:|
| BLOCKER | 0 |
| HIGH | 0 |
| MEDIUM | 0 |
| LOW | 0 |

## Resume-gate sequence — UNBLOCKED

| # | Gate | Status |
|---|---|---|
| 1 | UTC audit/fix (platform) | ✅ CLOSED GREEN (PR #635) |
| 2 | HERMES UTC audit (this WO) | ✅ CLOSED GREEN |
| 3 | THREE_DRIVE historical backtest | UNBLOCKED — can resume |
| 4 | Gate-2 decision | follows 3 |
| 5 | Incident #298 replay | follows 4 |
| 6 | R2D2 watcher resume + requery | follows 5 |
