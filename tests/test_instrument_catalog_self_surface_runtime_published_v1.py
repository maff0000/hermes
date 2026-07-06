"""HERMES instrument-catalog self-surface RUNTIME-PUBLISHED semantics — code-only, zero I/O, no Redis writes.
WO-HELM-HERMES-INSTRUMENT-CATALOG-SELF-SURFACE-RUNTIME-PUBLISHED-SEMANTICS-0001.

Fixes the self-contradiction where the runtime-PUBLISHED catalog still self-marked itself CODE_PRESENT_DARK /
live=False and stayed in dark_surfaces. Runtime publication (key written to Redis) and consumer-cutover/trusted-
live-plane are now DISTINCT truths; feed_health/quote/tick stay dark; the pure/pre-activation builder path is
unchanged. Deterministic; no regime/risk/signal/decision; canonical XAU_USD; XAUUSD alias-only.
"""
import json
from datetime import datetime, timezone

import pytest

import utils.hermes_instrument_catalog_v1 as ic
import utils.hermes_runtime_publisher_steps_v1 as steps
import utils.hermes_feed_health_v1 as fh

UTC = timezone.utc
_NOW = datetime(2026, 7, 4, 8, 0, tzinfo=UTC)


def _enable_feed_health(monkeypatch):
    monkeypatch.setenv(fh.ENABLED_ENV, "true")
    monkeypatch.setenv(fh.AUTHORISED_ENV, "true")
    monkeypatch.setenv(fh.INSTRUMENTS_ENV, "XAU_USD")


def _disable_feed_health(monkeypatch):
    for e in (fh.ENABLED_ENV, fh.AUTHORISED_ENV, fh.INSTRUMENTS_ENV):
        monkeypatch.delenv(e, raising=False)


def _pure(**over):
    """Pre-activation / pure builder path (nothing runtime-published)."""
    kw = dict(instrument="XAU_USD", generated_at_utc=_NOW, source_name="OANDA")
    kw.update(over)
    return ic.build_instrument_catalog_contract(**kw)


def _runtime():
    """Runtime-published path (as the supervisor step supplies it)."""
    return ic.build_instrument_catalog_contract(instrument="XAU_USD", generated_at_utc=_NOW, source_name="HERMES",
                                                runtime_published_surfaces=["instrument_catalog"])


class _FakeRedis:
    def __init__(self):
        self.store = {}
        self.sets = []

    def set(self, k, v, ex=None):
        assert isinstance(v, (str, bytes))
        self.store[k] = v
        self.sets.append((k, v, ex))
        return True


def _enable(monkeypatch):
    monkeypatch.setenv(ic.ENABLED_ENV, "true")
    monkeypatch.setenv(ic.AUTHORISED_ENV, "true")
    monkeypatch.setenv(ic.INSTRUMENTS_ENV, "XAU_USD")


def _step_payload(monkeypatch):
    _enable(monkeypatch)
    _disable_feed_health(monkeypatch)   # default: feed-health gate absent -> stays dark
    fake = _FakeRedis()
    assert steps.instrument_catalog_step(fake) == {"published": 1}
    return json.loads(fake.store["hermes:instrument_catalog:XAU_USD:v1"])


# ===================== catalog STEP tracks the feed-health gate (activation semantics) =====================
def test_step_feed_health_dark_when_gate_absent(monkeypatch):
    # catalog running, feed-health gate ABSENT -> catalog marks ONLY instrument_catalog runtime-published;
    # feed_health stays dark (this is the deploy-dark safety property).
    p = _step_payload(monkeypatch)
    assert p["runtime_published_surfaces"] == ["instrument_catalog"]
    assert p["feed_health_contract"]["runtime_published"] is False
    assert p["feed_health_contract"]["status"] == ic.SURFACE_CODE_PRESENT_DARK
    assert "feed_health" in p["pending_runtime_deployment"]["dark_surfaces"]


