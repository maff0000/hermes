"""HERMES feed-health runtime publisher wiring — PR #63 supervisor-compatible, disabled/dark by default.
WO-HELM-HERMES-FEED-HEALTH-RUNTIME-PUBLISHER-WIRING-0001. CODE-ONLY, in-memory fake Redis, no real I/O.

Reuses the EXISTING PR #66 feed-health contract/gates/key verbatim (hermes:feed_health:XAU_USD:v1; gates
HERMES_FEED_HEALTH_PUBLISH_ENABLED/AUTHORISED/INSTRUMENTS). Wires the runtime step + supervisor conditional only;
feed-health stays DARK until a separate activate WO.
"""
import json

import pytest

import utils.hermes_publisher_runtime_v1 as rt
import utils.hermes_runtime_publisher_steps_v1 as steps
import utils.hermes_feed_health_v1 as fh
import utils.hermes_instrument_catalog_v1 as ic

_FH_ENABLED = fh.ENABLED_ENV
_FH_AUTHORISED = fh.AUTHORISED_ENV
_FH_INSTRUMENTS = fh.INSTRUMENTS_ENV


class FakeRedis:
    """Minimal in-memory Redis: get/set/exists only. Records writes for single-writer assertions."""
    def __init__(self, seed=None):
        self.store = dict(seed or {})
        self.sets = []

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, ex=None):
        assert isinstance(v, (str, bytes))
        self.store[k] = v
        self.sets.append((k, v, ex))
        return True

    def exists(self, k):
        return 1 if k in self.store else 0


class _BoomClient:
    def __getattr__(self, name):
        raise AssertionError(f"redis client touched ({name}) while feed-health gate disabled")


def _enable_catalog(monkeypatch):
    monkeypatch.setenv(ic.ENABLED_ENV, "true")
    monkeypatch.setenv(ic.AUTHORISED_ENV, "true")
    monkeypatch.setenv(ic.INSTRUMENTS_ENV, "XAU_USD")


def _enable_fh(monkeypatch, instruments="XAU_USD"):
    monkeypatch.setenv(_FH_ENABLED, "true")
    monkeypatch.setenv(_FH_AUTHORISED, "true")
    if instruments is not None:
        monkeypatch.setenv(_FH_INSTRUMENTS, instruments)
    else:
        monkeypatch.delenv(_FH_INSTRUMENTS, raising=False)


def _disable_fh(monkeypatch):
    for e in (_FH_ENABLED, _FH_AUTHORISED, _FH_INSTRUMENTS):
        monkeypatch.delenv(e, raising=False)


def _names():
    return [n for (n, _s, _i) in rt.default_runner_specs()]


# ================================ 1. default inert behaviour ================================
def test_no_feed_health_runner_when_gates_absent(monkeypatch):
    _disable_fh(monkeypatch)
    assert "feed_health" not in _names()


def test_disabled_step_no_io_no_client_touch(monkeypatch):
    _disable_fh(monkeypatch)
    assert steps.feed_health_step(_BoomClient()) == {"published": 0}   # gate-first, no client touch


def test_disabled_step_no_write(monkeypatch):
    _disable_fh(monkeypatch)
    fake = FakeRedis()
    steps.feed_health_step(fake)
    assert fake.sets == [] and "hermes:feed_health:XAU_USD:v1" not in fake.store


def test_default_runner_count_stays_five_with_catalog_only(monkeypatch):
    _enable_catalog(monkeypatch)
    _disable_fh(monkeypatch)
    assert _names() == ["control_plane", "indicators", "candle_features", "sessions_levels", "instrument_catalog"]
    assert "feed_health" not in _names()


def test_bare_default_is_four_no_catalog_no_feed_health(monkeypatch):
    for e in (ic.ENABLED_ENV, ic.AUTHORISED_ENV, ic.INSTRUMENTS_ENV):
        monkeypatch.delenv(e, raising=False)
    _disable_fh(monkeypatch)
    assert _names() == ["control_plane", "indicators", "candle_features", "sessions_levels"]


