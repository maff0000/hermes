"""HERMES LIVE canonical tick emitter v1 — code-only, in-memory fake Redis, no real I/O.
WO-HELM-HERMES-LIVE-TICK-PUBLISHER-V1-BUILD-0001.

Type-B EMITTER (per-tick, stream loop) — NOT a supervisor runner. Dark/inert by default. Writes ONLY
hermes:ticks:XAU_USD:latest:v1 (EX=10) when LIVE gates enabled+authorised+scoped. Reuses tick_contract_v1.
Also proves the catalog tick keystone is gate-driven (no split-brain) and that feed_health/quote/instrument_catalog
remain regression-safe.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.tick_live_emitter_v1 as tle
import utils.tick_contract_v1 as tc
import utils.hermes_instrument_catalog_v1 as ic
import utils.hermes_runtime_publisher_steps_v1 as steps
import utils.hermes_publisher_runtime_v1 as rt

UTC = timezone.utc
_CANON_KEY = "hermes:ticks:XAU_USD:latest:v1"


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.sets = []

    def set(self, k, v, ex=None):
        assert isinstance(v, (str, bytes))
        self.store[k] = v
        self.sets.append((k, v, ex))
        return True

    def get(self, k):
        return self.store.get(k)

    def exists(self, k):
        return 1 if k in self.store else 0


class _BoomClient:
    def __getattr__(self, name):
        raise AssertionError(f"redis client touched ({name}) while tick emitter disabled")


class _Tick:
    """Duck-typed live SignalTick (instrument, bid, ask, source, received/source timestamps)."""
    def __init__(self, instrument="XAU_USD", bid=2650.10, ask=2650.30, source="oanda", received=None):
        self.instrument = instrument
        self.bid = bid
        self.ask = ask
        self.source = source
        self.source_timestamp = received or datetime.now(UTC)
        self.received_at = self.source_timestamp
        self.timestamp = self.source_timestamp


def _enable_tick(monkeypatch, instruments="XAU_USD"):
    monkeypatch.setenv(tle.ENABLED_ENV, "true")
    monkeypatch.setenv(tle.AUTHORISED_ENV, "true")
    if instruments is not None:
        monkeypatch.setenv(tle.INSTRUMENTS_ENV, instruments)
    else:
        monkeypatch.delenv(tle.INSTRUMENTS_ENV, raising=False)


def _disable_tick(monkeypatch):
    for e in (tle.ENABLED_ENV, tle.AUTHORISED_ENV, tle.INSTRUMENTS_ENV):
        monkeypatch.delenv(e, raising=False)


# ================================ 1. default inert / gates absent ================================
def test_disabled_by_default_no_emission(monkeypatch):
    _disable_tick(monkeypatch)
    em = tle.build_tick_live_emitter_from_env(redis_client=_BoomClient())
    assert em.enabled is False
    assert em.emit_tick_observed(_Tick())["emitted"] is False   # no client touch, no write


def test_gate_helper_false_when_absent(monkeypatch):
    _disable_tick(monkeypatch)
    assert tle.tick_live_gate_enabled() is False


# ================================ 2. gate / fail-closed ================================
def test_enabled_without_authorised_fails_loud(monkeypatch):
    monkeypatch.setenv(tle.ENABLED_ENV, "true")
    monkeypatch.delenv(tle.AUTHORISED_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        tle.build_tick_live_emitter_from_env(redis_client=FakeRedis())
    assert e.value.code == 101
    with pytest.raises(SystemExit) as e2:
        tle.tick_live_gate_enabled()
    assert e2.value.code == 101


def test_enabled_without_instrument_scope_fails_loud(monkeypatch):
    _enable_tick(monkeypatch, instruments=None)
    with pytest.raises(ValueError) as e:
        tle.build_tick_live_emitter_from_env(redis_client=FakeRedis())
    assert "GOV-HERMES-TICK-020" in str(e.value)


@pytest.mark.parametrize("bad", ["XAUUSD", "EUR_USD", "XAU_USD,EUR_USD", "XAU_USD,XAUUSD"])
def test_bad_instrument_scope_fails_loud(monkeypatch, bad):
    _enable_tick(monkeypatch, instruments=bad)
    with pytest.raises(ValueError) as e:
        tle.build_tick_live_emitter_from_env(redis_client=FakeRedis())
    assert "GOV-HERMES-TICK-021" in str(e.value)


# ================================ 3. enabled emitter + canonical key + EX ================================
def test_valid_gates_emitter_enabled_writes_canonical_key_ex10(monkeypatch):
    _enable_tick(monkeypatch)
    fake = FakeRedis()
    em = tle.build_tick_live_emitter_from_env(redis_client=fake)
    assert em.enabled is True
    res = em.emit_tick(_Tick(bid=2650.10, ask=2650.30), now=datetime.now(UTC))
    assert res["emitted"] is True
    assert list(fake.store.keys()) == [_CANON_KEY]              # ONLY the canonical key
    k, v, ex = fake.sets[0]
    assert k == _CANON_KEY and ex == tc.REDIS_EX_SECONDS == 10  # EX=10
    env = json.loads(v)
    assert env["key"] == _CANON_KEY and "XAUUSD" not in json.dumps(env)


def test_valid_envelope_from_live_tick(monkeypatch):
    _enable_tick(monkeypatch)
    fake = FakeRedis()
    em = tle.build_tick_live_emitter_from_env(redis_client=fake)
    em.emit_tick(_Tick(bid=2650.10, ask=2650.30), now=datetime.now(UTC))
    env = json.loads(fake.store[_CANON_KEY])
    assert env["service"] == "HERMES" and env["contract"] == "hermes.ticks.latest" and env["contract_version"] == "v1"
    assert env["provenance"]["publisher"] == "HERMES" and env["provenance"]["source"] == "oanda"
    assert env["data"]["instrument"] == "XAU_USD" and env["data"]["seq"] is None
    assert env["data"]["bid"] == 2650.10 and env["data"]["ask"] == 2650.30
    assert env["data"]["mid"] == 2650.20 and env["data"]["spread"] == round(2650.30 - 2650.10, 6)
    assert env["freshness_state"] in tc.FRESHNESS_STATES and env["status"] in tc.STATUSES
    assert env["generated_at_utc"].endswith("Z") and env["provenance"]["source_received_at_utc"].endswith("Z")
    assert tc.validate_tick_contract(env) is True


# ================================ 4. price validation / fail-loud ================================
def test_inverted_quote_ask_lt_bid_fails_loud(monkeypatch):
    _enable_tick(monkeypatch)
    em = tle.build_tick_live_emitter_from_env(redis_client=FakeRedis())
    with pytest.raises(ValueError) as e:
        em.emit_tick(_Tick(bid=2650.30, ask=2650.10))         # ask < bid
    assert "GOV-TICK-CONTRACT-003" in str(e.value)


def test_malformed_price_fails_loud(monkeypatch):
    _enable_tick(monkeypatch)
    em = tle.build_tick_live_emitter_from_env(redis_client=FakeRedis())
    with pytest.raises(Exception):
        em.emit_tick(_Tick(bid="not-a-number", ask=2650.30))


def test_observed_emit_never_raises_on_fault(monkeypatch):
    _enable_tick(monkeypatch)
    fake = FakeRedis()
    em = tle.build_tick_live_emitter_from_env(redis_client=fake)
    res = em.emit_tick_observed(_Tick(bid=2650.30, ask=2650.10))   # inverted -> fault, but observed never raises
    assert res["emitted"] is False and res["reason"] == "LIVE_TICK_EMIT_FAIL"
    assert _CANON_KEY not in fake.store                            # nothing written on fault


def test_stale_tick_is_honest_not_fake_green(monkeypatch):
    _enable_tick(monkeypatch)
    fake = FakeRedis()
    em = tle.build_tick_live_emitter_from_env(redis_client=fake)
    old = datetime.now(UTC) - timedelta(minutes=5)
    em.emit_tick(_Tick(received=old), now=datetime.now(UTC))
    env = json.loads(fake.store[_CANON_KEY])
    assert env["freshness_state"] == "STALE" and env["status"] == "WARN"   # honest stale, not fake FRESH


# ================================ 5. canonical-key guards ================================
def test_emitter_refuses_xauusd_and_shadow_keys():
    for bad in ("hermes:shadow:ticks:XAU_USD:latest:v1", "hermes:ticks:XAUUSD:latest:v1", "hermes:tick:XAU_USD:v1"):
        with pytest.raises(ValueError):
            tle._assert_canonical_live_key(bad)
    assert tle._assert_canonical_live_key(_CANON_KEY) is True


# ================================ 6. catalog keystone (gate-driven, no split-brain) ================================
def _enable_catalog(monkeypatch):
    monkeypatch.setenv(ic.ENABLED_ENV, "true")
    monkeypatch.setenv(ic.AUTHORISED_ENV, "true")
    monkeypatch.setenv(ic.INSTRUMENTS_ENV, "XAU_USD")


def test_catalog_tick_dark_when_gates_absent(monkeypatch):
    _enable_catalog(monkeypatch)
    _disable_tick(monkeypatch)
    fake = FakeRedis()
    steps.instrument_catalog_step(fake)
    p = json.loads(fake.store["hermes:instrument_catalog:XAU_USD:v1"])
    assert p["tick_contract"]["status"] == ic.SURFACE_CODE_PRESENT_DARK
    assert p["tick_contract"]["runtime_published"] is False
    assert "tick" in p["pending_runtime_deployment"]["dark_surfaces"]
    assert "tick" not in p["runtime_published_surfaces"]


def test_catalog_tick_runtime_published_when_gate_enabled_no_split_brain(monkeypatch):
    _enable_catalog(monkeypatch)
    _enable_tick(monkeypatch)
    fake = FakeRedis()
    steps.instrument_catalog_step(fake)
    p = json.loads(fake.store["hermes:instrument_catalog:XAU_USD:v1"])
    tk = p["tick_contract"]
    assert tk["status"] == ic.SURFACE_RUNTIME_PUBLISHED and tk["runtime_published"] is True and tk["consumer_live"] is False
    assert "tick" in p["runtime_published_surfaces"] and "tick" not in p["pending_runtime_deployment"]["dark_surfaces"]
    # split-brain equality: tick in runtime_published_surfaces == live tick emitter enabled == LIVE tick gate enabled
    tick_in_catalog = "tick" in p["runtime_published_surfaces"]
    tick_emitter_enabled = getattr(tle.build_tick_live_emitter_from_env(redis_client=FakeRedis()), "enabled", False)
    tick_gate = tle.tick_live_gate_enabled()
    assert tick_in_catalog == tick_emitter_enabled == tick_gate is True


def test_catalog_tick_consumer_live_always_false(monkeypatch):
    _enable_catalog(monkeypatch)
    _enable_tick(monkeypatch)
    fake = FakeRedis()
    steps.instrument_catalog_step(fake)
    p = json.loads(fake.store["hermes:instrument_catalog:XAU_USD:v1"])
    assert p["tick_contract"]["consumer_live"] is False


# ================================ 7. regression safety ================================
def test_default_runner_specs_unchanged_by_tick(monkeypatch):
    # tick is emitter-based, NOT a supervisor runner -> it must NEVER appear in default_runner_specs.
    _enable_catalog(monkeypatch)
    _enable_tick(monkeypatch)
    for e in ("HERMES_FEED_HEALTH_PUBLISH_ENABLED", "HERMES_QUOTE_PUBLISH_ENABLED"):
        monkeypatch.delenv(e, raising=False)
    names = [n for (n, _s, _i) in rt.default_runner_specs()]
    assert names == ["control_plane", "indicators", "candle_features", "sessions_levels", "instrument_catalog"]
    assert "tick" not in names


def test_quote_feed_health_instrument_catalog_regression_safe():
    import datetime as D
    # pure builder path: everything dark; the four permitted surfaces unchanged; validation passes
    p = ic.build_instrument_catalog_contract(instrument="XAU_USD",
        generated_at_utc=D.datetime(2026, 7, 7, 9, 0, tzinfo=UTC), source_name="OANDA")
    for c in ("feed_health_contract", "quote_contract", "tick_contract"):
        assert p[c]["status"] == ic.SURFACE_CODE_PRESENT_DARK and p[c]["runtime_published"] is False
    assert set(p["pending_runtime_deployment"]["dark_surfaces"]) == {"feed_health", "instrument_catalog", "quote", "tick"}
    assert ic.validate_instrument_catalog_contract(p) is True


def test_no_module_level_redis_client():
    for mod in (tle,):
        src = open(mod.__file__).read()
        assert "\nimport redis" not in src and "redis.Redis(" in src   # redis.Redis only inside the lazy factory
        # confirm the redis.Redis( call is function-scoped (inside _default_canonical_redis_client), not module-level
        assert "def _default_canonical_redis_client" in src
