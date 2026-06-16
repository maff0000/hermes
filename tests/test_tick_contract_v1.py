"""Tests for the HERMES Redis tick contract v1 (design-only; no Redis I/O).
WO-HELM-HERMES-REDIS-TICK-CONTRACT-DESIGN-0001. Pure-logic + fixtures.
"""
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import utils.tick_contract_v1 as tc  # noqa: E402

FX = os.path.join(ROOT, "tests", "fixtures", "tick_contract")
REC = datetime(2026, 6, 16, 9, 58, 0, 0, tzinfo=timezone.utc)


def _g(sec, ms=0):
    return datetime(2026, 6, 16, 9, 58, sec, ms * 1000, tzinfo=timezone.utc)


def _fresh():
    return tc.build_tick_contract(instrument="XAU_USD", source_received_at_utc=REC,
                                  generated_at_utc=_g(0, 500), bid=4207.015, ask=4207.265)


# ---------- canonical keys ----------
def test_canonical_key_format():
    assert tc.canonical_key("XAU_USD") == "hermes:ticks:XAU_USD:latest:v1"
    assert tc.aggregate_key() == "hermes:ticks:latest:v1"


def test_canonical_key_fail_loud():
    for bad in ("", None):
        try:
            tc.canonical_key(bad); assert False
        except ValueError as e:
            assert "GOV-TICK-CONTRACT-001" in str(e)


# ---------- envelope + UTC + ttl + valid_until ----------
def test_envelope_required_fields_and_validate():
    assert tc.validate_tick_contract(_fresh()) is True


def test_utc_ms_timestamp_format_and_valid_until_calc():
    p = _fresh()
    assert p["generated_at_utc"] == "2026-06-16T09:58:00.500Z"
    assert p["valid_until_utc"] == "2026-06-16T09:58:05.500Z"   # generated + ttl_seconds(5)
    assert p["ttl_seconds"] == 5 and tc.REDIS_EX_SECONDS == 10


def test_utc_format_rejected_when_not_ms_z():
    p = _fresh(); p["generated_at_utc"] = "2026-06-16T09:58:00Z"  # no ms
    try:
        tc.validate_tick_contract(p); assert False
    except ValueError as e:
        assert "GOV-TICK-CONTRACT-016" in str(e)


# ---------- freshness / status policy ----------
def test_freshness_policy_boundaries():
    assert tc.classify_freshness(3)[0] == "FRESH"
    assert tc.classify_freshness(5)[0] == "FRESH"
    assert tc.classify_freshness(8)[0] == "DEGRADED"
    assert tc.classify_freshness(15)[0] == "DEGRADED"
    assert tc.classify_freshness(16)[0] == "STALE"
    assert tc.classify_freshness(-1)[0] == "UNAVAILABLE"
    assert tc.classify_freshness(2, source_warning=True)[0] == "DEGRADED"  # warning -> degraded


def test_built_states_match_age():
    assert tc.build_tick_contract(instrument="XAU_USD", source_received_at_utc=REC, generated_at_utc=_g(20), bid=1, ask=2)["freshness_state"] == "STALE"
    assert tc.build_tick_contract(instrument="XAU_USD", source_received_at_utc=REC, generated_at_utc=_g(8), bid=1, ask=2)["freshness_state"] == "DEGRADED"


def test_freshness_status_enum_validation():
    p = _fresh(); p["freshness_state"] = "VERY_FRESH"
    try:
        tc.validate_tick_contract(p); assert False
    except ValueError as e:
        assert "GOV-TICK-CONTRACT-013" in str(e)


# ---------- price sanity + key/instrument consistency ----------
def test_bid_ask_mid_spread_sanity():
    p = _fresh()
    assert p["data"]["mid"] == round((4207.015 + 4207.265) / 2, 6)
    assert p["data"]["spread"] == round(4207.265 - 4207.015, 6)
    for bad in [(0, 1), (1, 0), (5, 4)]:  # bid<=0, ask<=0, ask<bid
        try:
            tc.build_tick_contract(instrument="XAU_USD", source_received_at_utc=REC, generated_at_utc=_g(0, 500), bid=bad[0], ask=bad[1]); assert False
        except ValueError as e:
            assert "GOV-TICK-CONTRACT-003" in str(e)


def test_instrument_key_consistency():
    p = _fresh(); p["data"]["instrument"] = "EUR_USD"  # key still XAU_USD
    try:
        tc.validate_tick_contract(p); assert False
    except ValueError as e:
        assert "GOV-TICK-CONTRACT-018" in str(e)


# ---------- seq rule ----------
def test_seq_is_null_and_not_completeness():
    assert _fresh()["data"]["seq"] is None
    p = _fresh(); p["data"]["seq"] = 5
    try:
        tc.validate_tick_contract(p); assert False
    except ValueError as e:
        assert "GOV-TICK-CONTRACT-019" in str(e)


# ---------- ownership boundary ----------
def test_publisher_is_hermes_only():
    p = _fresh(); p["provenance"]["publisher"] = "FALCON"
    try:
        tc.validate_tick_contract(p); assert False
    except ValueError as e:
        assert "GOV-TICK-CONTRACT-011" in str(e)


def test_no_hermes_structure_ownership():
    for fld in ("structure_state", "regime", "signal", "support_level", "cockpit_view"):
        p = _fresh(); p["data"][fld] = "x"
        try:
            tc.validate_tick_contract(p); assert False, fld
        except ValueError as e:
            assert "GOV-TICK-CONTRACT-022" in str(e)


def test_derivation_is_none_raw_tick():
    assert _fresh()["provenance"]["derivation"] == "NONE_RAW_TICK"


def test_falcon_read_only_consumer_boundary():
    # contract carries no consumer-write fields / no SQL refs; publisher is HERMES, raw ticks only
    p = _fresh()
    s = json.dumps(p).lower()
    assert "tradingsignals.ticks" not in s and "select" not in s and "sql" not in s
    assert p["provenance"]["source_contract"] == "HERMES_TICK_SOURCE_V1"


# ---------- unavailable + aggregate ----------
def test_unavailable_envelope():
    p = tc.build_unavailable(instrument="XAU_USD", generated_at_utc=_g(0, 500), reason_codes=["INSTRUMENT_DISABLED"])
    assert p["freshness_state"] == "UNAVAILABLE" and p["status"] == "UNAVAILABLE"
    assert p["data"]["bid"] is None and "INSTRUMENT_DISABLED" in p["reason_codes"]
    assert tc.validate_tick_contract(p) is True


def test_aggregate_discovery_is_catalog_not_tick():
    p = tc.build_aggregate_discovery(instruments=["XAU_USD", "EUR_USD"], generated_at_utc=_g(0, 500))
    assert p["key"] == "hermes:ticks:latest:v1" and p["data"]["count"] == 2
    assert "bid" not in p["data"]
    assert tc.validate_tick_contract(p) is True


# ---------- committed fixtures ----------
def test_fixtures_validate():
    for name in ("per_instrument_fresh", "aggregate_fresh", "stale", "degraded", "unavailable"):
        assert tc.validate_tick_contract(json.load(open(os.path.join(FX, name + ".json")))) is True
    # malformed fixture MUST fail validation
    try:
        tc.validate_tick_contract(json.load(open(os.path.join(FX, "malformed_invalid.json")))); assert False
    except ValueError:
        pass


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = 0
    for fn in fns:
        try:
            fn(); p += 1; print("PASS", fn.__name__)
        except Exception:
            print("FAIL", fn.__name__); traceback.print_exc()
    print(f"{p}/{len(fns)} passed"); raise SystemExit(0 if p == len(fns) else 1)
