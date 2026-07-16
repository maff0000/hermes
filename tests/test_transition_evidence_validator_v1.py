"""
Tests for the HERMES XAU market-transition evidence validator (v1).
WO-HELM-HERMES-MARKET-TRANSITION-EVIDENCE-VALIDATOR-0001

Pure stdlib. Loads the validator module by file path (it is a CLI tool, not a
package) and drives it against the committed synthetic fixtures. 16 scenario
tests assert the overall verdict AND the specific acceptance condition, plus a
handful of unit tests for the DST-aware governed-window computation, timestamp
parsing, and the runtime-identity file path.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL_PATH = REPO_ROOT / "tools" / "hermes_transition_evidence_validator_v1.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "transition_evidence_v1"
SCHEDULE = REPO_ROOT / "config" / "market_hours_schedule.v1.json"
DATE = "2026-07-16"


def _load_module():
    spec = importlib.util.spec_from_file_location("hermes_transition_evidence_validator_v1", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass/type resolution can find the module.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


V = _load_module()


def run_fixture(name, *, cadence=60, grace=None, runtime_identity=None, instrument="XAU_USD"):
    return V.run(str(FIXTURES / name), str(SCHEDULE), instrument, DATE, cadence, grace,
                 str(FIXTURES / runtime_identity) if runtime_identity else None)


# --------------------------------------------------------------------------- #
# 16 acceptance scenarios                                                     #
# --------------------------------------------------------------------------- #

def test_scenario_01_correct_closure_reopening_pass():
    r = run_fixture("01_pass_correct_closure_reopening.log")
    assert r["overall"] == V.PASS
    assert all(c["state"] != V.FAIL for c in r["conditions"].values())
    # every required condition is PASS
    for k in ("END_MARKER_PRESENT", "REQUIRED_WINDOW_COVERED", "SAMPLE_CONTINUITY",
              "CLOSURE_CLASSIFICATION", "NO_FABRICATED_FRESHNESS",
              "NO_INACTIVITY_INCIDENT_DURING_CLOSURE", "NO_CLOSURE_DRIVEN_RECONNECT",
              "CORRECT_GRACE_TIMING", "GENUINE_RESUMED_FLOW_OR_ESCALATION",
              "RUNTIME_IDENTITY_CONTINUITY"):
        assert r["conditions"][k]["state"] == V.PASS, k


def test_scenario_02_missing_end_not_pass():
    r = run_fixture("02_missing_end_marker.log")
    assert r["overall"] != V.PASS
    assert r["overall"] in (V.INDETERMINATE, V.FAIL)
    assert r["end_marker_present"] is False
    assert r["conditions"]["END_MARKER_PRESENT"]["state"] == V.INDETERMINATE


def test_scenario_03_sampling_gap_flagged():
    r = run_fixture("03_sampling_gap.log")
    assert r["overall"] == V.FAIL
    assert r["conditions"]["SAMPLE_CONTINUITY"]["state"] == V.FAIL
    assert r["max_sample_gap_s"] > 3 * 60


def test_scenario_04_false_open_during_closure_fail():
    r = run_fixture("04_false_open_during_closure.log")
    assert r["overall"] == V.FAIL
    assert r["conditions"]["CLOSURE_CLASSIFICATION"]["state"] == V.FAIL


def test_scenario_05_tick_refreshed_during_closure_fail():
    r = run_fixture("05_tick_refreshed_during_closure.log")
    assert r["overall"] == V.FAIL
    c = r["conditions"]["NO_FABRICATED_FRESHNESS"]
    assert c["state"] == V.FAIL
    assert c["fabricated_tick"] is True


def test_scenario_06_candle_fabricated_during_closure_fail():
    r = run_fixture("06_candle_fabricated_during_closure.log")
    assert r["overall"] == V.FAIL
    c = r["conditions"]["NO_FABRICATED_FRESHNESS"]
    assert c["state"] == V.FAIL
    assert c["fabricated_candle"] is True


def test_scenario_07_incident_during_closure_fail():
    r = run_fixture("07_incident_during_closure.log")
    assert r["overall"] == V.FAIL
    assert r["conditions"]["NO_INACTIVITY_INCIDENT_DURING_CLOSURE"]["state"] == V.FAIL
    assert r["incident_counts"]["delta_closure"] > 0


def test_scenario_08_reconnect_during_closure_fail():
    r = run_fixture("08_reconnect_during_closure.log")
    assert r["overall"] == V.FAIL
    assert r["conditions"]["NO_CLOSURE_DRIVEN_RECONNECT"]["state"] == V.FAIL
    assert r["reconnect_counts"]["delta_closure"] > 0


def test_scenario_09_rest_masks_stream_fail():
    r = run_fixture("09_rest_masks_stream.log")
    assert r["overall"] == V.FAIL
    assert r["conditions"]["REST_QUOTE_VS_STREAM_SEPARATION"]["state"] == V.FAIL


def test_scenario_10_false_green_during_grace_fail():
    r = run_fixture("10_false_green_during_grace.log")
    assert r["overall"] == V.FAIL
    assert r["conditions"]["NO_FALSE_GREEN_DURING_GRACE"]["state"] == V.FAIL


def test_scenario_11_resume_within_grace_pass():
    r = run_fixture("11_resume_within_grace.log")
    assert r["overall"] == V.PASS
    assert r["conditions"]["GENUINE_RESUMED_FLOW_OR_ESCALATION"]["state"] == V.PASS
    assert r["conditions"]["NO_FALSE_GREEN_DURING_GRACE"]["state"] == V.PASS
    assert r["first_resumed_candle"] is not None
    assert r["health_restoration_ts"] is not None


def test_scenario_12_pathb_escalation_pass():
    r = run_fixture("12_pathb_escalation_after_grace.log")
    assert r["overall"] == V.PASS
    c = r["conditions"]["GENUINE_RESUMED_FLOW_OR_ESCALATION"]
    assert c["state"] == V.PASS
    assert "Path B" in c["detail"]
    # flow genuinely never resumed in this Path-B log
    assert r["first_resumed_candle"] is None
    assert r["conditions"]["CORRECT_GRACE_TIMING"]["state"] == V.PASS


def test_scenario_13_no_escalation_silent_fail():
    r = run_fixture("13_no_escalation_silent_stall.log")
    assert r["overall"] == V.FAIL
    assert r["conditions"]["GENUINE_RESUMED_FLOW_OR_ESCALATION"]["state"] == V.FAIL


def test_scenario_14_runtime_identity_change_fail():
    r = run_fixture("14_runtime_identity_change.log")
    assert r["overall"] == V.FAIL
    assert r["conditions"]["RUNTIME_IDENTITY_CONTINUITY"]["state"] == V.FAIL
    assert r["observer_start_pid"] != r["observer_end_pid"]


def test_scenario_15_wtico_guessed_closed_fail():
    r = run_fixture("15_wtico_guessed_closed.log")
    assert r["overall"] == V.FAIL
    assert r["conditions"]["WTICO_FAIL_LOUD"]["state"] == V.FAIL
    # SPX still correctly fails loud in this fixture
    assert r["conditions"]["SPX500_FAIL_LOUD"]["state"] == V.PASS


def test_scenario_16_spx500_guessed_closed_fail():
    r = run_fixture("16_spx500_guessed_closed.log")
    assert r["overall"] == V.FAIL
    assert r["conditions"]["SPX500_FAIL_LOUD"]["state"] == V.FAIL
    assert r["conditions"]["WTICO_FAIL_LOUD"]["state"] == V.PASS


# --------------------------------------------------------------------------- #
# Runtime-identity file path (bonus coverage of the optional --runtime-identity)
# --------------------------------------------------------------------------- #

def test_runtime_identity_file_stable_keeps_pass():
    r = run_fixture("01_pass_correct_closure_reopening.log",
                    runtime_identity="runtime_identity_stable.json")
    assert r["conditions"]["RUNTIME_IDENTITY_CONTINUITY"]["state"] == V.PASS
    assert r["overall"] == V.PASS


def test_runtime_identity_file_changed_forces_fail():
    r = run_fixture("01_pass_correct_closure_reopening.log",
                    runtime_identity="runtime_identity_changed.json")
    assert r["conditions"]["RUNTIME_IDENTITY_CONTINUITY"]["state"] == V.FAIL
    assert r["overall"] == V.FAIL


# --------------------------------------------------------------------------- #
# Unit tests: DST-aware window, parsing, output field completeness            #
# --------------------------------------------------------------------------- #

def test_governed_window_dst_edt():
    cfg = json.loads(SCHEDULE.read_text())
    gw = V.compute_governed_window(cfg, "XAU_USD", "2026-07-16", None)
    assert gw.schedule_resolution == "RESOLVED"
    assert gw.named_schedule == "metals"
    # 17:00-18:00 EDT == 21:00-22:00 UTC in July
    assert V.iso(gw.closure_start) == "2026-07-16T21:00:00Z"
    assert V.iso(gw.closure_end) == "2026-07-16T22:00:00Z"
    assert V.iso(gw.reopening) == "2026-07-16T22:00:00Z"
    assert gw.grace_seconds == 300
    assert V.iso(gw.grace_end) == "2026-07-16T22:05:00Z"


def test_governed_window_dst_est_winter():
    cfg = json.loads(SCHEDULE.read_text())
    # January -> EST (UTC-5): 17:00-18:00 EST == 22:00-23:00 UTC
    gw = V.compute_governed_window(cfg, "XAU_USD", "2026-01-15", None)
    assert V.iso(gw.closure_start) == "2026-01-15T22:00:00Z"
    assert V.iso(gw.closure_end) == "2026-01-15T23:00:00Z"


def test_governed_window_fail_closed_instrument():
    cfg = json.loads(SCHEDULE.read_text())
    gw = V.compute_governed_window(cfg, "WTICO_USD", "2026-07-16", None)
    assert gw.schedule_resolution == "FAIL_CLOSED_NONE"
    assert gw.closure_start is None


def test_parse_utc_forms():
    assert V.iso(V.parse_utc("2026-07-16T21:00:00Z")) == "2026-07-16T21:00:00Z"
    assert V.iso(V.parse_utc("2026-07-16T21:00:00.000Z")) == "2026-07-16T21:00:00Z"
    assert V.iso(V.parse_utc("21:00:00Z", "2026-07-16")) == "2026-07-16T21:00:00Z"
    assert V.parse_utc("not-a-ts") is None


def test_output_contains_all_wo_fields():
    r = run_fixture("01_pass_correct_closure_reopening.log")
    required_fields = [
        "observer_start", "observer_end", "end_marker_present", "sample_count",
        "monotonic", "max_sample_gap_s", "pre_close_window", "closure_window",
        "reopening", "grace_end", "post_grace_window", "last_pre_close_tick",
        "last_pre_close_candle", "samples_during_closure", "first_resumed_tick",
        "first_resumed_candle", "health_restoration_ts", "incident_counts",
        "reconnect_counts", "rest_quote_vs_stream_separation",
        "runtime_identity_continuity", "wtico_fail_loud_evidence",
        "spx500_fail_loud_evidence", "conditions", "overall",
    ]
    for f in required_fields:
        assert f in r, f
    # per-acceptance-condition result map present and each has a state
    for k, v in r["conditions"].items():
        assert v["state"] in (V.PASS, V.FAIL, V.INDETERMINATE, V.NOT_OBSERVED), k


def test_json_serialisable_and_deterministic():
    r1 = run_fixture("01_pass_correct_closure_reopening.log")
    r2 = run_fixture("01_pass_correct_closure_reopening.log")
    r1.pop("generated_utc", None)
    r2.pop("generated_utc", None)
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)


def test_no_malformed_lines_in_fixtures():
    for log in sorted(FIXTURES.glob("*.log")):
        r = V.run(str(log), str(SCHEDULE), "XAU_USD", DATE, 60, None, None)
        assert r["malformed_line_count"] == 0, f"{log.name}: {r['malformed_lines']}"
