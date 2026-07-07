"""HERMES quote runtime publisher wiring — PR #63 supervisor-compatible, disabled/dark by default.
WO-HELM-HERMES-QUOTE-RUNTIME-PUBLISHER-WIRING-0001. CODE-ONLY, in-memory fake Redis, no real I/O.

Reuses the EXISTING PR #68 quote contract/gates/key verbatim (hermes:quote:XAU_USD:v1; gates
HERMES_QUOTE_PUBLISH_ENABLED/AUTHORISED/INSTRUMENTS). Governed source = the existing tick surface
hermes:ticks:XAU_USD:latest:v1 via reconcile_quote_from_tick_envelope. Wires the runtime step + supervisor
conditional only; quote stays DARK until a separate activate WO. Stale/market-closed is honest (never faked).
"""
import datetime
import json

import pytest

import utils.hermes_publisher_runtime_v1 as rt
import utils.hermes_runtime_publisher_steps_v1 as steps
import utils.hermes_quote_tick_contract_v1 as qt
import utils.hermes_feed_health_v1 as fh
import utils.hermes_instrument_catalog_v1 as ic

UTC = datetime.timezone.utc
_TICK_KEY = "hermes:ticks:XAU_USD:latest:v1"


class FakeRedis:
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
        raise AssertionError(f"redis client touched ({name}) while quote gate disabled")


def _fmt(dt):
    return qt._utc(dt)


def _tick_seed(bid=2650.10, ask=2650.30, received=None, extra=None):
    d = {"bid": bid, "ask": ask, "source": "OANDA"}
    if received is not None:
        d["received_at_utc"] = _fmt(received)
    if extra:
        d.update(extra)
    return {_TICK_KEY: json.dumps({"data": d})}


def _enable_catalog(monkeypatch):
    monkeypatch.setenv(ic.ENABLED_ENV, "true")
    monkeypatch.setenv(ic.AUTHORISED_ENV, "true")
    monkeypatch.setenv(ic.INSTRUMENTS_ENV, "XAU_USD")


def _enable_fh(monkeypatch):
    monkeypatch.setenv(fh.ENABLED_ENV, "true")
    monkeypatch.setenv(fh.AUTHORISED_ENV, "true")
    monkeypatch.setenv(fh.INSTRUMENTS_ENV, "XAU_USD")


def _enable_quote(monkeypatch, instruments="XAU_USD"):
    monkeypatch.setenv(qt.QUOTE_ENABLED_ENV, "true")
    monkeypatch.setenv(qt.QUOTE_AUTHORISED_ENV, "true")
    if instruments is not None:
        monkeypatch.setenv(qt.QUOTE_INSTRUMENTS_ENV, instruments)
    else:
        monkeypatch.delenv(qt.QUOTE_INSTRUMENTS_ENV, raising=False)


def _disable_quote(monkeypatch):
    for e in (qt.QUOTE_ENABLED_ENV, qt.QUOTE_AUTHORISED_ENV, qt.QUOTE_INSTRUMENTS_ENV):
        monkeypatch.delenv(e, raising=False)


def _names():
    return [n for (n, _s, _i) in rt.default_runner_specs()]


# ================================ 1. default inert behaviour ================================
def test_no_quote_runner_when_gates_absent(monkeypatch):
    _disable_quote(monkeypatch)
    assert "quote" not in _names()


def test_disabled_step_no_client_touch(monkeypatch):
    _disable_quote(monkeypatch)
    assert steps.quote_step(_BoomClient()) == {"published": 0}


def test_disabled_step_no_write(monkeypatch):
    _disable_quote(monkeypatch)
    fake = FakeRedis()
    steps.quote_step(fake)
    assert fake.sets == [] and "hermes:quote:XAU_USD:v1" not in fake.store


def test_default_count_stays_five_and_feed_health_dark(monkeypatch):
    _enable_catalog(monkeypatch)
    _disable_quote(monkeypatch)
    for e in (fh.ENABLED_ENV, fh.AUTHORISED_ENV, fh.INSTRUMENTS_ENV):
        monkeypatch.delenv(e, raising=False)
    names = _names()
    assert names == ["control_plane", "indicators", "candle_features", "sessions_levels", "instrument_catalog"]
    assert "quote" not in names and "feed_health" not in names


