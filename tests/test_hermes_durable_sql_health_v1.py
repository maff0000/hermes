"""HERMES durable-SQL persistence READINESS TRUTH v1 — pure derivation tests.
WO-HELM-HERMES-DEV-DARWIN-DURABLE-CANONICAL-HISTORICAL-AUTHORITY-0001 (Architect review correction).

Proves: disabled writer never downgrades; connect/write faults -> RED; a genuine conflict -> AMBER;
routine source-incomplete refusals never downgrade; the worse of H4/D1 wins.
"""
import utils.hermes_durable_sql_health_v1 as dsh


def _status(**overrides):
    base = {"enabled": True, "attempted": 0, "written": 0, "match_skip": 0, "conflict_detected": 0,
           "connect_fail": 0, "write_fail": 0, "not_ok_skipped": 0, "source_incomplete_refused": 0}
    base.update(overrides)
    return base


def test_disabled_never_downgrades():
    assert dsh.durable_sql_writer_health({"enabled": False}) is None
    assert dsh.durable_sql_writer_health(None) is None


def test_healthy_enabled_never_downgrades():
    assert dsh.durable_sql_writer_health(_status()) is None


def test_routine_source_incomplete_refusal_never_downgrades():
    assert dsh.durable_sql_writer_health(_status(source_incomplete_refused=500)) is None


def test_connect_fail_is_red():
    assert dsh.durable_sql_writer_health(_status(connect_fail=1)) == "RED"


def test_write_fail_is_red():
    assert dsh.durable_sql_writer_health(_status(write_fail=1)) == "RED"


def test_conflict_is_amber():
    assert dsh.durable_sql_writer_health(_status(conflict_detected=1)) == "AMBER"


def test_red_beats_amber_on_same_writer():
    assert dsh.durable_sql_writer_health(_status(conflict_detected=1, write_fail=1)) == "RED"


def test_combined_takes_the_worse_of_h4_and_d1():
    block, downgrade = dsh.durable_sql_health(_status(), _status(conflict_detected=1))
    assert downgrade == "AMBER"
    assert block["h4"]["conflict_detected"] == 0
    assert block["d1"]["conflict_detected"] == 1

    block2, downgrade2 = dsh.durable_sql_health(_status(write_fail=1), _status(conflict_detected=1))
    assert downgrade2 == "RED"

    block3, downgrade3 = dsh.durable_sql_health(None, None)
    assert downgrade3 is None
    assert block3["h4"] == {"enabled": False}
    assert block3["d1"] == {"enabled": False}
