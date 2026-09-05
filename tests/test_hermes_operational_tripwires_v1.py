"""HERMES operational tripwires — pure-logic unit tests. No Redis/Docker/filesystem I/O.
WO-HELM-HERMES-DEV-PRE-PROD-RECOVERY-GATE-CLOSURE-0001.
"""
import json
from datetime import datetime, timezone

import pytest

import utils.hermes_operational_tripwires_v1 as tw

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


# =============================== capacity ceiling resolution ===============================
def test_ceiling_prefers_redis_maxmemory_when_set():
    assert tw.resolve_effective_capacity_bytes(
        redis_maxmemory=17179869184, cgroup_limit_bytes=1610612736, governed_ceiling_bytes=999
    ) == 17179869184


def test_ceiling_falls_back_to_cgroup_when_maxmemory_unset():
    assert tw.resolve_effective_capacity_bytes(
        redis_maxmemory=0, cgroup_limit_bytes=805306368, governed_ceiling_bytes=999
    ) == 805306368


def test_ceiling_falls_back_to_governed_env_when_neither_available():
    assert tw.resolve_effective_capacity_bytes(
        redis_maxmemory=0, cgroup_limit_bytes=None, governed_ceiling_bytes=1073741824
    ) == 1073741824


def test_ceiling_fails_loud_when_nothing_resolves():
    with pytest.raises(ValueError) as e:
        tw.resolve_effective_capacity_bytes(redis_maxmemory=0, cgroup_limit_bytes=None, governed_ceiling_bytes=None)
    assert "GOV-CAPACITY-TRIPWIRE-001" in str(e.value)


# =============================== capacity evaluation / severity bands ===============================
@pytest.mark.parametrize("pct_target,expected_severity", [
    (10.0, None), (69.9, None), (70.0, "WARNING"), (75.0, "WARNING"),
    (80.0, "CRITICAL"), (85.0, "CRITICAL"), (90.0, "FATAL"), (99.0, "FATAL"), (100.0, "FATAL"),
])
def test_capacity_severity_bands(pct_target, expected_severity):
    limit = 1_000_000
    used = int(limit * pct_target / 100)
    result = tw.evaluate_capacity(used_memory_bytes=used, effective_limit_bytes=limit, observed_utc=NOW)
    assert result["severity"] == expected_severity
    assert abs(result["utilisation_pct"] - pct_target) < 0.2


def test_capacity_evidence_fields_present():
    result = tw.evaluate_capacity(used_memory_bytes=500, effective_limit_bytes=1000, observed_utc=NOW)
    assert result["used_memory_bytes"] == 500
    assert result["effective_limit_bytes"] == 1000
    assert result["observed_utc"] == NOW.isoformat()


def test_capacity_rejects_non_positive_limit():
    with pytest.raises(ValueError) as e:
        tw.evaluate_capacity(used_memory_bytes=1, effective_limit_bytes=0, observed_utc=NOW)
    assert "GOV-CAPACITY-TRIPWIRE-002" in str(e.value)


# =============================== restart-storm ===============================
def test_restart_storm_no_prior_state_is_baseline_not_a_fault():
    r = tw.evaluate_restart_storm(container="hermes-signal", current_restart_count=42,
                                  current_status="running", current_health="healthy",
                                  last_known_count=None, observed_utc=NOW)
    assert r["fault_code"] is None and r["severity"] is None
    assert r["delta_since_last_check"] is None


def test_restart_storm_single_new_restart_is_warning():
    r = tw.evaluate_restart_storm(container="hermes-signal", current_restart_count=43,
                                  current_status="running", current_health="healthy",
                                  last_known_count=42, observed_utc=NOW)
    assert r["delta_since_last_check"] == 1
    assert r["fault_code"] == "CONTAINER_RESTART_STORM"
    assert r["severity"] == "WARNING"


def test_restart_storm_three_or_more_new_restarts_is_critical():
    r = tw.evaluate_restart_storm(container="hermes-cache", current_restart_count=8908,
                                  current_status="running", current_health=None,
                                  last_known_count=8905, observed_utc=NOW)
    assert r["delta_since_last_check"] == 3
    assert r["severity"] == "CRITICAL"


