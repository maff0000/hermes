"""Route-level HTTP/ASGI proof of GET /readiness.
WO-...-PROVENANCE-SCOPE-READINESS-...-0001 continuation.

Exercises the REAL FastAPI route + dependency injection + serialisation from utils/hermes_readiness_router_v1.py —
the same router object main.py includes — via Starlette's TestClient. It does NOT call build_readiness_report()
directly: assemble() runs the real observers on injected fakes inside the route, so the three formerly hard-coded
inputs (stream count, order path, inactive publication) and their faults are proven through the HTTP layer. No
production SQL / Redis / OANDA is touched.
"""
import json
import pathlib
from datetime import datetime, timezone, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from utils.hermes_readiness_router_v1 import router, readiness_sources, assemble
import utils.hermes_readiness_surface_v1 as rs
from utils.hermes_advanced_v1_selection_v1 import gaps_key

UTC = timezone.utc
NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
GOOD_SHA = "cdbaabd0cc3a069da8357bd8a2183a5261e3b99c"
GOOD_BI = {"source_sha": GOOD_SHA, "image_ref": "hermes-signal:prod-cdbaabd0", "build_utc": "2026-08-06T09:11:15Z",
           "build_identity_valid": True, "build_identity_reasons": []}
GOOD_REG = {"loaded": True, "rows": 14, "active": ["XAU_USD"], "not_enabled": 13,
            "invalid_active": 0, "malformed_capability": 0}
CAL = {"index_cash": {"validation_status": "ASSUMPTION_REQUIRES_PROVIDER", "production_approved": False, "fault_code": "SCHEDULE_AMBIGUOUS_FAIL_CLOSED"},
       "energy": {"validation_status": "ASSUMPTION_REQUIRES_PROVIDER", "production_approved": False, "fault_code": "SCHEDULE_AMBIGUOUS_FAIL_CLOSED"}}
INACTIVE13 = ["XAG_USD", "EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY", "SPX500_USD", "WTICO_USD",
              "USD_CAD", "USD_CHF", "NZD_USD", "EUR_GBP", "EUR_JPY", "GBP_JPY"]
BASE_ENV = {"CONSUMER_LIVE": "false", "HERMES_BACKFILL_EXECUTION_ENABLED": "false"}
ROUTES = ["/health", "/ready", "/readiness", "/metrics", "/prices", "/status", "/buildinfo"]


# ------------------------------------------------------------------ fakes (real observers run on these)
class _St:
    def __init__(self, v): self.value = v


class _Adapter:
    def __init__(self, state, last):
        self.health = type("H", (), {"state": _St(state), "last_tick_at": last})()
    def connect(self, *a, **k): raise AssertionError("route must not connect a stream")


class _RaisingAdapter:
    @property
    def health(self): raise RuntimeError("stream state unavailable")


class _Task:
    def __init__(self, done): self._done = done
    def done(self): return self._done


class _Redis:
    def __init__(self, present=(), record=None): self.present = set(present); self.record = record
    def exists(self, k):
        if self.record is not None:
            self.record.append(("exists", k))
        return 1 if k in self.present else 0
    def __getattr__(self, name):
        raise AssertionError(f"route touched a non-read Redis op: {name}")


class _Boom:
    def __iter__(self): raise RuntimeError("route table unavailable")


class FakeSources:
    def __init__(self, **kw):
        self._now = kw.get("now", lambda: NOW)
        self._bi = kw.get("build_identity", GOOD_BI)
        self._registry = kw.get("registry", (GOOD_REG, list(INACTIVE13), ("H1", "H4")))
        self._raise_registry = kw.get("raise_registry", False)
        self._mmp = kw.get("mmp", (False, "DISABLED", {"XAU_USD"}))
        self._cal = kw.get("calendar", CAL)
        self._core = kw.get("core", ("GREEN", True, True))
        self._streams = kw.get("streams", [(_Adapter("connected", NOW), _Task(False))])
        self._order = kw.get("order", (list(ROUTES), set(), dict(BASE_ENV)))
        self._redis = kw.get("redis", _Redis())
        self._seven = kw.get("seven", 0)
        self._last_xau = kw.get("last_xau", None)

    def now(self): return self._now()
    def last_xau_tick(self): return self._last_xau
    def build_identity(self): return self._bi
    def load_registry(self):
        if self._raise_registry:
            raise RuntimeError("registry db down")
        return self._registry
    def load_master_mode_pilot(self): return self._mmp
    def load_calendar(self): return self._cal
    def core_snapshot(self): return self._core
    def stream_handles(self): return self._streams
    def order_handles(self): return self._order
    def redis_client(self): return self._redis
    def seven_new_count(self): return self._seven


