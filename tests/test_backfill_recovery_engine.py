"""Guardrail tests for the automated backfill-recovery engine.
WO-HERMES-AUTOMATED-BACKFILL-RECOVERY.

Pure/injected — no DB, no OANDA, no network. Verifies the three immutable guardrails:
  1. idempotency/re-entry — only the missing interval is passed to backfill (since=last, until=now)
  2. time-bounded — Δt > window or no baseline -> AMBER_STOP (RC=20), backfill NOT invoked
  3. decoupled webhook — alert failures never alter the recovery decision; missing webhook != corruption
"""
import importlib.util
import os
from datetime import datetime, timedelta, timezone

_SPEC = importlib.util.spec_from_file_location(
    "backfill_recovery_engine",
    os.path.join(os.path.dirname(__file__), "..", "tools", "backfill_recovery_engine.py"))
eng = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(eng)

_NOW = datetime(2026, 6, 23, 12, 0, 0, tzinfo=timezone.utc)


def _utc(**delta):
    return _NOW - timedelta(**delta)


# --------------------------------------------------------------- pure decision ---------------------
def test_decision_fresh_when_no_gap():
    state, code, _ = eng.recovery_decision(_NOW, _NOW, window_hours=24)
    assert state == eng.FRESH and code is None


def test_decision_proceed_within_window():
    state, code, delta = eng.recovery_decision(_NOW, _utc(hours=6), window_hours=24)
    assert state == eng.PROCEED and code is None and delta == timedelta(hours=6)


def test_decision_amber_when_window_exceeded():
    state, code, _ = eng.recovery_decision(_NOW, _utc(hours=25), window_hours=24)
    assert state == eng.AMBER_STOP and code == eng.GOV_WINDOW_EXCEEDED


def test_decision_amber_when_no_baseline():
    state, code, _ = eng.recovery_decision(_NOW, None, window_hours=24)
    assert state == eng.AMBER_STOP and code == eng.GOV_NO_BASELINE


def test_decision_rejects_naive_datetimes():
    for args in ((datetime(2026, 6, 23, 12), _NOW), (_NOW, datetime(2026, 6, 23, 11))):
        try:
            eng.recovery_decision(*args, window_hours=24)
            assert False
        except ValueError as e:
            assert eng.GOV_EXEC in str(e)


# --------------------------------------------------------------- orchestration (exit codes) --------
def _run(**over):
    calls = {"backfill": [], "alerts": []}
    base = dict(
        enabled=True,
        now_fn=lambda: _NOW,
        last_ts_fn=lambda: _utc(hours=3),
        backfill_fn=lambda *, since, until: calls["backfill"].append((since, until)),
        alert_fn=lambda code, summary: calls["alerts"].append(code),
        window_hours=24,
    )
    base.update(over)
    rc = eng.run(**base)
    return rc, calls


def test_disabled_is_noop_rc0_no_backfill():
    rc, calls = _run(enabled=False)
    assert rc == 0 and calls["backfill"] == [] and calls["alerts"] == []  # fail-silent normal ops


def test_fresh_is_rc0_no_backfill():
    rc, calls = _run(last_ts_fn=lambda: _NOW)
    assert rc == 0 and calls["backfill"] == []


def test_proceed_backfills_only_missing_interval_rc0():
    rc, calls = _run()
    assert rc == 0
    assert calls["backfill"] == [(_utc(hours=3), _NOW)]      # exactly [last, now] — only the gap
    assert eng.GOV_RECOVER in calls["alerts"]


def test_amber_window_exceeded_rc20_no_backfill():
    rc, calls = _run(last_ts_fn=lambda: _utc(hours=48))
    assert rc == 20 and calls["backfill"] == []             # cascade prevented
    assert eng.GOV_WINDOW_EXCEEDED in calls["alerts"]


def test_amber_no_baseline_rc20_no_backfill():
    rc, calls = _run(last_ts_fn=lambda: None)
    assert rc == 20 and calls["backfill"] == []
    assert eng.GOV_NO_BASELINE in calls["alerts"]


def test_backfill_failure_is_fail_loud_rc1():
    def boom(*, since, until):
        raise RuntimeError("OANDA 503")
    rc, calls = _run(backfill_fn=boom)
    assert rc == 1 and eng.GOV_EXEC in calls["alerts"]


def test_missing_webhook_does_not_block_or_alter_recovery():
    # Decoupled webhook (R2D2's concern): a muted alert (missing/invalid webhook -> dispatch returns
    # False, never raises) must NOT change the PROCEED decision or the bounded interval. A missing
    # webhook can never cause a silent fall-through to unbounded/canonical-corrupting ingest.
    rc, calls = _run(alert_fn=lambda c, s: False, last_ts_fn=lambda: _utc(hours=2))
    assert rc == 0 and calls["backfill"] == [(_utc(hours=2), _NOW)]

    # And under AMBER, a muted alert still yields RC=20 with NO backfill (cascade still prevented).
    rc2, calls2 = _run(alert_fn=lambda c, s: False, last_ts_fn=lambda: _utc(hours=72))
    assert rc2 == 20 and calls2["backfill"] == []