# ================================ 2. gate / fail-closed behaviour ================================
def test_enabled_without_authorised_fails_closed(monkeypatch):
    monkeypatch.setenv(_FH_ENABLED, "true")
    monkeypatch.delenv(_FH_AUTHORISED, raising=False)
    with pytest.raises(SystemExit) as e:
        fh.build_feed_health_publisher_from_env()
    assert e.value.code == 101
    with pytest.raises(SystemExit) as e2:
        rt.default_runner_specs()
    assert e2.value.code == 101
    with pytest.raises(SystemExit) as e3:
        steps.feed_health_step(FakeRedis())
    assert e3.value.code == 101


def test_authorised_without_enabled_does_not_publish(monkeypatch):
    _disable_fh(monkeypatch)
    monkeypatch.setenv(_FH_AUTHORISED, "true")     # authorised but NOT enabled
    assert steps.feed_health_step(_BoomClient()) == {"published": 0}
    assert "feed_health" not in _names()


def test_missing_instruments_fails_closed(monkeypatch):
    _enable_fh(monkeypatch, instruments=None)
    with pytest.raises(ValueError) as e:
        rt.default_runner_specs()
    assert "GOV-HERMES-FH-020" in str(e.value)


def test_empty_instruments_fails_closed(monkeypatch):
    _enable_fh(monkeypatch, instruments="   ")
    with pytest.raises(ValueError) as e:
        fh.build_feed_health_publisher_from_env()
    assert "GOV-HERMES-FH-020" in str(e.value)


@pytest.mark.parametrize("bad", ["XAUUSD", "EUR_USD", "XAU_USD,EUR_USD", "XAU_USD,XAUUSD"])
def test_bad_instruments_rejected(monkeypatch, bad):
    _enable_fh(monkeypatch, instruments=bad)
    with pytest.raises(ValueError) as e:
        fh.build_feed_health_publisher_from_env()
    assert "GOV-HERMES-FH-021" in str(e.value)


# ================================ 3. enabled behaviour (cold) ================================
def test_runner_specs_become_exactly_six(monkeypatch):
    _enable_catalog(monkeypatch)
    _enable_fh(monkeypatch)
    specs = rt.default_runner_specs()
    assert [n for (n, _s, _i) in specs] == ["control_plane", "indicators", "candle_features",
                                            "sessions_levels", "instrument_catalog", "feed_health"]
    assert specs[-1][1] is steps.feed_health_step
    names = [n for (n, _s, _i) in specs]
    assert "quote" not in names and "tick" not in names and "market_map" not in names


def test_step_builds_key_and_governed_payload(monkeypatch):
    _enable_fh(monkeypatch)
    fake = FakeRedis()
    assert steps.feed_health_step(fake) == {"published": 1}
    assert list(fake.store.keys()) == ["hermes:feed_health:XAU_USD:v1"]
    k, v, ex = fake.sets[0]
    assert k == "hermes:feed_health:XAU_USD:v1" and ex == fh.TTL_SECONDS
    p = json.loads(v)
    assert p["instrument"] == "XAU_USD" and p["canonical_instrument"] == "XAU_USD"
    assert p["publisher"] == "HERMES" and p["contract"] == "feed_health:v1"
    assert p["generated_at_utc"].endswith("Z") and p["published_at_utc"].endswith("Z")
    assert p["status"] in fh.FEED_HEALTH_STATUSES
    assert fh.validate_feed_health_contract(p) is True


def test_step_reads_only_and_writes_only_feed_health(monkeypatch):
    # seed some governed candle-latest keys; the step GETs them but writes ONLY the feed-health key.
    _enable_fh(monkeypatch)
    seed = {f"hermes:candles:XAU_USD:{tf}:latest:v1": json.dumps(
        {"status": "OK", "data": {"timestamp_utc": "2026-07-04T13:00:00.000Z", "is_closed": True}})
        for tf in ("M1", "M5", "M15", "H1", "H4")}
    seed["hermes:publisher:heartbeat:v1"] = json.dumps({"status": "OK"})
    fake = FakeRedis(seed)
    steps.feed_health_step(fake)
    assert [k for (k, _v, _e) in fake.sets] == ["hermes:feed_health:XAU_USD:v1"]   # single write, own key only


