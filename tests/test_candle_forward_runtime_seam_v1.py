"""Tests for the HERMES candle-forward runtime seam (INERT / disabled-by-default).

WO-HELM-HERMES-CANDLE-FORWARD-RUNTIME-WIRE-INERT-0001. No Redis/SQL writes. Forbidden tokens are
fragment-assembled where needed.
"""
import os

from utils import candle_runtime_seam_v1 as seam
from utils import candle_publisher_v1 as cp


def _clear(monkeypatch):
    for k in list(os.environ):
        if k.startswith("HERMES_CANDLE_FORWARD") or (k.endswith("HERMES_CANDLE_FORWARD_ENABLED")):
            monkeypatch.delenv(k, raising=False)
    for k in ("HERMES_CANDLE_FORWARD_ENABLED", "HERMES_CANDLE_FORWARD_SINK",
              "DEV_HERMES_CANDLE_FORWARD_ENABLED", "DEV_HERMES_CANDLE_FORWARD_SINK"):
        monkeypatch.delenv(k, raising=False)


def test_disabled_by_default_returns_no_op_emitter(monkeypatch):
    _clear(monkeypatch)
    e = seam.build_candle_forward_seam_from_env()
    assert isinstance(e, cp.DisabledCandleEmitter)
    assert e.status()["enabled"] is False
    # emit writes nothing
    out = e.emit(envelope={"x": 1})
    assert out["emitted"] is False


def test_disabled_mode_requires_no_sink_config(monkeypatch):
    _clear(monkeypatch)
    # no sink/redis env at all -> must NOT raise
    seam.build_candle_forward_seam_from_env()


def test_enabled_without_sink_fails_loud(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_ENABLED", "true")
    raised = False
    try:
        seam.build_candle_forward_seam_from_env()
    except Exception as e:
        raised = True
        # env_config required-missing fail-loud (no hidden default)
        assert "HERMES_CANDLE_FORWARD_SINK" in str(e) or "required" in str(e).lower()
    assert raised, "enabled without sink must fail loud"


def test_live_prod_unknown_sink_forbidden(monkeypatch):
    # 'live'/'prod' (production-implying) and unknown sinks FAIL LOUD; 'shadow' and 'canonical' are
    # now separate governed paths (canonical requires explicit config — see the canonical tests).
    _clear(monkeypatch)
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_ENABLED", "true")
    for bad in ("live", "prod", "redis", "bogus"):
        monkeypatch.setenv("HERMES_CANDLE_FORWARD_SINK", bad)
        try:
            seam.build_candle_forward_seam_from_env()
            assert False, f"sink {bad} must be rejected"
        except ValueError as e:
            assert seam.FAULT_WRITE_FORBIDDEN in str(e)


def test_canonical_sink_without_config_fails_loud(monkeypatch):
    # canonical is a GOVERNED path now, but with no flags/config it must FAIL LOUD (never accidental).
    _clear(monkeypatch)
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_ENABLED", "true")
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_SINK", "canonical")
    for k in ("HERMES_CANDLE_PUBLISH_ENABLED", "HERMES_CANDLE_PUBLISH_AUTHORISED",
              "HERMES_CANDLE_CANONICAL_REDIS_HOST", "HERMES_CANDLE_CANONICAL_REDIS_PORT",
              "HERMES_CANDLE_CANONICAL_REDIS_DB"):
        monkeypatch.delenv(k, raising=False)
    try:
        seam.build_candle_forward_seam_from_env()
        assert False, "canonical without governed config must fail loud"
    except ValueError as e:
        # either required-config missing, or the canonical-disabled guard
        assert ("required" in str(e).lower() or "PUBLISH_DISABLED" in str(e)
                or "GOV-CANDLE-PUB-CANON-001" in str(e))


def test_enabled_inert_sink_is_no_write(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_ENABLED", "true")
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_SINK", "inert")
    e = seam.build_candle_forward_seam_from_env()
    assert isinstance(e, seam.InertCandleForwardSeam)
    assert e.enabled is False and e.write_mode == cp.WRITE_MODE_INERT
    assert isinstance(e.sink, cp.NoWriteCandleSink)
    out = e.emit(envelope={"key": "hermes:candles:XAU_USD:H4:latest:v1"})
    assert out["wrote"] is False and out["emitted"] is False


def test_no_proteus_no_stale_sql_no_cross_lane():
    src = open(seam.__file__).read()
    # no proteus fallback, no stale-SQL fallback, no consumer-lane reference
    assert ("trading" + "Proteus") not in src
    assert ("/srv" + "-dev") not in src
    assert "structure_engine" not in src
    # both governed real-client writers are wired (shadow + canonical), no legacy pubsub
    assert "SerializingCandleShadowWriter" in src
    assert "SerializingCandleCanonicalWriter" in src
    assert "signals:candle" not in src


def test_canonical_disabled_by_default_in_config():
    # the canonical writer EXISTS now, but canonical publish remains gated: the disabled config
    # (publish_enabled/authorised both false) must still fail the canonical guard.
    cfg = seam._disabled_config()
    try:
        cfg.assert_canonical_allowed()
        assert False, "canonical must be disabled by default"
    except ValueError as ex:
        assert "PUBLISH_DISABLED" in str(ex)


def test_disabled_emitter_accepts_keyword_candle_call_cleanly(monkeypatch):
    # WO-...-fix: the disabled seam (default) must accept the runtime hook's emit(candle=candle)
    # as a CLEAN no-op (not an exception-driven no-op). It must also accept emit(envelope=...) / emit().
    _clear(monkeypatch)
    em = seam.build_candle_forward_seam_from_env()           # default -> DisabledCandleEmitter
    assert isinstance(em, cp.DisabledCandleEmitter)
    # the previously-broken call shape — must NOT raise
    r1 = em.emit(candle={"instrument": "XAU_USD", "timeframe": "H4"})
    r2 = em.emit(envelope={"key": "hermes:candles:XAU_USD:H4:latest:v1"})
    r3 = em.emit()
    for r in (r1, r2, r3):
        assert r["emitted"] is False and r["reason"] == "CANDLE_PUBLISH_DISABLED"
    # still writes nothing: no redis client / connection constructed
    assert not any(hasattr(em, a) for a in ("redis_client", "client", "connection", "socket"))
