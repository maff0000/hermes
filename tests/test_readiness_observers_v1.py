"""Authoritative readiness observers (stream / order-path / inactive-publication).
WO-...-PROVENANCE-SCOPE-READINESS-...-0001 continuation. Bounded, read-only, fail-closed. No I/O beyond injected fakes."""
from datetime import datetime, timezone, timedelta

import pytest

import utils.hermes_readiness_observers_v1 as obs

UTC = timezone.utc
NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------------ fakes
class _St:
    def __init__(self, v): self.value = v


class _Health:
    def __init__(self, state, last): self.state = _St(state); self.last_tick_at = last


class _Adapter:
    """A pricing adapter with a .health surface and mutating methods that MUST NOT be called by an observer."""
    def __init__(self, state, last): self.health = _Health(state, last)
    def connect(self, *a, **k): raise AssertionError("observer must not connect()")
    def disconnect(self, *a, **k): raise AssertionError("observer must not disconnect()")
    def stream(self, *a, **k): raise AssertionError("observer must not stream()")
    def subscribe(self, *a, **k): raise AssertionError("observer must not subscribe()")


class _RaisingAdapter:
    @property
    def health(self): raise RuntimeError("health unavailable")


class _Task:
    def __init__(self, done): self._done = done
    def done(self): return self._done


class _Redis:
    """Read-only fake: only exists() is permitted; any write attr access fails the test."""
    def __init__(self, present=()): self.present = set(present); self.calls = []
    def exists(self, k): self.calls.append(k); return 1 if k in self.present else 0
    def __getattr__(self, name):
        raise AssertionError(f"observer touched a non-read Redis method: {name}")


# ------------------------------------------------------------------ stream
def _one(state, last, done=False):
    return [(_Adapter(state, last), _Task(done))]


def test_stream_exactly_one_active():
    r = obs.observe_stream(streams=_one("connected", NOW), now=NOW)
    assert r["active_count"] == 1 and r["count"] == 1 and r["health"] == "ACTIVE" and r["unknown"] is False
    assert r["last_heartbeat_utc"].startswith("2026-08-06")


@pytest.mark.parametrize("state,last,done,health", [
    ("connected", NOW, True, "STOPPED"),                       # task stopped
    ("connected", NOW - timedelta(seconds=120), False, "STALE"),  # heartbeat too old
    ("connected", None, False, "STARTING"),                    # connected, no tick yet
    ("connecting", None, False, "STARTING"),
    ("disconnected", None, False, "DISCONNECTED"),
    ("failed", None, False, "DISCONNECTED"),
])
def test_stream_not_active_is_zero(state, last, done, health):
    r = obs.observe_stream(streams=_one(state, last, done), now=NOW)
    assert r["active_count"] == 0 and r["count"] == 0 and r["health"] == health and r["unknown"] is False


def test_stream_none_adapter_is_stopped_not_unknown():
    r = obs.observe_stream(streams=[(None, None)], now=NOW)
    assert r["count"] == 0 and r["unknown"] is False


def test_stream_unrecognised_state_is_unknown_failclosed():
    r = obs.observe_stream(streams=_one("teleporting", NOW), now=NOW)
    assert r["unknown"] is True and r["count"] == -1 and r["health"] == "UNKNOWN"     # never the safe 1


def test_stream_observation_exception_is_unknown_failclosed():
    r = obs.observe_stream(streams=[(_RaisingAdapter(), _Task(False))], now=NOW)
    assert r["unknown"] is True and r["count"] == -1


def test_stream_two_active_is_rogue_second():
    streams = _one("connected", NOW) + _one("connected", NOW)
    r = obs.observe_stream(streams=streams, now=NOW)
    assert r["active_count"] == 2 and r["count"] == 2 and r["health"] == "MULTIPLE"


def test_stream_observer_never_mutates_adapter():
    # _Adapter.connect/stream/subscribe raise if touched; a clean observation proves read-only
    r = obs.observe_stream(streams=_one("connected", NOW), now=NOW)
    assert r["count"] == 1