# ================================ 2. gate / fail-closed behaviour ================================
def test_enabled_without_authorised_fails_closed(monkeypatch):
    monkeypatch.setenv(qt.QUOTE_ENABLED_ENV, "true")
    monkeypatch.delenv(qt.QUOTE_AUTHORISED_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        qt.build_quote_publisher_from_env()
    assert e.value.code == 101
    with pytest.raises(SystemExit) as e2:
        rt.default_runner_specs()
    assert e2.value.code == 101
    with pytest.raises(SystemExit) as e3:
        steps.quote_step(FakeRedis())
    assert e3.value.code == 101


def test_authorised_without_enabled_does_not_publish(monkeypatch):
    _disable_quote(monkeypatch)
    monkeypatch.setenv(qt.QUOTE_AUTHORISED_ENV, "true")
    assert steps.quote_step(_BoomClient()) == {"published": 0}
    assert "quote" not in _names()


def test_missing_instruments_fails_closed(monkeypatch):
    _enable_quote(monkeypatch, instruments=None)
    with pytest.raises(ValueError) as e:
        rt.default_runner_specs()
    assert "GOV-HERMES-QT-020" in str(e.value)


def test_empty_instruments_fails_closed(monkeypatch):
    _enable_quote(monkeypatch, instruments="   ")
    with pytest.raises(ValueError) as e:
        qt.build_quote_publisher_from_env()
    assert "GOV-HERMES-QT-020" in str(e.value)


@pytest.mark.parametrize("bad", ["XAUUSD", "EUR_USD", "XAU_USD,EUR_USD", "XAU_USD,XAUUSD"])
def test_bad_instruments_rejected(monkeypatch, bad):
    _enable_quote(monkeypatch, instruments=bad)
    with pytest.raises(ValueError) as e:
        qt.build_quote_publisher_from_env()
    assert "GOV-HERMES-QT-021" in str(e.value)


# ================================ 3. enabled behaviour (cold) ================================
def test_runner_specs_six_catalog_plus_quote(monkeypatch):
    _enable_catalog(monkeypatch)
    _enable_quote(monkeypatch)
    for e in (fh.ENABLED_ENV, fh.AUTHORISED_ENV, fh.INSTRUMENTS_ENV):
        monkeypatch.delenv(e, raising=False)
    specs = rt.default_runner_specs()
    assert [n for (n, _s, _i) in specs] == ["control_plane", "indicators", "candle_features",
                                            "sessions_levels", "instrument_catalog", "quote"]
    assert specs[-1][1] is steps.quote_step


def test_runner_specs_seven_catalog_feedhealth_quote(monkeypatch):
    _enable_catalog(monkeypatch)
    _enable_fh(monkeypatch)
    _enable_quote(monkeypatch)
    names = [n for (n, _s, _i) in rt.default_runner_specs()]
    assert names == ["control_plane", "indicators", "candle_features", "sessions_levels",
                     "instrument_catalog", "feed_health", "quote"]
    assert "tick" not in names and "market_map" not in names


def test_step_builds_key_and_governed_payload(monkeypatch):
    _enable_quote(monkeypatch)
    fake = FakeRedis(_tick_seed(received=datetime.datetime.now(UTC)))
    assert steps.quote_step(fake) == {"published": 1}
    assert list(fake.store.keys()) == [_TICK_KEY, "hermes:quote:XAU_USD:v1"]   # read tick, wrote only quote
    k, v, ex = fake.sets[0]
    assert k == "hermes:quote:XAU_USD:v1" and ex == qt.QUOTE_TTL_SECONDS
    p = json.loads(v)
    assert p["instrument"] == "XAU_USD" and p["canonical_instrument"] == "XAU_USD"
    assert p["publisher"] == "HERMES" and p["contract"] == "quote:v1"
    assert p["generated_at_utc"].endswith("Z") and p["published_at_utc"].endswith("Z")
    assert p["status"] in qt.QUOTE_STATUSES
    assert qt.validate_quote_contract(p) is True


# ================================ 4. quote payload governance / honest stale ================================
def test_fresh_tick_gives_factual_prices_green(monkeypatch):
    _enable_quote(monkeypatch)
    fake = FakeRedis(_tick_seed(bid=2650.10, ask=2650.30, received=datetime.datetime.now(UTC)))
    steps.quote_step(fake)
    p = json.loads(fake.store["hermes:quote:XAU_USD:v1"])
    assert p["bid"] == 2650.10 and p["ask"] == 2650.30
    assert p["mid"] == 2650.20 and p["spread"] == round(2650.30 - 2650.10, 6)   # RECOMPUTED, not trusted
    assert p["status"] == qt.STATUS_GREEN and p["freshness"] == qt.FRESHNESS_FRESH


def test_absent_tick_is_honest_red_missing(monkeypatch):
    # market-closed / no tick source -> explicit RED_MISSING, null prices, UNAVAILABLE — NEVER a fake GREEN
    _enable_quote(monkeypatch)
    fake = FakeRedis()          # no tick key
    assert steps.quote_step(fake) == {"published": 1}
    p = json.loads(fake.store["hermes:quote:XAU_USD:v1"])
    assert p["bid"] is None and p["ask"] is None and p["mid"] is None
    assert p["status"] == qt.STATUS_RED_MISSING and p["freshness"] == qt.FRESHNESS_UNAVAILABLE


def test_stale_tick_is_honest_amber_stale(monkeypatch):
    _enable_quote(monkeypatch)
    old = datetime.datetime.now(UTC) - datetime.timedelta(hours=48)   # weekend-old source ts
    fake = FakeRedis(_tick_seed(received=old))
    steps.quote_step(fake)
    p = json.loads(fake.store["hermes:quote:XAU_USD:v1"])
    assert p["status"] == qt.STATUS_AMBER_STALE and p["freshness"] == qt.FRESHNESS_STALE
    assert p["age_seconds"] is not None and p["age_seconds"] > qt.QUOTE_STALE_THRESHOLD_SECONDS


def test_interpretation_dropped_and_no_consumer_trust(monkeypatch):
    # a tick envelope carrying interpretive junk must NOT leak into the quote payload; no consumer-live/trust field
    _enable_quote(monkeypatch)
    seed = _tick_seed(received=datetime.datetime.now(UTC),
                      extra={"signal": "BUY", "regime": "bull", "decision": "go"})
    fake = FakeRedis(seed)
    steps.quote_step(fake)
    p = json.loads(fake.store["hermes:quote:XAU_USD:v1"])
    def keys(o):
        if isinstance(o, dict):
            for kk, vv in o.items():
                yield str(kk).lower()
                yield from keys(vv)
        elif isinstance(o, list):
            for vv in o:
                yield from keys(vv)
    ks = set(keys(p))
    assert not any(t in k for k in ks for t in ("signal", "regime", "decision", "consumer_live", "trust"))
    assert qt.validate_quote_contract(p) is True   # forbidden-key scan passes


# ================================ 5. catalog preservation ================================
def test_catalog_quote_feed_health_tick_all_dark_pure_path():
    import datetime as D
    p = ic.build_instrument_catalog_contract(instrument="XAU_USD",
        generated_at_utc=D.datetime(2026, 7, 4, 16, 0, tzinfo=UTC), source_name="OANDA")
    for c in ("quote_contract", "feed_health_contract", "tick_contract"):
        assert p[c]["status"] == ic.SURFACE_CODE_PRESENT_DARK
        assert p[c]["runtime_published"] is False and p[c]["consumer_live"] is False
    # pure/pre-activation path: catalog itself is also dark, so all four dark surfaces are listed
    assert {"feed_health", "quote", "tick"}.issubset(set(p["pending_runtime_deployment"]["dark_surfaces"]))
    assert "quote" not in p.get("runtime_published_surfaces", [])


def test_catalog_runtime_published_still_only_catalog(monkeypatch):
    _enable_catalog(monkeypatch)
    fake = FakeRedis()
    steps.instrument_catalog_step(fake)
    p = json.loads(fake.store["hermes:instrument_catalog:XAU_USD:v1"])
    assert p["instrument_catalog_contract"]["status"] == ic.SURFACE_RUNTIME_PUBLISHED
    assert p["runtime_published_surfaces"] == ["instrument_catalog"]
    assert "quote" in p["pending_runtime_deployment"]["dark_surfaces"]


def test_quote_and_tick_now_permitted_non_surface_guarded():
    # quote and tick are both now permitted (each has a governed publisher/emitter). A non-surface still fails loud.
    import datetime as D
    assert "quote" in ic.RUNTIME_PUBLISHABLE_SURFACES and "tick" in ic.RUNTIME_PUBLISHABLE_SURFACES
    with pytest.raises(ValueError) as e:
        ic.build_instrument_catalog_contract(instrument="XAU_USD",
            generated_at_utc=D.datetime(2026, 7, 4, 16, 0, tzinfo=UTC),
            source_name="HERMES", runtime_published_surfaces=["market_map"])
    assert "GOV-HERMES-IC-030" in str(e.value)


def test_step_quote_dark_when_gate_absent(monkeypatch):
    # catalog running, quote gate ABSENT -> catalog marks quote dark (deploy-dark safety); feed-health also absent here.
    _enable_catalog(monkeypatch)
    _disable_quote(monkeypatch)
    for e in (fh.ENABLED_ENV, fh.AUTHORISED_ENV, fh.INSTRUMENTS_ENV):
        monkeypatch.delenv(e, raising=False)
    fake = FakeRedis()
    steps.instrument_catalog_step(fake)
    p = json.loads(fake.store["hermes:instrument_catalog:XAU_USD:v1"])
    assert p["runtime_published_surfaces"] == ["instrument_catalog"]
    assert p["quote_contract"]["runtime_published"] is False and p["quote_contract"]["status"] == ic.SURFACE_CODE_PRESENT_DARK
    assert "quote" in p["pending_runtime_deployment"]["dark_surfaces"]


def test_step_quote_runtime_published_when_gate_enabled_split_brain(monkeypatch):
    # catalog running AND quote gate ENABLED -> catalog marks quote RUNTIME_PUBLISHED, consumer_live False, dropped
    # from dark_surfaces. NO split-brain: catalog agrees with supervisor runner selection and the quote gate.
    _enable_catalog(monkeypatch)
    _enable_quote(monkeypatch)
    for e in (fh.ENABLED_ENV, fh.AUTHORISED_ENV, fh.INSTRUMENTS_ENV):
        monkeypatch.delenv(e, raising=False)
    fake = FakeRedis()
    steps.instrument_catalog_step(fake)
    p = json.loads(fake.store["hermes:instrument_catalog:XAU_USD:v1"])
    qc = p["quote_contract"]
    assert qc["status"] == ic.SURFACE_RUNTIME_PUBLISHED and qc["runtime_published"] is True and qc["consumer_live"] is False
    assert "quote" in p["runtime_published_surfaces"] and "quote" not in p["pending_runtime_deployment"]["dark_surfaces"]
    # the split-brain equality: quote in runtime_published_surfaces == quote runner selected == quote gate enabled
    quote_in_catalog = "quote" in p["runtime_published_surfaces"]
    quote_runner = "quote" in [n for (n, _s, _i) in rt.default_runner_specs()]
    quote_gate = getattr(qt.build_quote_publisher_from_env(), "enabled", False)
    assert quote_in_catalog == quote_runner == quote_gate is True
    # tick stays dark
    assert "tick" in p["pending_runtime_deployment"]["dark_surfaces"] and p["tick_contract"]["runtime_published"] is False


def test_step_quote_enabled_without_authorised_fails_loud(monkeypatch):
    _enable_catalog(monkeypatch)
    monkeypatch.setenv(qt.QUOTE_ENABLED_ENV, "true")
    monkeypatch.delenv(qt.QUOTE_AUTHORISED_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        steps.instrument_catalog_step(FakeRedis())
    assert e.value.code == 101


def test_step_quote_enabled_without_instrument_scope_fails_loud(monkeypatch):
    _enable_catalog(monkeypatch)
    monkeypatch.setenv(qt.QUOTE_ENABLED_ENV, "true")
    monkeypatch.setenv(qt.QUOTE_AUTHORISED_ENV, "true")
    monkeypatch.delenv(qt.QUOTE_INSTRUMENTS_ENV, raising=False)
    with pytest.raises(ValueError) as e:
        steps.instrument_catalog_step(FakeRedis())
    assert "GOV-HERMES-QT-020" in str(e.value)


# ================================ 6. boundary regression ================================
def test_no_xauusd_no_duplicate_tick_key(monkeypatch):
    _enable_quote(monkeypatch)
    fake = FakeRedis(_tick_seed(received=datetime.datetime.now(UTC)))
    steps.quote_step(fake)
    p = json.loads(fake.store["hermes:quote:XAU_USD:v1"])
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
    assert all(s != "hermes:tick:XAU_USD:v1" for s in strings(p))   # no duplicate singular tick key


def test_no_module_level_redis_client():
    # steps + qt construct NO redis client (injected client only). rt excluded (lazy _default_redis_client factory).
    for mod in (steps, qt):
        src = open(mod.__file__).read()
        assert "\nimport redis" not in src and "redis.Redis(" not in src and "\nfrom redis" not in src
