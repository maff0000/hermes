"""HERMES instrument-catalog runtime publisher wiring — PR #63 supervisor-compatible, disabled/dark by default.
CODE-ONLY, in-memory fake Redis, no real I/O. WO-HELM-HERMES-INSTRUMENT-CATALOG-RUNTIME-PUBLISHER-WIRING-0001.
"""
import json

import pytest

import utils.hermes_publisher_runtime_v1 as rt
import utils.hermes_runtime_publisher_steps_v1 as steps
import utils.hermes_instrument_catalog_v1 as ic

_CAT_ENABLED = ic.ENABLED_ENV
_CAT_AUTHORISED = ic.AUTHORISED_ENV
_CAT_INSTRUMENTS = ic.INSTRUMENTS_ENV


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.sets = []

    def set(self, k, v, ex=None):
        assert isinstance(v, (str, bytes))
        self.store[k] = v
        self.sets.append((k, v, ex))
        return True


class _BoomClient:
    def __getattr__(self, name):
        raise AssertionError(f"redis client touched ({name}) while instrument-catalog gate disabled")


def _enable(monkeypatch):
    monkeypatch.setenv(_CAT_ENABLED, "true")
    monkeypatch.setenv(_CAT_AUTHORISED, "true")
    monkeypatch.setenv(_CAT_INSTRUMENTS, "XAU_USD")


def _disable(monkeypatch):
    for e in (_CAT_ENABLED, _CAT_AUTHORISED, _CAT_INSTRUMENTS):
        monkeypatch.delenv(e, raising=False)


# ============================ disabled default ============================
def test_step_disabled_default_noop_no_io(monkeypatch):
    _disable(monkeypatch)
    assert steps.instrument_catalog_step(_BoomClient()) == {"published": 0}   # gate-first, no client touch


def test_default_runner_specs_exactly_four_when_disabled(monkeypatch):
    _disable(monkeypatch)
    names = [n for (n, _s, _i) in rt.default_runner_specs()]
    assert names == ["control_plane", "indicators", "candle_features", "sessions_levels"]
    assert "instrument_catalog" not in names


def test_no_catalog_key_written_while_disabled(monkeypatch):
    _disable(monkeypatch)
    fake = FakeRedis()
    steps.instrument_catalog_step(fake)                                       # disabled -> returns before any write
    assert fake.sets == [] and "hermes:instrument_catalog:XAU_USD:v1" not in fake.store


# ============================ gates ============================
def test_enabled_without_authorised_fails_closed(monkeypatch):
    monkeypatch.setenv(_CAT_ENABLED, "true")
    monkeypatch.delenv(_CAT_AUTHORISED, raising=False)
    with pytest.raises(SystemExit) as e:
        ic.build_instrument_catalog_publisher_from_env()
    assert e.value.code == 101
    # and the supervisor spec builder fails closed the same way
    with pytest.raises(SystemExit) as e2:
        rt.default_runner_specs()
    assert e2.value.code == 101


def test_enabled_authorised_appends_fifth_runner(monkeypatch):
    _enable(monkeypatch)
    specs = rt.default_runner_specs()
    names = [n for (n, _s, _i) in specs]
    assert names == ["control_plane", "indicators", "candle_features", "sessions_levels", "instrument_catalog"]
    assert specs[-1][1] is steps.instrument_catalog_step


# ============================ payload / key ============================
def test_publish_key_and_governed_payload(monkeypatch):
    _enable(monkeypatch)
    fake = FakeRedis()
    assert steps.instrument_catalog_step(fake) == {"published": 1}
    assert list(fake.store.keys()) == ["hermes:instrument_catalog:XAU_USD:v1"]
    k, v, ex = fake.sets[0]
    assert k == "hermes:instrument_catalog:XAU_USD:v1" and ex == ic.TTL_SECONDS
    p = json.loads(v)
    assert p["instrument"] == "XAU_USD" and p["canonical_instrument"] == "XAU_USD"
    assert p["aliases"]["inbound"] == ["XAUUSD"]
    assert p["publisher"] == "HERMES" and p["contract"] == "instrument_catalog:v1"
    assert p["generated_at_utc"].endswith("Z") and p["published_at_utc"].endswith("Z")
    # NO :XAUUSD: in any published key/string
    assert all(":XAUUSD:" not in s for s in ic._iter_key_strings(p) if isinstance(s, str))
    # WO-...-SELF-SURFACE-RUNTIME-PUBLISHED-SEMANTICS: the step IS the runtime publication, so the catalog now
    # truthfully self-marks RUNTIME_PUBLISHED (NOT dark), consumer_live False (no cutover implied), and drops out
    # of dark_surfaces; the still-dark surfaces remain feed_health/quote/tick only.
    icc = p["instrument_catalog_contract"]
    assert icc["status"] == ic.SURFACE_RUNTIME_PUBLISHED
    assert icc["runtime_published"] is True and icc["consumer_live"] is False and icc["live"] is False
    assert p["surfaces"]["instrument_catalog"] == ic.SURFACE_RUNTIME_PUBLISHED
    assert p["runtime_published_surfaces"] == ["instrument_catalog"]
    prd = p["pending_runtime_deployment"]
    assert prd["runtime_live"] is False
    assert set(prd["dark_surfaces"]) == {"feed_health", "quote", "tick"}
    assert "instrument_catalog" not in prd["dark_surfaces"]
    assert prd["runtime_published_surfaces"] == ["instrument_catalog"]
    # feed_health/quote/tick remain dark + not runtime-published + not consumer-live
    for c in ("feed_health_contract", "quote_contract", "tick_contract"):
        assert p[c]["status"] == ic.SURFACE_CODE_PRESENT_DARK
        assert p[c]["runtime_published"] is False and p[c]["consumer_live"] is False and p[c]["live"] is False
    # D1 default PENDING (no liveness inferred from any D1 GREEN seal)
    assert p["candle_contracts"]["D1"]["latest_status"] == ic.SURFACE_PENDING_FIRST_DAILY_SEAL
    assert p["d1_policy"]["latest_status"] == ic.SURFACE_PENDING_FIRST_DAILY_SEAL
    # deterministic + governed (no ARES fields)
    assert p["deterministic_only"] is True
    assert ic.validate_instrument_catalog_contract(p) is True


def test_no_duplicate_tick_family_in_payload(monkeypatch):
    _enable(monkeypatch)
    fake = FakeRedis(); steps.instrument_catalog_step(fake)
    p = json.loads(fake.store["hermes:instrument_catalog:XAU_USD:v1"])
    assert all("hermes:tick:XAU_USD:v1" != s for s in ic._iter_key_strings(p))
    assert p["tick_contract"]["key"] == "hermes:ticks:XAU_USD:latest:v1"


# ============================ import safety ============================
def test_no_module_level_redis_client_or_import_time_io():
    # the steps module + catalog module construct NO redis client at import (steps use the INJECTED client;
    # the only DB access is a PRE-EXISTING lazy `import pymysql` INSIDE functions for governed config reads on the
    # enabled path — not module-import I/O). No top-level redis import; re-import is side-effect-free.
    for mod in (steps, ic):
        src = open(mod.__file__).read()
        assert "\nimport redis" not in src and "redis.Redis(" not in src and "\nfrom redis" not in src
    import importlib
    importlib.reload(ic)                                                     # side-effect-free re-import
