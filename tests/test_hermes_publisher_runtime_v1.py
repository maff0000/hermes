"""HERMES durable in-process publisher runtime — supervisor, duplicate-publisher guard, fault isolation, gate
preservation, and closed-H1 level semantics. CODE-ONLY, no deploy, no real Redis.
WO-HELM-HERMES-DURABLE-PUBLISHER-WIRING-MAINPY-0001.
"""
import threading
import time
from datetime import datetime, timezone

import pytest

import utils.hermes_publisher_runtime_v1 as rt
import utils.hermes_runtime_publisher_steps_v1 as steps
import utils.hermes_levels_v1 as lvl
import utils.hermes_sessions_v1 as sess

UTC = timezone.utc
_NOW = datetime(2026, 6, 30, 14, 0, tzinfo=UTC)
_SESSION_LEVELS = {"asia_high": 4082.5, "asia_low": 4070.1, "london_high": 4090.0, "london_low": 4075.2,
                   "range_midpoint": 4080.05}

def _disable_all_families(monkeypatch):
    for env in ("HERMES_REDIS_CONTROL_PLANE_ENABLED",
                "HERMES_INDICATOR_PUBLISH_ENABLED",
                "HERMES_CANDLE_FEATURE_PUBLISH_ENABLED",
                sess.ENABLED_ENV, lvl.ENABLED_ENV):
        monkeypatch.delenv(env, raising=False)


# =============================== supervisor gating ===============================
def test_supervisor_disabled_by_default(monkeypatch):
    monkeypatch.delenv(rt.ENABLED_ENV, raising=False)
    s = rt.build_publisher_supervisor_from_env()
    assert isinstance(s, rt.DisabledPublisherSupervisor) and s.enabled is False
    s.start(); s.stop()                                  # no-op safe; no threads, no client
    assert s.status() == {"enabled": False} and s.fault_summary() == {}


def test_enabled_without_authorised_exits_101(monkeypatch):
    monkeypatch.setenv(rt.ENABLED_ENV, "true")
    monkeypatch.delenv(rt.AUTHORISED_ENV, raising=False)
    sentinel = {"built": False}

    def factory():
        sentinel["built"] = True
        return "FAKE"

    with pytest.raises(SystemExit) as e:
        rt.build_publisher_supervisor_from_env(redis_client_factory=factory, runner_specs=[])
    assert e.value.code == 101
    assert sentinel["built"] is False                    # fail loud BEFORE building a redis client


def test_enabled_authorised_builds_but_not_started(monkeypatch):
    monkeypatch.setenv(rt.ENABLED_ENV, "true")
    monkeypatch.setenv(rt.AUTHORISED_ENV, "true")
    monkeypatch.delenv(rt.OWNER_ENV, raising=False)      # default owner -> detached
    specs = [("x", lambda c: {"published": 0}, 1)]
    s = rt.build_publisher_supervisor_from_env(redis_client_factory=lambda: "FAKE", runner_specs=specs)
    assert isinstance(s, rt.HermesPublisherSupervisor)
    assert s.started is False and s.owner == rt.DETACHED_OWNER


# =============================== duplicate-publisher guard ===============================
def test_duplicate_guard_refuses_unless_in_process(monkeypatch):
    monkeypatch.setenv(rt.ENABLED_ENV, "true")
    monkeypatch.setenv(rt.AUTHORISED_ENV, "true")
    monkeypatch.setenv(rt.OWNER_ENV, rt.DETACHED_OWNER)
    s = rt.build_publisher_supervisor_from_env(redis_client_factory=lambda: "FAKE",
                                               runner_specs=[("x", lambda c: {"published": 0}, 1)])
    with pytest.raises(ValueError) as e:
        s.start()
    assert "GOV-HERMES-PUBRT-002" in str(e.value)
    assert s.started is False