def make_client(sources):
    app = FastAPI()
    app.include_router(router)
    if sources is not None:
        app.dependency_overrides[readiness_sources] = lambda: sources
    return TestClient(app)


# ------------------------------------------------------------------ GET authorised dark state
def test_get_readiness_authorised_dark_state():
    r = make_client(FakeSources()).get("/readiness")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["identity"]["readiness_contract_version"] == "v1"
    assert body["identity"]["source_sha"] == GOOD_SHA
    assert body["timestamps"]["evaluated_utc"].startswith("2026-08-06")
    assert body["registry"]["row_count"] == 14 and body["registry"]["not_enabled_count"] == 13
    assert body["pilot"]["xau_pilot_state"] == "ACTIVE_AUTHORISED"
    assert body["expansion"]["expansion_master_enabled"] is False and body["expansion"]["publisher_mode"] == "DISABLED"
    assert body["expansion"]["expansion_aggregate_state"] == "DARK_MASTER_DISABLED"
    assert body["calendar"]["SPX500_USD"] == "BLOCKED_CALENDAR" and body["calendar"]["WTICO_USD"] == "BLOCKED_CALENDAR"
    assert body["core"]["stream_count"] == 1 and body["core"]["stream_health"] == "ACTIVE"
    assert body["boundaries"]["order_path_state"] == "ABSENT"
    assert body["expansion"]["inactive_row_published_count"] == 0
    assert body["boundaries"]["consumer_state"] == "OFF" and body["boundaries"]["backfill_execution_state"] == "OFF"
    assert body["readiness"]["overall"] == "GREEN" and body["readiness"]["fault_codes"] == []
    assert body["readiness"]["expansion_readiness"] == "NOT_READY_DARK"
    assert body["readiness"]["trading_execution_readiness"] == "ABSENT"


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_unsupported_methods_405(method):
    r = getattr(make_client(FakeSources()), method)("/readiness")
    assert r.status_code == 405


def test_head_is_not_supported_405():
    # explicit contract: only GET is registered; HEAD is not auto-added -> 405 (documented in README)
    r = make_client(FakeSources()).head("/readiness")
    assert r.status_code == 405


# ------------------------------------------------------------------ fault paths THROUGH the route
FAULT_CASES = {
    "stream_zero": (dict(streams=[(_Adapter("connected", NOW), _Task(True))]), rs.F_STREAM_COUNT),
    "stream_two": (dict(streams=[(_Adapter("connected", NOW), _Task(False)), (_Adapter("connected", NOW), _Task(False))]), rs.F_STREAM_COUNT),
    "stream_unknown": (dict(streams=[(_RaisingAdapter(), _Task(False))]), rs.F_STREAM_UNKNOWN),
    "order_present": (dict(order=(ROUTES + ["/orders"], set(), dict(BASE_ENV))), rs.F_ORDER_PRESENT),
    "order_unknown": (dict(order=(_Boom(), set(), dict(BASE_ENV))), rs.F_ORDER_UNKNOWN),
    "inactive_published": (dict(redis=_Redis(present=(gaps_key("EUR_USD"),))), rs.F_INACTIVE_PUBLISHED),
    "inactive_unobserved": (dict(redis=None), rs.F_INACTIVE_UNOBSERVED),
    "source_malformed": (dict(build_identity={**GOOD_BI, "source_sha": "abc123"}), rs.F_SOURCE_MALFORMED),
    "source_missing": (dict(build_identity={**GOOD_BI, "source_sha": "UNKNOWN_SOURCE_SHA"}), rs.F_SOURCE_MISSING),
    "registry_load": (dict(raise_registry=True), rs.F_REGISTRY_LOAD),
    "master_true": (dict(mmp=(True, "DISABLED", {"XAU_USD"})), rs.F_MASTER_TRUE),
    "mode_active": (dict(mmp=(False, "ACTIVE", {"XAU_USD"})), rs.F_MODE_NOT_DISABLED),
    "mode_shadow": (dict(mmp=(False, "SHADOW", {"XAU_USD"})), rs.F_MODE_NOT_DISABLED),
    "consumer_on": (dict(order=(ROUTES, set(), {**BASE_ENV, "CONSUMER_LIVE": "true"})), rs.F_CONSUMER_ON),
    "backfill_on": (dict(order=(ROUTES, set(), {**BASE_ENV, "HERMES_BACKFILL_EXECUTION_ENABLED": "true"})), rs.F_BACKFILL_ON),
    "seven_new": (dict(seven=1), rs.F_SEVEN_NEW_PUBLISHED),
}


