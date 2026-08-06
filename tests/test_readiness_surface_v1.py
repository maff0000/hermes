"""Scope-aware readiness surface contract. WO-...-PROVENANCE-SCOPE-READINESS-...-0001. Pure, no I/O."""
from datetime import datetime, timezone

import pytest

import utils.hermes_readiness_surface_v1 as rs

UTC = timezone.utc
NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
GOOD_SHA = "cdbaabd0cc3a069da8357bd8a2183a5261e3b99c"
BI = {"source_sha": GOOD_SHA, "image_ref": "hermes-signal:prod-cdbaabd0", "build_utc": "2026-08-06T09:11:15Z",
      "build_identity_valid": True, "build_identity_reasons": []}
REG = {"loaded": True, "rows": 14, "active": ["XAU_USD"], "not_enabled": 13, "invalid_active": 0, "malformed_capability": 0}
CAL = {"index_cash": {"validation_status": "ASSUMPTION_REQUIRES_PROVIDER", "production_approved": False, "fault_code": "SCHEDULE_AMBIGUOUS_FAIL_CLOSED"},
       "energy": {"validation_status": "ASSUMPTION_REQUIRES_PROVIDER", "production_approved": False, "fault_code": "SCHEDULE_AMBIGUOUS_FAIL_CLOSED"}}


def _report(**over):
    kw = dict(now=NOW, build_identity=BI, registry=REG, master_enabled=False, publisher_mode="DISABLED",
              pilot_scope={"XAU_USD"}, calendar_provenance=CAL, core_health="GREEN", db_ok=True, redis_ok=True,
              stream_count=1, consumer_live="false", order_path_present=False, backfill_execution="false")
    kw.update(over)
    return rs.build_readiness_report(**kw)


# ============================ current authorised mixed state ============================
def test_authorised_dark_state_is_green():
    r = _report()
    assert r["readiness"]["overall"] == rs.OK and r["readiness"]["fault_codes"] == []
    assert r["identity"]["source_sha"] == GOOD_SHA and r["identity"]["build_identity_valid"] is True
    assert r["registry"]["row_count"] == 14 and r["registry"]["active_instruments"] == ["XAU_USD"] and r["registry"]["not_enabled_count"] == 13
    assert r["pilot"]["xau_pilot_state"] == "ACTIVE_AUTHORISED"
    assert r["expansion"]["expansion_master_enabled"] is False and r["expansion"]["publisher_mode"] == "DISABLED"
    assert r["expansion"]["expansion_aggregate_state"] == "DARK_MASTER_DISABLED"
    assert r["calendar"]["SPX500_USD"] == "BLOCKED_CALENDAR" and r["calendar"]["WTICO_USD"] == "BLOCKED_CALENDAR"
    assert r["boundaries"]["consumer_state"] == "OFF" and r["boundaries"]["order_path_state"] == "ABSENT"
    assert r["boundaries"]["backfill_execution_state"] == "OFF" and r["core"]["stream_count"] == 1
    # separate concepts, never collapsed to one boolean
    assert r["readiness"]["expansion_readiness"] == "NOT_READY_DARK"
    assert r["readiness"]["trading_execution_readiness"] == "ABSENT"


def test_contract_version_and_timestamps():
    r = _report()
    assert r["identity"]["readiness_contract_version"] == rs.READINESS_CONTRACT_VERSION == "v1"
    assert r["timestamps"]["evaluated_utc"].endswith("+00:00") or r["timestamps"]["evaluated_utc"].endswith("Z")


# ============================ fail-closed authority-compliance breaches ============================
@pytest.mark.parametrize("over,fault", [
    ({"build_identity": {**BI, "source_sha": "UNKNOWN_SOURCE_SHA"}}, rs.F_SOURCE_MISSING),
    ({"build_identity": {**BI, "source_sha": None}}, rs.F_SOURCE_MISSING),
    ({"build_identity": {**BI, "source_sha": "abc123"}}, rs.F_SOURCE_MALFORMED),
    ({"registry": {"loaded": False}}, rs.F_REGISTRY_LOAD),
    ({"registry": {**REG, "invalid_active": 1}}, rs.F_ACTIVE_INCOMPLETE),
    ({"registry": {**REG, "malformed_capability": 1}}, rs.F_MALFORMED_CAP),
    ({"pilot_scope": set()}, rs.F_PILOT_INVALID),
    ({"registry": {**REG, "active": []}}, rs.F_PILOT_INACTIVE),
    ({"master_enabled": True}, rs.F_MASTER_TRUE),
    ({"publisher_mode": "SHADOW"}, rs.F_MODE_NOT_DISABLED),
    ({"publisher_mode": "ACTIVE"}, rs.F_MODE_NOT_DISABLED),
    ({"seven_new_published_count": 1}, rs.F_SEVEN_NEW_PUBLISHED),
    ({"inactive_published_count": 1}, rs.F_INACTIVE_PUBLISHED),
    ({"stream_count": 0}, rs.F_STREAM_COUNT),
    ({"stream_count": 2}, rs.F_STREAM_COUNT),
    ({"calendar_provenance": {**CAL, "index_cash": {**CAL["index_cash"], "production_approved": True}}}, rs.F_CALENDAR_DRIFT),
    ({"consumer_live": "true"}, rs.F_CONSUMER_ON),
    ({"order_path_present": True}, rs.F_ORDER_PRESENT),
    ({"backfill_execution": "true"}, rs.F_BACKFILL_ON),
])
def test_each_breach_is_red_with_fault(over, fault):
    r = _report(**over)
    assert fault in r["readiness"]["fault_codes"]
    assert r["readiness"]["deployment_authority_compliance"] == rs.RED
    assert r["readiness"]["overall"] == rs.RED


def test_core_degraded_is_amber_not_red_when_compliant():
    r = _report(core_health="AMBER")
    assert r["readiness"]["deployment_authority_compliance"] == rs.OK   # authority still compliant
    assert r["readiness"]["overall"] == rs.AMBER                        # but core not fully green


def test_no_secret_leakage_in_report():
    import json
    r = _report()
    blob = json.dumps(r).lower()
    for tok in ("password", "secret", "token", "credential", "dsn", "db_password"):
        assert tok not in blob
