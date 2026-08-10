"""Readiness live OANDA stream provider-wiring correction.
WO-HELM-HERMES-READINESS-LIVE-OANDA-STREAM-PROVIDER-WIRING-CORRECTION-0001.

Defect: /health reports the single OANDA stream FLOWING (from the watchdog authority), but /readiness read the base
pricing adapter's own AdapterHealth.state — which stays DISCONNECTED while the runtime streams via adapter.stream(),
and whose last_tick_at is refreshed by HEARTBEATS — so /readiness reported active_stream_count=0,
stream_health=DISCONNECTED, last_xau_tick_utc=None and RDY-STREAM-COUNT-NOT-ONE even during genuine sustained flow.

These are behavioural (not source-string) infra-free tests. They FIRST reproduce the exact contradiction (same
authoritative flow -> old base-adapter wiring reads 0 active, new watchdog projection reads 1 active), then prove the
corrected provider observes the authoritative runtime state while PRESERVING every fail-closed guard: market-closed /
disconnected / stale-flow / heartbeat-only stay not-active, a rogue second stream still trips COUNT_NOT_ONE, and the
real GET /readiness ASGI route (the router main.py includes) is exercised end to end.
"""
from datetime import datetime, timezone, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import utils.hermes_readiness_observers_v1 as obs
import utils.hermes_readiness_surface_v1 as rs
from utils.hermes_readiness_router_v1 import router, readiness_sources

UTC = timezone.utc
NOW = datetime(2026, 8, 10, 7, 30, tzinfo=UTC)


# --------------------------------------------------------------------- watchdog-snapshot + adapter fakes
def wd_snapshot(*, stream_state="FLOWING", xau_tick=NOW, agg_tick="__same__", health="GREEN", instruments=True):
    """Build a watchdog get_health_snapshot()-shaped dict (the authoritative source /health reports)."""
    agg = xau_tick if agg_tick == "__same__" else agg_tick
    instr = {}
    if instruments and xau_tick is not None:
        instr = {"XAU_USD": {"health_state": "GREEN", "reason_code": None,
                             "last_tick_utc": xau_tick.isoformat(), "last_m1_persisted_utc": None}}
    return {
        "health_state": health,
        "stream_state": stream_state,
        "last_tick_utc": agg.isoformat() if agg is not None else None,
        "instruments": instr,
    }


class _Task:
    def __init__(self, done=False): self._done = done
    def done(self): return self._done


class _BaseAdapterLikeStream:
    """Models the DEFECT source: the base pricing adapter reports .health.state=disconnected while the runtime streams
    via adapter.stream(), yet its last_tick_at is refreshed by HEARTBEATS (so it looks 'fresh'). This is exactly the
    live shape that made /readiness read 0 active during genuine flow."""
    def __init__(self, state, last_tick_at):
        self.health = type("H", (), {"state": type("S", (), {"value": state})(), "last_tick_at": last_tick_at})()


def _observe(snapshot, *, task_done=False, now=NOW):
    proj = obs.project_watchdog_stream(snapshot)
    streams = [] if proj is None else [(proj, _Task(task_done))]
    return obs.observe_stream(streams=streams, now=now)


# ===================================================================== A. projection mapping (unit)
@pytest.mark.parametrize("ss,expected", [
    ("FLOWING", "connected"), ("PARTIAL_FLOWING", "connected"),
    ("CONNECTED_UNPROVEN", "reconnecting"), ("RECOVERING", "reconnecting"),
    ("DISCONNECTED", "disconnected"), ("STALE", "disconnected"),
])
def test_projection_maps_watchdog_state_to_lifecycle(ss, expected):
    proj = obs.project_watchdog_stream(wd_snapshot(stream_state=ss))
    assert proj is not None and proj.health.state == expected
    assert proj.health.last_tick_at == NOW           # authoritative last real tick carried through


@pytest.mark.parametrize("snap", [None, {}, {"stream_state": "TELEPORTING"}, {"stream_state": ""}])
def test_projection_indeterminate_is_none_failclosed(snap):
    assert obs.project_watchdog_stream(snap) is None


def test_projection_holds_no_connection_and_no_io():
    proj = obs.project_watchdog_stream(wd_snapshot())
    # read-only projection: no connect/stream/subscribe surface an observer could drive
    for forbidden in ("connect", "disconnect", "stream", "subscribe"):
        assert not hasattr(proj, forbidden)