@pytest.mark.parametrize("name", list(FAULT_CASES))
def test_fault_path_is_503_with_exact_fault(name):
    over, fault = FAULT_CASES[name]
    r = make_client(FakeSources(**over)).get("/readiness")
    assert r.status_code == 503, name
    body = r.json()
    assert body["readiness"]["overall"] == "RED", name
    assert fault in body["readiness"]["fault_codes"], (name, body["readiness"]["fault_codes"])


def test_three_formerly_dead_faults_reachable_via_http():
    """The three inputs R2D2 flagged as hard-coded/omitted now emit their faults through the actual HTTP route."""
    for over, fault in (FAULT_CASES["stream_zero"], FAULT_CASES["order_present"], FAULT_CASES["inactive_published"]):
        body = make_client(FakeSources(**over)).get("/readiness").json()
        assert fault in body["readiness"]["fault_codes"]


# ------------------------------------------------------------------ status contract
def test_amber_core_is_200_not_503():
    r = make_client(FakeSources(core=("AMBER", True, True))).get("/readiness")
    assert r.status_code == 200 and r.json()["readiness"]["overall"] == "AMBER"


def test_unwired_provider_is_503_not_500():
    # no dependency override -> readiness_sources returns None -> fail-closed contract, never a crash
    r = make_client(None).get("/readiness")
    assert r.status_code == 503 and "RDY-PROVIDER-UNWIRED" in r.json()["readiness"]["fault_codes"]


def test_evaluation_error_is_bounded_503_no_traceback():
    def _boom(): raise RuntimeError("clock exploded")
    r = make_client(FakeSources(now=_boom)).get("/readiness")
    assert r.status_code == 503
    body = r.json()
    assert body["readiness"]["fault_codes"] == ["RDY-EVALUATION-ERROR"]
    assert "Traceback" not in json.dumps(body) and "clock exploded" not in json.dumps(body)


# ------------------------------------------------------------------ read-only + no-secret + performance
def test_route_is_read_only_on_redis():
    rec = []
    # _Redis raises on any non-exists op; a clean GET proves only exists() is used
    r = make_client(FakeSources(redis=_Redis(record=rec))).get("/readiness")
    assert r.status_code == 200
    assert rec and all(op == "exists" for op, _ in rec)          # only bounded exact-key reads


def test_route_bounded_redis_checks():
    rec = []
    make_client(FakeSources(redis=_Redis(record=rec))).get("/readiness").json()
    # 13 inactive x (4 single-key families + 2 timeframes) + 0 seven-new (fake returns count directly) = <= 78
    assert len(rec) <= len(INACTIVE13) * (4 + 2)


def test_no_secret_leakage_in_http_body():
    body = make_client(FakeSources()).get("/readiness").text.lower()
    for tok in ("password", "secret", "token", "credential", "dsn", "db_password"):
        assert tok not in body


# ------------------------------------------------------------------ route equivalence
def test_assemble_is_the_single_shared_entrypoint():
    # the route uses assemble(); calling assemble() directly yields the same GREEN body the HTTP route returns
    direct = assemble(FakeSources())
    via_http = make_client(FakeSources()).get("/readiness").json()
    assert direct["readiness"]["overall"] == via_http["readiness"]["overall"] == "GREEN"
    assert direct["identity"]["source_sha"] == via_http["identity"]["source_sha"]


def test_production_main_includes_this_router_not_an_inline_handler():
    """Route equivalence (WO §17): production main.py includes THIS router object and binds the sources; it does not
    re-define /readiness inline. (Source assertion — main.py cannot be imported without full boot.)"""
    main_src = (pathlib.Path(__file__).resolve().parents[1] / "main.py").read_text()
    assert "app.include_router(_readiness_router.router)" in main_src
    assert "app.dependency_overrides[_readiness_router.readiness_sources]" in main_src
    assert '@app.get("/readiness")' not in main_src          # the inline handler is gone; only the router defines it
    # and the router module is the SINGLE definition of the route
    router_src = (pathlib.Path(__file__).resolve().parents[1] / "utils/hermes_readiness_router_v1.py").read_text()
    assert router_src.count('@router.get("/readiness")') == 1