def test_step_feed_health_runtime_published_when_gate_enabled(monkeypatch):
    # catalog running AND feed-health gate ENABLED -> catalog marks feed_health RUNTIME_PUBLISHED (tracks the live
    # supervisor), consumer_live stays False, dropped from dark_surfaces. quote/tick stay dark.
    _enable(monkeypatch)
    _enable_feed_health(monkeypatch)
    fake = _FakeRedis()
    assert steps.instrument_catalog_step(fake) == {"published": 1}
    p = json.loads(fake.store["hermes:instrument_catalog:XAU_USD:v1"])
    assert set(p["runtime_published_surfaces"]) == {"instrument_catalog", "feed_health"}
    fhc = p["feed_health_contract"]
    assert fhc["status"] == ic.SURFACE_RUNTIME_PUBLISHED and fhc["runtime_published"] is True and fhc["consumer_live"] is False
    assert "feed_health" not in p["pending_runtime_deployment"]["dark_surfaces"]
    assert set(p["pending_runtime_deployment"]["dark_surfaces"]) == {"quote", "tick"}
    assert p["instrument_catalog_contract"]["status"] == ic.SURFACE_RUNTIME_PUBLISHED   # unchanged


def test_step_feed_health_enabled_without_authorised_fails_loud(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv(fh.ENABLED_ENV, "true")
    monkeypatch.delenv(fh.AUTHORISED_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        steps.instrument_catalog_step(_FakeRedis())
    assert e.value.code == 101


# ===================== runtime-published: explicit truth, no longer dark =====================
def test_runtime_published_not_listed_as_dark():
    p = _runtime()
    assert "instrument_catalog" not in p["pending_runtime_deployment"]["dark_surfaces"]
    assert p["instrument_catalog_contract"]["status"] == ic.SURFACE_RUNTIME_PUBLISHED
    assert p["surfaces"]["instrument_catalog"] == ic.SURFACE_RUNTIME_PUBLISHED
    assert p["instrument_catalog_contract"]["status"] != ic.SURFACE_CODE_PRESENT_DARK


def test_runtime_publication_truth_explicit():
    p = _runtime()
    assert p["instrument_catalog_contract"]["runtime_published"] is True
    assert p["runtime_published_surfaces"] == ["instrument_catalog"]
    assert p["pending_runtime_deployment"]["runtime_published_surfaces"] == ["instrument_catalog"]


def test_consumer_cutover_remains_false_not_implied():
    p = _runtime()
    icc = p["instrument_catalog_contract"]
    assert icc["consumer_live"] is False           # trusted-live-plane / consumer cutover NOT implied
    assert icc["live"] is False                    # legacy `live` == consumer_live, still False
    assert p["pending_runtime_deployment"]["runtime_live"] is False


def test_no_single_ambiguous_live_field():
    # runtime publication and consumer cutover are represented by DISTINCT fields (never one `live` meaning both)
    icc = _runtime()["instrument_catalog_contract"]
    assert "runtime_published" in icc and "consumer_live" in icc
    assert icc["runtime_published"] is True and icc["consumer_live"] is False   # genuinely different values


# ===================== other surfaces stay dark =====================
def test_feed_health_quote_tick_remain_dark_when_catalog_runtime_published():
    p = _runtime()
    assert set(p["pending_runtime_deployment"]["dark_surfaces"]) == {"feed_health", "quote", "tick"}
    for c in ("feed_health_contract", "quote_contract", "tick_contract"):
        assert p[c]["status"] == ic.SURFACE_CODE_PRESENT_DARK
        assert p[c]["runtime_published"] is False and p[c]["consumer_live"] is False and p[c]["live"] is False


def test_dark_surfaces_only_genuinely_dark():
    p = _runtime()
    dark = p["pending_runtime_deployment"]["dark_surfaces"]
    assert all(p["surfaces"][s] == ic.SURFACE_CODE_PRESENT_DARK for s in dark)
    assert "instrument_catalog" not in dark


def test_quote_tick_may_not_be_marked_runtime_published():
    # quote/tick have no active runtime publisher -> still fail loud. (feed_health is now permitted — see
    # test_feed_health_may_be_marked_runtime_published below.)
    for bad in ("quote", "tick", "candles"):
        with pytest.raises(ValueError) as e:
            ic.build_instrument_catalog_contract(instrument="XAU_USD", generated_at_utc=_NOW, source_name="HERMES",
                                                 runtime_published_surfaces=[bad])
        assert "GOV-HERMES-IC-030" in str(e.value)


def test_feed_health_may_be_marked_runtime_published():
    # WO-...-CATALOG-FEED-HEALTH-RUNTIME-PUBLISHED-SEMANTICS: feed_health now has a governed runtime publisher, so
    # it is permitted in RUNTIME_PUBLISHABLE_SURFACES and marked RUNTIME_PUBLISHED when passed (consumer_live stays
    # False, dropped from dark_surfaces). quote/tick remain guarded.
    assert "feed_health" in ic.RUNTIME_PUBLISHABLE_SURFACES
    p = ic.build_instrument_catalog_contract(instrument="XAU_USD", generated_at_utc=_NOW, source_name="HERMES",
                                             runtime_published_surfaces=["instrument_catalog", "feed_health"])
    fhc = p["feed_health_contract"]
    assert fhc["status"] == ic.SURFACE_RUNTIME_PUBLISHED
    assert fhc["runtime_published"] is True and fhc["consumer_live"] is False and fhc["live"] is False
    assert p["surfaces"]["feed_health"] == ic.SURFACE_RUNTIME_PUBLISHED
    assert set(p["runtime_published_surfaces"]) == {"instrument_catalog", "feed_health"}
    assert "feed_health" not in p["pending_runtime_deployment"]["dark_surfaces"]
    assert set(p["pending_runtime_deployment"]["dark_surfaces"]) == {"quote", "tick"}   # only genuinely-dark left
    # quote/tick stay dark + not runtime-published even while feed_health is runtime-published
    for c in ("quote_contract", "tick_contract"):
        assert p[c]["status"] == ic.SURFACE_CODE_PRESENT_DARK and p[c]["runtime_published"] is False
    assert ic.validate_instrument_catalog_contract(p) is True


# ===================== preserved invariants (still valid) =====================
def test_tick_still_references_existing_key_no_duplicate():
    p = _runtime()
    assert p["tick_contract"]["key"] == "hermes:ticks:XAU_USD:latest:v1"
    assert all("hermes:tick:XAU_USD:v1" != k for k in ic._iter_key_strings(p))


def test_no_xauusd_output_key_runtime_path():
    p = _runtime()
    assert all(":XAUUSD:" not in k for k in ic._iter_key_strings(p) if isinstance(k, str))
    assert p["instrument"] == "XAU_USD" and p["aliases"]["inbound"] == ["XAUUSD"]


def test_d1_not_hardcoded_active_runtime_path():
    p = _runtime()
    assert p["candle_contracts"]["D1"]["latest_status"] == ic.SURFACE_PENDING_FIRST_DAILY_SEAL
    assert p["d1_policy"]["latest_status"] == ic.SURFACE_PENDING_FIRST_DAILY_SEAL


def test_no_regime_risk_signal_decision_semantics_runtime_path():
    assert ic.validate_instrument_catalog_contract(_runtime()) is True   # forbidden-key scan passes
    p = _runtime()
    p["notes"] = [{"decision_gate": 1}]
    with pytest.raises(ValueError) as e:
        ic.validate_instrument_catalog_contract(p)
    assert "GOV-HERMES-IC-003" in str(e.value)


def test_runtime_published_payload_validates():
    assert ic.validate_instrument_catalog_contract(_runtime()) is True


# ===================== pure/pre-activation path UNCHANGED (PR #69/#70/#71 preserved) =====================
def test_pure_builder_path_still_dark():
    p = _pure()
    assert p["instrument_catalog_contract"]["status"] == ic.SURFACE_CODE_PRESENT_DARK
    assert p["instrument_catalog_contract"]["runtime_published"] is False
    assert set(p["pending_runtime_deployment"]["dark_surfaces"]) == {"feed_health", "instrument_catalog", "quote", "tick"}
    assert p["runtime_published_surfaces"] == []


def test_none_arg_equivalent_to_dark():
    assert _pure(runtime_published_surfaces=None)["instrument_catalog_contract"]["status"] == ic.SURFACE_CODE_PRESENT_DARK


# ===================== step (real publish path) exposes runtime-published truth =====================
def test_step_path_marks_runtime_published(monkeypatch):
    p = _step_payload(monkeypatch)
    assert p["instrument_catalog_contract"]["runtime_published"] is True
    assert p["instrument_catalog_contract"]["consumer_live"] is False
    assert "instrument_catalog" not in p["pending_runtime_deployment"]["dark_surfaces"]
    assert p["runtime_published_surfaces"] == ["instrument_catalog"]
    assert set(p["pending_runtime_deployment"]["dark_surfaces"]) == {"feed_health", "quote", "tick"}