# ------------------------------------------------------------------ order path
def test_order_path_absent_is_the_authorised_state():
    r = obs.observe_order_path(route_paths=["/health", "/ready", "/readiness", "/prices"],
                               state_attrs_present=set(), env={})
    assert r["state"] == "ABSENT" and r["present"] is False and r["evidence"] == []


def test_order_path_route_marks_present_disabled():
    r = obs.observe_order_path(route_paths=["/health", "/orders"], state_attrs_present=set(), env={})
    assert r["state"] == "PRESENT_DISABLED" and r["present"] is True and any("route:" in e for e in r["evidence"])


def test_order_path_component_marks_present_disabled():
    r = obs.observe_order_path(route_paths=["/health"], state_attrs_present={"order_router"}, env={})
    assert r["state"] == "PRESENT_DISABLED" and r["present"] is True


def test_order_path_enabling_flag_marks_present_enabled():
    r = obs.observe_order_path(route_paths=["/health"], state_attrs_present=set(),
                               env={"HERMES_ORDER_ENABLED": "true"})
    assert r["state"] == "PRESENT_ENABLED" and r["present"] is True


def test_order_path_observation_failure_is_unknown_failclosed():
    class _Boom:
        def __iter__(self): raise RuntimeError("route table unavailable")
    r = obs.observe_order_path(route_paths=_Boom(), state_attrs_present=set(), env={})
    assert r["state"] == "UNKNOWN" and r["present"] is True                          # never silently ABSENT


# ------------------------------------------------------------------ inactive publication
INACTIVE = ["XAG_USD", "EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY", "SPX500_USD", "WTICO_USD",
            "USD_CAD", "USD_CHF", "NZD_USD", "EUR_GBP", "EUR_JPY", "GBP_JPY"]  # 13 inactive


def test_inactive_none_when_registry_clean():
    rc = _Redis(present=())
    r = obs.observe_inactive_publication(inactive_instruments=INACTIVE, redis_client=rc, indicator_timeframes=("H1", "H4"))
    assert r["published_count"] == 0 and r["published_instruments"] == [] and r["observed_ok"] is True
    # bounded: no more than N*(4 single-key families + timeframes) exact checks, no scan
    assert r["checks"] <= len(INACTIVE) * (4 + 2)


def test_inactive_detects_a_leaked_publication():
    from utils.hermes_advanced_v1_selection_v1 import gaps_key
    rc = _Redis(present=(gaps_key("EUR_USD"),))
    r = obs.observe_inactive_publication(inactive_instruments=INACTIVE, redis_client=rc, indicator_timeframes=())
    assert r["published_count"] == 1 and r["published_instruments"] == ["EUR_USD"] and r["observed_ok"] is True


def test_inactive_no_redis_is_unobserved_not_zero():
    r = obs.observe_inactive_publication(inactive_instruments=INACTIVE, redis_client=None)
    assert r["observed_ok"] is False and r["published_count"] == 0    # fail-closed: caller must fault, not report zero


def test_inactive_redis_error_is_unobserved():
    class _ErrRedis:
        def exists(self, k): raise RuntimeError("redis down")
    r = obs.observe_inactive_publication(inactive_instruments=INACTIVE, redis_client=_ErrRedis())
    assert r["observed_ok"] is False


def test_inactive_uses_exact_canonical_keys_only():
    from utils.hermes_advanced_v1_selection_v1 import tick_latest_key, gaps_key, backfill_status_key
    from utils.hermes_feed_health_v1 import feed_health_key
    rc = _Redis(present=())
    obs.observe_inactive_publication(inactive_instruments=["EUR_USD"], redis_client=rc, indicator_timeframes=("H1",))
    expected = {tick_latest_key("EUR_USD"), gaps_key("EUR_USD"), backfill_status_key("EUR_USD"),
                feed_health_key("EUR_USD"), "hermes:indicators:EUR_USD:H1:v1"}
    assert set(rc.calls) == expected                                   # exact keys, no wildcard/alias