# ================================ 4. catalog preservation ================================
def test_feed_health_dark_in_catalog_pure_path():
    p = ic.build_instrument_catalog_contract(instrument="XAU_USD",
                                             generated_at_utc=__import__("datetime").datetime(2026, 7, 4, 12, 0,
                                                 tzinfo=__import__("datetime").timezone.utc), source_name="OANDA")
    assert p["feed_health_contract"]["status"] == ic.SURFACE_CODE_PRESENT_DARK
    assert p["feed_health_contract"]["runtime_published"] is False and p["feed_health_contract"]["consumer_live"] is False
    assert "feed_health" in p["pending_runtime_deployment"]["dark_surfaces"]
    assert p["quote_contract"]["status"] == ic.SURFACE_CODE_PRESENT_DARK
    assert p["tick_contract"]["status"] == ic.SURFACE_CODE_PRESENT_DARK


def test_catalog_runtime_published_still_only_catalog(monkeypatch):
    _enable_catalog(monkeypatch)
    fake = FakeRedis()
    steps.instrument_catalog_step(fake)
    p = json.loads(fake.store["hermes:instrument_catalog:XAU_USD:v1"])
    assert p["instrument_catalog_contract"]["status"] == ic.SURFACE_RUNTIME_PUBLISHED
    assert p["runtime_published_surfaces"] == ["instrument_catalog"]
    assert "feed_health" in p["pending_runtime_deployment"]["dark_surfaces"]      # feed-health still dark
    assert p["feed_health_contract"]["runtime_published"] is False


def test_feed_health_guard_intact_cannot_be_marked_runtime_published():
    # PR #73 allow-list guard must remain: feed_health/quote/tick still fail GOV-HERMES-IC-030
    import datetime as D
    for bad in ("feed_health", "quote", "tick"):
        with pytest.raises(ValueError) as e:
            ic.build_instrument_catalog_contract(instrument="XAU_USD",
                generated_at_utc=D.datetime(2026, 7, 4, 12, 0, tzinfo=D.timezone.utc),
                source_name="HERMES", runtime_published_surfaces=[bad])
        assert "GOV-HERMES-IC-030" in str(e.value)


# ================================ 5. boundary regression ================================
def test_no_xauusd_and_d1_gated(monkeypatch):
    _enable_fh(monkeypatch)
    fake = FakeRedis()
    steps.feed_health_step(fake)
    p = json.loads(fake.store["hermes:feed_health:XAU_USD:v1"])
    # no :XAUUSD: anywhere; no duplicate hermes:tick:* introduced
    def strings(o):
        if isinstance(o, str):
            yield o
        elif isinstance(o, dict):
            for kk, vv in o.items():
                yield kk
                yield from strings(vv)
        elif isinstance(o, list):
            for vv in o:
                yield from strings(vv)
    assert all(":XAUUSD:" not in s for s in strings(p))
    assert all(s != "hermes:tick:XAU_USD:v1" for s in strings(p))
    # D1 GATED (never hard-coded ACTIVE) when no D1 latest present
    assert p["per_timeframe_health"]["D1"]["status"] == fh.STATUS_GATED


def test_no_module_level_redis_client():
    # steps + fh construct NO redis client (steps use the INJECTED client; fh uses the injected snapshot client).
    # rt (the supervisor) is intentionally excluded: it builds a client ONLY via the lazy, function-scoped
    # _default_redis_client() on the enabled path — never at import.
    for mod in (steps, fh):
        src = open(mod.__file__).read()
        assert "\nimport redis" not in src and "redis.Redis(" not in src and "\nfrom redis" not in src