def test_restart_storm_caught_mid_restart_even_with_no_count_delta():
    """The real PROD signature: docker's restart policy is actively cycling the container between
    polls, but the count sampled at any given instant might not have ticked yet -- catch the state."""
    r = tw.evaluate_restart_storm(container="hermes-signal", current_restart_count=1482,
                                  current_status="restarting", current_health=None,
                                  last_known_count=1482, observed_utc=NOW)
    assert r["delta_since_last_check"] == 0
    assert r["fault_code"] == "CONTAINER_UNHEALTHY_PROLONGED"
    assert r["severity"] == "CRITICAL"


def test_restart_storm_unhealthy_flagged_even_when_status_running():
    r = tw.evaluate_restart_storm(container="hermes-cache", current_restart_count=100,
                                  current_status="running", current_health="unhealthy",
                                  last_known_count=100, observed_utc=NOW)
    assert r["fault_code"] == "CONTAINER_UNHEALTHY_PROLONGED"


def test_restart_storm_stable_healthy_container_is_clean():
    r = tw.evaluate_restart_storm(container="hermes-signal", current_restart_count=0,
                                  current_status="running", current_health="healthy",
                                  last_known_count=0, observed_utc=NOW)
    assert r["fault_code"] is None and r["severity"] is None


# =============================== hostname drift ===============================
def test_hostname_no_drift_when_all_agree():
    r = tw.evaluate_hostname_drift(expected_hostname="HERMES", persisted_hostname="HERMES",
                                   transient_hostname="HERMES", observed_utc=NOW)
    assert r["fault_code"] is None and r["severity"] is None


def test_hostname_drift_detected_persisted_mismatch():
    """The exact INC-HERMES-2026-09-PROD-HOSTNAME-DRIFT signature: static (persisted) file diverged,
    transient (kernel) hostname is unaffected because no reboot occurred."""
    r = tw.evaluate_hostname_drift(expected_hostname="HERMES", persisted_hostname="TRADING-1",
                                   transient_hostname="HERMES", observed_utc=NOW)
    assert r["persisted_mismatch"] is True
    assert r["transient_mismatch"] is True   # TRADING-1 (persisted) != HERMES (transient) too
    assert r["fault_code"] == "HOST_IDENTITY_DRIFT"
    assert r["severity"] == "FATAL"


def test_hostname_drift_detected_even_without_transient_reading():
    r = tw.evaluate_hostname_drift(expected_hostname="HERMES", persisted_hostname="TRADING-1",
                                   transient_hostname=None, observed_utc=NOW)
    assert r["fault_code"] == "HOST_IDENTITY_DRIFT"
    assert r["transient_mismatch"] is False   # can't assert a mismatch we have no reading for


def test_hostname_dev_expected_value():
    r = tw.evaluate_hostname_drift(expected_hostname="dell-debian", persisted_hostname="dell-debian",
                                   transient_hostname="dell-debian", observed_utc=NOW)
    assert r["fault_code"] is None


# =============================== state file round-trip ===============================
def test_state_round_trip(tmp_path):
    p = tmp_path / "state.json"
    tw.save_state(str(p), {"restart_counts": {"hermes-signal": 5}})
    assert tw.load_state(str(p)) == {"restart_counts": {"hermes-signal": 5}}


def test_state_missing_file_returns_empty_dict(tmp_path):
    p = tmp_path / "does_not_exist.json"
    assert tw.load_state(str(p)) == {}


def test_state_corrupt_file_returns_empty_dict(tmp_path):
    p = tmp_path / "corrupt.json"
    p.write_text("{not valid json")
    assert tw.load_state(str(p)) == {}


# =============================== cgroup reader (real filesystem, safe paths only) ===============================
def test_cgroup_reader_returns_none_when_no_cgroup_files_exist(monkeypatch, tmp_path):
    # Point both candidate paths at nonexistent files by monkeypatching Path itself is overkill here;
    # simplest safe proof is that on a host/container with no readable cgroup file the function
    # degrades to None rather than raising -- exercised directly against a guaranteed-missing path
    # via the real function, since it already catches OSError internally.
    import utils.hermes_operational_tripwires_v1 as mod
    orig = mod.Path
    class FakePath:
        def __init__(self, p): self._p = p
        def read_text(self):
            raise FileNotFoundError(self._p)
    monkeypatch.setattr(mod, "Path", lambda p: FakePath(p))
    assert mod.read_cgroup_memory_limit() is None
    monkeypatch.setattr(mod, "Path", orig)
