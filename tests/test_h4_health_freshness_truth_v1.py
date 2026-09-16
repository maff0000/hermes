"""WO-HELM-HERMES-H4-COMPLETION-DRIVEN-SEAL-AND-HEALTH-FRESHNESS-TRUTH-0001 — freshness-truth half.

Real incident this closes: the 06:00-10:00 H4 candle sat well past its own governed valid_until_utc
(status still "OK") while the publisher heartbeat and /health both kept reporting it FRESH/healthy,
because freshness was derived ONLY from the stored status field, never compared against wall-clock now.
"""
import datetime
import json

from utils import hermes_runtime_publisher_steps_v1 as steps

UTC = datetime.timezone.utc
INST = "XAU_USD"


def _payload(status="OK", generated_at=None, valid_until=None):
    now = datetime.datetime.now(UTC)
    generated_at = generated_at or now
    valid_until = valid_until or (now + datetime.timedelta(hours=4))
    return json.dumps({
        "status": status,
        "generated_at_utc": generated_at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "valid_until_utc": valid_until.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "data": {"instrument": INST, "timeframe": "H4"},
    })


class FakeRedis:
    def __init__(self, kv=None): self.kv = kv or {}
    def get(self, key): return self.kv.get(key)


# --------------------------------------------------------------------------- valid OK -> FRESH (unchanged behaviour)
def test_valid_ok_within_validity_window_is_fresh():
    now = datetime.datetime.now(UTC)
    r = FakeRedis({f"hermes:candles:{INST}:H4:latest:v1":
                   _payload(status="OK", generated_at=now, valid_until=now + datetime.timedelta(hours=4))})
    assert steps._freshness(r, "H4") == "FRESH"


# --------------------------------------------------------------------------- the real incident condition
def test_expired_ok_candle_is_stale_not_fresh():
    now = datetime.datetime.now(UTC)
    # exactly the real shape: status=OK, generated 4h+7min ago, valid_until_utc already 7 minutes in the past
    r = FakeRedis({f"hermes:candles:{INST}:H4:latest:v1":
                   _payload(status="OK", generated_at=now - datetime.timedelta(hours=4, minutes=7),
                            valid_until=now - datetime.timedelta(minutes=7))})
    assert steps._freshness(r, "H4") == "STALE_EXPIRED"


def test_freshness_boundary_is_valid_until_utc_itself_no_invented_grace_period():
    now = datetime.datetime.now(UTC)
    # one second before expiry -> still fresh; one second after -> stale. No grace window either side.
    r_before = FakeRedis({f"hermes:candles:{INST}:H4:latest:v1":
                          _payload(status="OK", valid_until=now + datetime.timedelta(seconds=1))})
    r_after = FakeRedis({f"hermes:candles:{INST}:H4:latest:v1":
                         _payload(status="OK", valid_until=now - datetime.timedelta(seconds=1))})
    assert steps._freshness(r_before, "H4") == "FRESH"
    assert steps._freshness(r_after, "H4") == "STALE_EXPIRED"


def test_missing_key_still_unknown():
    r = FakeRedis({})
    assert steps._freshness(r, "H4") == "UNKNOWN"


def test_non_ok_status_passes_through_unchanged():
    now = datetime.datetime.now(UTC)
    r = FakeRedis({f"hermes:candles:{INST}:H4:latest:v1":
                   _payload(status="SOURCE_INCOMPLETE", valid_until=now + datetime.timedelta(hours=4))})
    assert steps._freshness(r, "H4") == "SOURCE_INCOMPLETE"


def test_missing_valid_until_utc_does_not_break_existing_ok_behaviour():
    payload = json.dumps({"status": "OK", "data": {"instrument": INST, "timeframe": "H4"}})
    r = FakeRedis({f"hermes:candles:{INST}:H4:latest:v1": payload})
    assert steps._freshness(r, "H4") == "FRESH"


# --------------------------------------------------------------------------- flows correctly into publisher status
def test_one_expired_timeframe_degrades_publisher_status_to_warn():
    fresh = {"M1": "FRESH", "M5": "FRESH", "M15": "FRESH", "H1": "FRESH", "H4": "STALE_EXPIRED"}
    assert steps.derive_publisher_status(fresh) == "WARN"


def test_all_expired_degrades_to_fail():
    stale = {"M1": "STALE_EXPIRED", "H1": "STALE_EXPIRED"}
    assert steps.derive_publisher_status(stale) == "FAIL"
