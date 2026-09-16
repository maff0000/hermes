"""WO-HELM-HERMES-INCIDENT-CANONICAL-PIPELINE-CONFIG-AND-READINESS-TRUTH-0001.

Regression coverage for the incident: HERMES /health reported GREEN for 11 days (2026-09-05..09-16)
while the canonical hermes:candles:XAU_USD:* publish path was completely frozen, because health_state
was derived only from the legacy tick/M1 watchdog. These tests pin the pure derivation the fix adds:
a missing, expired, unparseable, or FAIL-status publisher heartbeat must downgrade /health's readiness
signal — it must never be silently absorbed the way it was during the real incident.
"""
import json

from utils.hermes_canonical_publisher_health_v1 import canonical_publisher_health, worse_of


# --------------------------------------------------------------------------- worse_of
def test_worse_of_ordering():
    assert worse_of("GREEN", "AMBER") == "AMBER"
    assert worse_of("AMBER", "RED") == "RED"
    assert worse_of("RED", "GREEN") == "RED"
    assert worse_of("GREEN", "GREEN") == "GREEN"
    assert worse_of("AMBER", None) == "AMBER"
    assert worse_of(None, "RED") == "RED"
    assert worse_of(None, None) is None


# --------------------------------------------------------------------------- the actual incident shape
def test_missing_heartbeat_is_not_ready_never_silently_green():
    # This is EXACTLY the incident: no exception, no crash — just an absent/expired key. The watchdog-only
    # health_state stayed GREEN through this for 11 real days; the fix must never do that again.
    block, downgrade = canonical_publisher_health(None)
    assert downgrade == "RED"
    assert block["observed"] is False
    assert block["reason"] == "HEARTBEAT_KEY_MISSING"


def test_empty_string_heartbeat_is_not_ready():
    block, downgrade = canonical_publisher_health("")
    assert downgrade == "RED"
    assert block["observed"] is False


def test_unparseable_heartbeat_is_not_ready_never_raises():
    block, downgrade = canonical_publisher_health("{not json")
    assert downgrade == "RED"
    assert block["reason"] == "HEARTBEAT_KEY_UNPARSEABLE"


# --------------------------------------------------------------------------- status mapping
def _hb(status, **extra):
    return json.dumps({"status": status, "latest_key_freshness": {"M1": "FRESH"},
                       "generated_at_utc": "2026-09-16T06:00:00.000Z", **extra})


def test_status_fail_downgrades_to_red_not_ready():
    block, downgrade = canonical_publisher_health(_hb("FAIL"))
    assert downgrade == "RED"
    assert block["observed"] is True and block["status"] == "FAIL"


def test_status_warn_downgrades_to_amber_degraded_not_blocking():
    block, downgrade = canonical_publisher_health(_hb("WARN"))
    assert downgrade == "AMBER"


def test_status_ok_never_downgrades():
    block, downgrade = canonical_publisher_health(_hb("OK"))
    assert downgrade is None
    assert block["status"] == "OK"


def test_unrecognised_status_fails_closed_never_treated_as_ok():
    block, downgrade = canonical_publisher_health(_hb("SOMETHING_NEW"))
    assert downgrade == "RED"   # fail-closed: an unrecognised status is never silently OK


def test_block_carries_freshness_and_d1_state_for_operator_visibility():
    block, _ = canonical_publisher_health(_hb("WARN", d1_state="PENDING_FIRST_DAILY_SEAL"))
    assert block["latest_key_freshness"] == {"M1": "FRESH"}
    assert block["d1_state"] == "PENDING_FIRST_DAILY_SEAL"
    assert block["generated_at_utc"] == "2026-09-16T06:00:00.000Z"


# --------------------------------------------------------------------------- exact incident reproduction
def test_reproduces_the_real_incident_heartbeat_payload():
    # Captured verbatim (redacted only of the timestamp) from the live DEV heartbeat during the outage.
    incident_heartbeat = json.dumps({
        "schema_version": "v1", "service_identity": "hermes-signal",
        "updated_at_utc": "2026-09-16T06:11:24.675Z", "generated_at_utc": "2026-09-16T06:11:24.675Z",
        "deployed_sha": "a985733aa51e4696b05588c881274ce4cff397a8", "run_env": "STAGING",
        "status": "FAIL",
        "latest_key_freshness": {"M1": "UNKNOWN", "M5": "UNKNOWN", "M15": "UNKNOWN",
                                 "H1": "UNKNOWN", "H4": "UNKNOWN"},
        "history_forward_state": "ACTIVE", "d1_state": "PENDING_FIRST_DAILY_SEAL",
    })
    block, downgrade = canonical_publisher_health(incident_heartbeat)
    assert downgrade == "RED", "the real incident payload must mechanically force NOT_READY"
    assert block["latest_key_freshness"] == {"M1": "UNKNOWN", "M5": "UNKNOWN", "M15": "UNKNOWN",
                                              "H1": "UNKNOWN", "H4": "UNKNOWN"}
