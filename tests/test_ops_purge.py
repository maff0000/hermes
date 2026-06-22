"""Tests for the production-only purge engine (HERMES-OPS-PURGE-001).

No live DB, no live Redis, no live SIEM. All effects are injected, so the gate logic, fail-loud
behaviour, config validation, UTC cutoff, and chunked-delete mechanics are exercised in isolation.
"""
import importlib.util
import os
from datetime import datetime, timezone

# Load ops/purge.py directly (ops is not a package on the path).
_SPEC = importlib.util.spec_from_file_location(
    "ops_purge", os.path.join(os.path.dirname(__file__), "..", "ops", "purge.py"))
purge = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(purge)


# ----------------------------------------------------------------- gate decision -------------------
def test_gate_bypass_when_run_env_unset():
    action, msg = purge.decide_gate({})
    assert action == purge.BYPASS and msg.startswith("[PURGE_BYPASS]")


def test_gate_bypass_for_dev_and_staging():
    for env in ("DEV", "development", "STAGING", "test", "prod-ish"):
        action, msg = purge.decide_gate({"RUN_ENV": env})
        assert action == purge.BYPASS, env
        assert "[PURGE_BYPASS]" in msg


def test_gate_blocked_in_prod_without_toggle():
    action, msg = purge.decide_gate({"RUN_ENV": "PRODUCTION"})
    assert action == purge.BLOCKED
    assert purge.GOV_PURGE_DISABLED in msg  # GOV-PURGE-001


def test_gate_blocked_in_prod_with_wrong_toggle():
    for v in ("true", "1", "YES", "false", ""):
        action, msg = purge.decide_gate({"RUN_ENV": "PRODUCTION", "HERMES_PURGE_ENABLED": v})
        assert action == purge.BLOCKED, v


def test_gate_proceed_only_with_exact_true():
    # exact "TRUE" (whitespace-stripped) proceeds; case variants must fail closed (BLOCKED).
    for v in ("TRUE", " TRUE "):
        action, _ = purge.decide_gate({"RUN_ENV": "production", "HERMES_PURGE_ENABLED": v})
        assert action == purge.PROCEED, v
    for v in ("true", "True", "tRuE"):
        action, _ = purge.decide_gate({"RUN_ENV": "PRODUCTION", "HERMES_PURGE_ENABLED": v})
        assert action == purge.BLOCKED, v


# ----------------------------------------------------------------- config / injection --------------
def test_parse_table_specs_valid():
    assert purge.parse_table_specs("ticks:received_at, candles_M1:timestamp") == [
        ("ticks", "received_at"), ("candles_M1", "timestamp")]


def test_parse_table_specs_rejects_injection():
    for bad in ("ticks", "ticks;DROP TABLE x:ts", "ticks:ts;--", "t t:ts", "ticks:t s", ""):
        try:
            purge.parse_table_specs(bad)
            assert False, f"expected reject: {bad!r}"
        except ValueError as e:
            assert purge.GOV_PURGE_CONFIG in str(e)


def test_load_config_requires_retention_and_tables():
    try:
        purge.load_config({"PURGE_TABLES": "ticks:ts"})  # no retention
        assert False
    except ValueError as e:
        assert "PURGE_RETENTION_DAYS" in str(e)
    try:
        purge.load_config({"PURGE_RETENTION_DAYS": "30"})  # no tables
        assert False
    except ValueError as e:
        assert purge.GOV_PURGE_CONFIG in str(e)


def test_load_config_defaults_and_bounds():
    cfg = purge.load_config({"PURGE_RETENTION_DAYS": "30", "PURGE_TABLES": "ticks:ts"})
    assert cfg["batch_size"] == 5000 and cfg["batch_sleep_ms"] == 200
    assert cfg["max_batches_per_table"] == 100000
    try:
        purge.load_config({"PURGE_RETENTION_DAYS": "0", "PURGE_TABLES": "ticks:ts"})
        assert False
    except ValueError as e:
        assert purge.GOV_PURGE_CONFIG in str(e)