def test_duplicate_guard_allows_in_process_owner(monkeypatch):
    monkeypatch.setenv(rt.ENABLED_ENV, "true")
    monkeypatch.setenv(rt.AUTHORISED_ENV, "true")
    monkeypatch.setenv(rt.OWNER_ENV, rt.IN_PROCESS_OWNER)
    ran = {"n": 0}

    def step(c):
        ran["n"] += 1
        return {"published": 1}

    s = rt.build_publisher_supervisor_from_env(redis_client_factory=lambda: "FAKE",
                                               runner_specs=[("x", step, 1)])
    s.start()
    assert s.started is True and s.owner == rt.IN_PROCESS_OWNER
    for _ in range(100):
        if ran["n"] >= 1:
            break
        time.sleep(0.02)
    s.stop()
    assert ran["n"] >= 1 and s.started is False and s.fault_summary() == {"x": 0}


# =============================== runner fault isolation + graceful stop ===============================
def test_runner_exception_isolation_counts_and_survives():
    calls = {"n": 0}

    def bad(c):
        calls["n"] += 1
        raise RuntimeError("boom")

    r = rt.PublisherRunner("bad", bad, interval_seconds=1)
    r.start(redis_client=None)
    for _ in range(100):
        if calls["n"] >= 1:
            break
        time.sleep(0.02)
    r.stop()
    assert calls["n"] >= 1
    assert r.metrics["faults"] >= 1 and r.metrics["runs"] == 0      # fault counted, success never claimed
    assert r.metrics["last_fault"] and "boom" in r.metrics["last_fault"]
    assert r._thread is not None and not r._thread.is_alive()        # graceful join, app never crashed


def test_runner_counts_published_and_graceful_join():
    r = rt.PublisherRunner("g", lambda c: {"published": 3}, interval_seconds=1)
    r.start(redis_client=None)
    for _ in range(100):
        if r.metrics["runs"] >= 1:
            break
        time.sleep(0.02)
    r.stop()
    assert r.metrics["runs"] >= 1 and r.metrics["published"] >= 3 and r.metrics["faults"] == 0
    assert not r._thread.is_alive()


def test_systemexit_from_step_propagates_not_swallowed():
    # an enabled-without-authorised gate inside a step must NOT be silently isolated
    def gated(c):
        raise SystemExit(101)

    r = rt.PublisherRunner("gate", gated, interval_seconds=1)
    with pytest.raises(SystemExit) as e:
        r._loop(redis_client=None)
    assert e.value.code == 101


# =============================== import safety / no I/O / no starts at import ===============================
def test_no_publisher_threads_started_at_import():
    assert not any(t.name.startswith("hermes-pub-") for t in threading.enumerate())


@pytest.mark.parametrize("mod", [rt, steps])
def test_no_module_level_redis_or_starts_at_import(mod):
    src = open(mod.__file__).read()
    # redis is only ever imported lazily inside a function on the enabled path (runtime module); the steps module
    # never imports redis at all (it uses the INJECTED client). No top-level `import redis`.
    assert "\nimport redis" not in src and "\nfrom redis" not in src
    # no thread/loop is started at module import (start() lives in methods/lifespan, not at top level)
    assert "\n_default_redis_client(" not in src and "\n.start(" not in src


def test_runtime_module_no_auth_markers():
    src = open(rt.__file__).read().lower()
    for m in ("password=", "requirepass", ".auth(", "acl setuser", "username=", "ssl=", "noauth"):
        assert m not in src


def test_steps_module_no_redis_auth_markers():
    # the steps module legitimately reads GOVERNED DB config (pre-existing get_db_config -> pymysql password);
    # it must add NO Redis AUTH/ACL/credential gate and change no Redis security posture.
    low = open(steps.__file__).read().lower()
    for m in ("requirepass", ".auth(", "acl setuser", "noauth", "ssl="):
        assert m not in low