# ===================================================================== B. the EXACT contradiction (§19)
def test_reproduce_contradiction_base_adapter_reads_zero_projection_reads_one():
    """Same authoritative live flow. OLD wiring (base adapter: state=disconnected, heartbeat-fresh last_tick) reads
    0 active / DISCONNECTED — the defect. NEW wiring (watchdog projection of FLOWING+fresh tick) reads 1 active."""
    # --- OLD provider wiring: the base adapter as /readiness used to consume it ---
    old = obs.observe_stream(streams=[(_BaseAdapterLikeStream("disconnected", NOW), _Task(False))], now=NOW)
    assert old["active_count"] == 0 and old["health"] == "DISCONNECTED"      # <- reproduced contradiction

    # --- NEW provider wiring: the authoritative watchdog projection for the SAME flow ---
    new = _observe(wd_snapshot(stream_state="FLOWING", xau_tick=NOW))
    assert new["active_count"] == 1 and new["health"] == "ACTIVE"
    assert new["configured_count"] == 1 and new["unknown"] is False


# ===================================================================== C. observe_stream over the projection
def test_market_open_flowing_is_active_one():                                # §20
    r = _observe(wd_snapshot(stream_state="FLOWING", xau_tick=NOW))
    assert r == {"configured_count": 1, "active_count": 1, "count": 1,
                 "health": "ACTIVE", "last_heartbeat_utc": obs._iso(NOW), "unknown": False}


def test_market_closed_weekend_stale_is_not_active():                        # §15 / §21
    # weekend: no real ticks; watchdog stays STALE (heartbeats do NOT promote to FLOWING). Not active.
    stale = NOW - timedelta(minutes=20)
    r = _observe(wd_snapshot(stream_state="STALE", xau_tick=stale))
    assert r["active_count"] == 0 and r["health"] == "DISCONNECTED"


def test_disconnected_is_not_active():                                       # §22
    r = _observe(wd_snapshot(stream_state="DISCONNECTED", xau_tick=None, agg_tick=None))
    assert r["active_count"] == 0 and r["health"] == "DISCONNECTED"


def test_stale_flow_recent_promotion_but_old_tick_is_not_active():           # §24
    # authoritative last real tick older than the freshness window -> the unchanged freshness gate denies active,
    # even though the mapped lifecycle is 'connected'. A stale FLOWING can never read ACTIVE.
    old_tick = NOW - timedelta(seconds=120)
    r = _observe(wd_snapshot(stream_state="FLOWING", xau_tick=old_tick))
    assert r["active_count"] == 0 and r["health"] == "STALE"


def test_reconnecting_is_not_active():
    r = _observe(wd_snapshot(stream_state="RECOVERING", xau_tick=NOW))
    assert r["active_count"] == 0 and r["health"] == "STARTING"


def test_heartbeat_only_does_not_promote_to_active():                        # §25
    """The distinction that exposed the defect: an advancing heartbeat with NO fresh real tick must not read active.
    In the authority, heartbeats never promote stream_state to FLOWING nor advance last_tick_utc -> STALE -> not active."""
    hb_now = NOW                     # heartbeat is 'current'...
    stale_tick = NOW - timedelta(minutes=10)   # ...but the last REAL tick is old and stream_state is STALE
    r = _observe(wd_snapshot(stream_state="STALE", xau_tick=stale_tick, agg_tick=stale_tick))
    assert r["active_count"] == 0                # not promoted from a mere heartbeat


def test_no_authoritative_state_supplies_no_slot_failclosed():
    # project returns None -> provider supplies no slot -> observe_stream sees 0 configured -> not one active
    r = obs.observe_stream(streams=[], now=NOW)
    assert r["active_count"] == 0 and r["count"] == 0


def test_rogue_second_active_stream_still_trips_count_not_one():             # §23
    proj = obs.project_watchdog_stream(wd_snapshot(stream_state="FLOWING", xau_tick=NOW))
    r = obs.observe_stream(streams=[(proj, _Task(False)), (proj, _Task(False))], now=NOW)
    assert r["active_count"] == 2 and r["health"] == "MULTIPLE"


def test_stopped_task_is_not_active_even_when_flowing():
    r = _observe(wd_snapshot(stream_state="FLOWING", xau_tick=NOW), task_done=True)
    assert r["active_count"] == 0 and r["health"] == "STOPPED"


# ===================================================================== D. authoritative last XAU tick (§14)
def test_authoritative_last_xau_tick_from_per_instrument_truth():
    assert obs.authoritative_last_instrument_tick_utc(wd_snapshot(xau_tick=NOW), "XAU_USD") == NOW


def test_authoritative_last_xau_tick_none_when_absent():
    assert obs.authoritative_last_instrument_tick_utc(wd_snapshot(instruments=False), "XAU_USD") is None
    assert obs.authoritative_last_instrument_tick_utc({}, "XAU_USD") is None
    assert obs.authoritative_last_instrument_tick_utc(wd_snapshot(), None) is None