# ----------------------------------------------------------------- UTC cutoff ----------------------
def test_compute_cutoff_utc():
    now = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)
    assert purge.compute_cutoff_utc(now, 30) == "2026-05-23 12:00:00"


def test_compute_cutoff_rejects_naive():
    try:
        purge.compute_cutoff_utc(datetime(2026, 6, 22, 12, 0, 0), 30)
        assert False
    except ValueError:
        pass


# ----------------------------------------------------------------- chunked delete ------------------
class _FakeExec:
    """Simulates LIMIT-bounded deletes draining `rows` total in `batch` chunks."""
    def __init__(self, rows, batch):
        self.remaining = rows
        self.batch = batch
        self.calls = 0

    def __call__(self, sql, params):
        self.calls += 1
        assert "ORDER BY" in sql and "LIMIT" in sql  # bounded, ordered (oldest first)
        n = min(self.batch, self.remaining)
        self.remaining -= n
        return n


def test_purge_table_chunks_and_sleeps_between_only():
    ex = _FakeExec(rows=23, batch=10)            # -> 10, 10, 3  (3 batches, 2 sleeps)
    sleeps = []
    emits = []
    res = purge.purge_table(ex, "ticks", "ts", "2026-05-23 12:00:00", 10,
                            sleep_fn=lambda: sleeps.append(1), max_batches=0,
                            emit_fn=lambda ev, **f: emits.append((ev, f)))
    assert res == {"table": "ticks", "rows": 23, "batches": 3, "capped": False}
    assert ex.calls == 3
    assert len(sleeps) == 2                       # sleep BETWEEN batches only, never after the last
    assert [e[0] for e in emits] == ["PURGE_BATCH", "PURGE_BATCH", "PURGE_BATCH"]


def test_purge_table_single_drained_batch_no_sleep():
    ex = _FakeExec(rows=4, batch=10)             # one short batch -> done, zero sleeps
    sleeps = []
    res = purge.purge_table(ex, "ticks", "ts", "c", 10, sleep_fn=lambda: sleeps.append(1),
                            max_batches=0, emit_fn=lambda *a, **k: None)
    assert res["batches"] == 1 and res["rows"] == 4 and sleeps == []


def test_purge_table_respects_max_batches_cap():
    ex = _FakeExec(rows=10_000, batch=10)        # would be 1000 batches; cap at 5
    caps = []
    res = purge.purge_table(ex, "ticks", "ts", "c", 10, sleep_fn=lambda: None,
                            max_batches=5, emit_fn=lambda ev, **f: caps.append(ev))
    assert res["batches"] == 5 and res["capped"] is True
    assert "PURGE_BATCH_CAP" in caps


def test_purge_table_emits_progress_fields():
    ex = _FakeExec(rows=10, batch=10)
    emits = []
    purge.purge_table(ex, "candles_M1", "timestamp", "2026-05-23 00:00:00", 10,
                      sleep_fn=lambda: None, max_batches=0, emit_fn=lambda ev, **f: emits.append(f))
    f = emits[0]
    assert f["table"] == "candles_M1" and f["rows"] == 10 and f["cumulative"] == 10
    assert f["cutoff_utc"] == "2026-05-23 00:00:00"


# ----------------------------------------------------------------- main() exit codes ---------------
def test_main_bypass_exit_zero(capsys, monkeypatch):
    monkeypatch.delenv("RUN_ENV", raising=False)
    assert purge.main() == 0
    assert "[PURGE_BYPASS]" in capsys.readouterr().out


def test_main_blocked_exit_one(monkeypatch):
    monkeypatch.setenv("RUN_ENV", "PRODUCTION")
    monkeypatch.delenv("HERMES_PURGE_ENABLED", raising=False)
    logged = {}
    monkeypatch.setattr(purge, "_build_logger", lambda: type("L", (), {
        "error": lambda self, *a, **k: logged.setdefault("err", (a, k)),
        "warning": lambda self, *a, **k: None, "info": lambda self, *a, **k: None})())
    assert purge.main() == 1
    assert purge.GOV_PURGE_DISABLED in str(logged["err"])


if __name__ == "__main__":
    import sys
    sys.exit(os.system(f"python3 -m pytest {__file__} -q"))