@pytest.mark.parametrize("mod", [rt, steps])
def test_no_regime_or_ares_interpretation_logic(mod):
    # Docstrings legitimately NAME the forbidden fields (the governance promise "no regime/risk/decision"); the
    # ban is on ARES INTERPRETATION LOGIC / legacy mutation actually being implemented. Interpretive field KEYS
    # are independently rejected at build/validate time by the contract modules these steps call.
    src = open(mod.__file__).read()
    for marker in ("regime_detector", ".detect_regime", "hermes:signals", "set_market_map", "import market_map",
                   "order_block_meaning", "smart_money_zone", "go_no_go", "decision_gate"):
        assert marker not in src


# =============================== gate preservation: families no-op when disabled (no client I/O) ===============================
class _BoomClient:
    def __getattr__(self, name):
        raise AssertionError(f"redis client touched ({name}) while family gate disabled")


def test_steps_noop_and_no_io_when_family_disabled(monkeypatch):
    _disable_all_families(monkeypatch)
    boom = _BoomClient()
    assert steps.control_plane_step(boom) == {"published": 0}
    assert steps.indicator_step(boom) == {"published": 0}
    assert steps.candle_feature_step(boom) == {"published": 0}
    assert steps.sessions_levels_step(boom) == {"published": 0}


def test_runtime_families_and_d1_gate_preserved():
    assert "D1" not in steps.LATEST_TFS                  # D1 stays gated while D1 latest AMBER
    names = [n for (n, _step, _i) in rt.default_runner_specs()]
    assert names == ["control_plane", "indicators", "candle_features", "sessions_levels"]


# =============================== closed-H1 level semantics (Part C) ===============================
def test_levels_payload_carries_closed_h1_semantics():
    p = lvl.build_level_contract(instrument="XAU_USD", scope="session", generated_at_utc=_NOW,
                                 levels=_SESSION_LEVELS)
    ls = p["level_semantics"]
    assert ls["level_source_granularity"] == "H1"
    assert ls["level_source_policy"] == "CLOSED_CANDLES_ONLY"
    assert ls["forming_candle_excluded"] is True
    assert ls["live_tick_included"] is False
    assert ls["as_of_candle_close_utc"] is None
    assert lvl.validate_level_contract(p) is True


def test_levels_as_of_close_recorded_when_supplied():
    p = lvl.build_level_contract(instrument="XAU_USD", scope="intraday", generated_at_utc=_NOW,
                                 levels={"intraday_high": 4090.0, "intraday_low": 4070.0, "intraday_midpoint": 4080.0},
                                 as_of_candle_close_utc=_NOW)
    assert p["level_semantics"]["as_of_candle_close_utc"].endswith("Z")
    assert lvl.validate_level_contract(p) is True


def test_daily_scope_semantics_granularity_d1():
    p = lvl.build_level_contract(instrument="XAU_USD", scope="daily", generated_at_utc=_NOW,
                                 levels={"prior_day_high": 4090.0, "prior_day_low": 4060.0, "adr_20": 28.5},
                                 d1_latest_green=True)
    assert p["level_semantics"]["level_source_granularity"] == "D1"
    assert lvl.validate_level_contract(p) is True


def test_validate_rejects_missing_semantics():
    p = lvl.build_level_contract(instrument="XAU_USD", scope="session", generated_at_utc=_NOW, levels=_SESSION_LEVELS)
    p.pop("level_semantics")
    with pytest.raises(ValueError) as e:
        lvl.validate_level_contract(p)
    assert "GOV-HERMES-LVL-015" in str(e.value)


def test_validate_rejects_live_tick_included():
    p = lvl.build_level_contract(instrument="XAU_USD", scope="session", generated_at_utc=_NOW, levels=_SESSION_LEVELS)
    p["level_semantics"]["live_tick_included"] = True
    with pytest.raises(ValueError) as e:
        lvl.validate_level_contract(p)
    assert "GOV-HERMES-LVL-018" in str(e.value)