# ===================================================================== E. health/readiness consistency (§26)
@pytest.mark.parametrize("ss,flowing", [("FLOWING", True), ("PARTIAL_FLOWING", True),
                                        ("STALE", False), ("DISCONNECTED", False)])
def test_health_and_readiness_agree_on_stream_flowing_fact(ss, flowing):
    snap = wd_snapshot(stream_state=ss, xau_tick=NOW)
    health_says_flowing = str(snap["stream_state"]).upper() in obs._STREAM_FLOWING_STATES   # what /health reports
    readiness_active = _observe(snap)["active_count"] == 1                                   # what /readiness derives
    assert health_says_flowing == flowing
    # they must not contradict each other about whether the single stream is actually flowing
    assert readiness_active == health_says_flowing


# ===================================================================== F. real GET /readiness route (§27/§28/§20)
class _RouteSources:
    """Minimal readiness-sources for the REAL router — everything GREEN except the stream/tick under test."""
    def __init__(self, *, snapshot, last_xau, task_done=False):
        self._snap = snapshot
        self._last_xau = last_xau
        self._task_done = task_done

    def now(self): return NOW
    def build_identity(self):
        return {"source_sha": "cdbaabd0cc3a069da8357bd8a2183a5261e3b99c",
                "image_ref": "hermes-signal:prod-cdbaabd0", "build_utc": "2026-08-10T06:00:00Z",
                "build_identity_valid": True, "build_identity_reasons": []}
    def load_registry(self):
        reg = {"loaded": True, "rows": 14, "active": ["XAU_USD"], "not_enabled": 13,
               "invalid_active": 0, "malformed_capability": 0}
        return reg, [], ()
    def load_master_mode_pilot(self): return (False, "DISABLED", {"XAU_USD"})
    def load_calendar(self):
        amb = {"validation_status": "ASSUMPTION_REQUIRES_PROVIDER", "production_approved": False,
               "fault_code": "SCHEDULE_AMBIGUOUS_FAIL_CLOSED"}
        return {"index_cash": amb, "energy": amb}
    def core_snapshot(self): return ("GREEN", True, True)
    def stream_handles(self):
        proj = obs.project_watchdog_stream(self._snap)
        return [] if proj is None else [(proj, _Task(self._task_done))]
    def last_xau_tick(self): return self._last_xau
    def order_handles(self):
        return (["/health", "/readiness", "/metrics"], set(), {"CONSUMER_LIVE": "false",
                "HERMES_BACKFILL_EXECUTION_ENABLED": "false"})
    def redis_client(self):
        class _R:
            def exists(self, k): return 0
            def __getattr__(self, n): raise AssertionError(f"non-read redis op {n}")
        return _R()
    def seven_new_count(self): return 0


def _client(sources):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[readiness_sources] = lambda: sources
    return TestClient(app)


def test_route_market_open_flowing_is_green_active_one_with_tick():          # §20 / §27
    r = _client(_RouteSources(snapshot=wd_snapshot(stream_state="FLOWING", xau_tick=NOW), last_xau=NOW)).get("/readiness")
    assert r.status_code == 200
    b = r.json()
    assert b["core"]["stream_count"] == 1 and b["core"]["active_stream_count"] == 1
    assert b["core"]["stream_health"] == "ACTIVE"
    assert b["timestamps"]["last_xau_tick_utc"] == obs._iso(NOW)
    assert "RDY-STREAM-COUNT-NOT-ONE" not in b["readiness"]["fault_codes"]
    assert b["readiness"]["overall"] == "GREEN"


def test_route_market_closed_stays_red_not_falsely_green():                  # §21 / §15 preserved through the route
    stale = NOW - timedelta(minutes=20)
    r = _client(_RouteSources(snapshot=wd_snapshot(stream_state="STALE", xau_tick=stale), last_xau=None)).get("/readiness")
    assert r.status_code == 503
    b = r.json()
    assert b["core"]["active_stream_count"] == 0
    assert "RDY-STREAM-COUNT-NOT-ONE" in b["readiness"]["fault_codes"]
    assert b["readiness"]["overall"] == "RED"


def test_route_no_authoritative_state_fails_closed_red():                    # §28 route negative
    r = _client(_RouteSources(snapshot={}, last_xau=None)).get("/readiness")
    assert r.status_code == 503
    b = r.json()
    assert b["core"]["active_stream_count"] == 0
    assert "RDY-STREAM-COUNT-NOT-ONE" in b["readiness"]["fault_codes"]
