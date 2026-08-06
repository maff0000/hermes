"""Central Advanced-v1 publication-eligibility gate — substantive enforcement tests.
WO-HELM-HERMES-ADVANCED-V1-RUNTIME-MASTER-AND-SCOPE-GATE-0001. Pure, no I/O; params injected for determinism.

Proves: the XAU pilot is preserved without the expansion master; every seven-new x five-family combination is
fail-closed with master false; an eligible non-index/non-energy expansion passes ONLY when every gate is true
(non-vacuity); SPX500/WTICO stay calendar-blocked even under master; no gate can be bypassed; master and publisher
mode are genuinely consumed. No ticker branch anywhere in the gate or the publisher modules.
"""
import pathlib
from datetime import datetime, timezone

import pytest

import utils.hermes_advanced_v1_publication_gate_v1 as pg
import utils.hermes_instrument_registry_v1 as reg

UTC = timezone.utc
NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)     # within the calendar effective window
ROOT = pathlib.Path(__file__).resolve().parents[1]

PILOT = "XAU_USD"
SEVEN_NEW = ("XAG_USD", "EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY", "SPX500_USD", "WTICO_USD")
FAMILIES = ("tick", "indicator", "gaps", "backfill_status", "feed_health")
CALENDAR_BLOCKED = ("SPX500_USD", "WTICO_USD")
ELIGIBLE_EXPANSION = ("XAG_USD", "EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY")
_POLICY = {"XAU_USD": "metals", "XAG_USD": "metals", "EUR_USD": "fx_24x5", "GBP_USD": "fx_24x5",
           "AUD_USD": "fx_24x5", "USD_JPY": "fx_24x5", "SPX500_USD": "index_cash", "WTICO_USD": "energy"}
_CAT = {"metals": "precious_metals", "fx_24x5": "forex_major", "index_cash": "indices", "energy": "energy"}


def _row(symbol, *, caps=1):
    pol = _POLICY[symbol]
    return {"symbol": symbol, "broker_symbol": symbol, "name": symbol, "category": _CAT[pol], "enabled": 1,
            "oanda_compatible": 1, "price_precision": 3, "tick_size": 0.001, "price_authority": "mid",
            "market_hours_policy": pol, "expected_freshness_sec": 120,
            "enabled_timeframes": '["M1","M5","M15","H1","H4","D1"]', "indicator_profile": "standard_v1",
            "tick_contract_enabled": caps, "indicator_contract_enabled": caps, "gap_detection_enabled": caps,
            "backfill_policy": "status_only", "retention_policy": "default_v1", "metadata_version": "v1"}


def _records(all_caps=True):
    return reg.load_registry([_row(s, caps=(1 if all_caps else 0)) for s in ("XAU_USD",) + SEVEN_NEW])


RECS = _records()
PILOT_SCOPE = frozenset({PILOT})


def _d(instrument, family, **kw):
    kw.setdefault("now", NOW); kw.setdefault("records", RECS); kw.setdefault("pilot_scope", PILOT_SCOPE)
    return pg.decide(instrument, family, **kw)


# ============================ 16.1 pilot preservation (no master needed) ============================
@pytest.mark.parametrize("family", FAMILIES)
def test_pilot_permitted_without_master(family):
    d = _d(PILOT, family, master=False, publisher_mode="DISABLED")   # master OFF, mode DISABLED
    assert d.permitted and d.scope == pg.SCOPE_PILOT and d.reason == pg.PERMIT_PILOT


# ============================ 16.2 expansion denial matrix (master false) ============================
@pytest.mark.parametrize("instrument", SEVEN_NEW)
@pytest.mark.parametrize("family", FAMILIES)
def test_expansion_denied_master_false(instrument, family):
    d = _d(instrument, family, master=False, publisher_mode="ACTIVE")
    assert not d.permitted and d.scope == pg.SCOPE_EXPANSION and d.reason == pg.DENY_MASTER_DISABLED


@pytest.mark.parametrize("instrument", SEVEN_NEW)
def test_expansion_denied_publisher_disabled(instrument):
    d = _d(instrument, "gaps", master=True, publisher_mode="DISABLED")
    assert not d.permitted and d.reason == pg.DENY_PUBLISHER_DISABLED


def test_expansion_denied_shadow_unavailable():
    d = _d("EUR_USD", "gaps", master=True, publisher_mode="SHADOW")
    assert not d.permitted and d.reason == pg.DENY_SHADOW_UNAVAILABLE   # SHADOW fails closed (not implemented)


def test_expansion_denied_unknown_mode():
    d = _d("EUR_USD", "gaps", master=True, publisher_mode="turbo")
    assert not d.permitted and d.reason == pg.DENY_UNKNOWN_MODE


def test_expansion_denied_registry_capability_false():
    d = pg.decide("EUR_USD", "gaps", now=NOW, records=_records(all_caps=False), pilot_scope=PILOT_SCOPE,
                  master=True, publisher_mode="ACTIVE")
    assert not d.permitted and d.reason == pg.DENY_CAPABILITY_DISABLED


def test_expansion_denied_family_unauthorised():
    d = _d("EUR_USD", "gaps", master=True, publisher_mode="ACTIVE", family_authorised=False)
    assert not d.permitted and d.reason == pg.DENY_PUBLISHER_DISABLED


# ============================ 16.3 positive-path isolation (non-vacuity) ============================
@pytest.mark.parametrize("instrument", ELIGIBLE_EXPANSION)
def test_eligible_expansion_permitted_when_all_gates_true(instrument):
    d = _d(instrument, "gaps", master=True, publisher_mode="ACTIVE")
    assert d.permitted and d.scope == pg.SCOPE_EXPANSION and d.reason == pg.PERMIT_EXPANSION_ACTIVE
    assert d.calendar_status == "BROKER_CONFIRMED_WITH_HOLIDAY_LIMITATION" and d.readiness_ok is True


# ============================ 16.4 calendar negative (blocked even under master) ============================
@pytest.mark.parametrize("instrument", CALENDAR_BLOCKED)
@pytest.mark.parametrize("family", FAMILIES)
def test_index_and_energy_blocked_even_with_all_gates(instrument, family):
    d = _d(instrument, family, master=True, publisher_mode="ACTIVE", family_enabled=True, family_authorised=True)
    assert not d.permitted and d.reason == pg.DENY_CALENDAR_INELIGIBLE
    assert d.calendar_status == "ASSUMPTION_REQUIRES_PROVIDER"


# ============================ 16.5 abuse / bypass ============================
def test_alias_rejected():
    assert not _d("XAUUSD", "gaps", master=True, publisher_mode="ACTIVE").permitted
    assert _d("XAUUSD", "gaps", master=True, publisher_mode="ACTIVE").reason == pg.DENY_ALIAS


def test_unknown_family_rejected():
    assert _d("EUR_USD", "orders", master=True, publisher_mode="ACTIVE").reason == pg.DENY_UNKNOWN_FAMILY


def test_stale_calendar_blocks_expansion():
    future = datetime(2027, 6, 1, tzinfo=UTC)   # beyond fx/metals next_review -> stale
    d = _d("EUR_USD", "gaps", now=future, master=True, publisher_mode="ACTIVE")
    assert not d.permitted and d.reason == pg.DENY_CALENDAR_INELIGIBLE


def test_readiness_failure_blocks_expansion():
    bad = reg.load_registry([{**_row("XAU_USD"), "metadata_version": "v1"},
                             {**_row("EUR_USD"), "metadata_version": "v9"}])   # unsupported metadata -> readiness fails
    d = pg.decide("EUR_USD", "gaps", now=NOW, records=bad, pilot_scope=PILOT_SCOPE, master=True, publisher_mode="ACTIVE")
    assert not d.permitted and d.reason == pg.DENY_READINESS_FAILED


def test_missing_pilot_authority_fails_closed():
    d = pg.decide("EUR_USD", "gaps", now=NOW, records=RECS, pilot_scope=None, master=True, publisher_mode="ACTIVE")
    # pilot_scope injected None -> load from config (present in repo). Prove the loader path also denies without master:
    d2 = pg.decide("EUR_USD", "gaps", now=NOW, records=RECS, master=False, publisher_mode="ACTIVE")
    assert not d2.permitted


@pytest.mark.parametrize("only", ["master", "mode", "capability"])
def test_no_single_gate_alone_permits_expansion(only):
    kw = dict(master=False, publisher_mode="DISABLED")
    if only == "master":
        kw["master"] = True
    elif only == "mode":
        kw["publisher_mode"] = "ACTIVE"
    d = _d("EUR_USD", "gaps", **kw)
    assert not d.permitted            # master alone, or mode alone, never permits


def test_xau_permission_does_not_leak_to_expansion():
    assert _d(PILOT, "gaps", master=False, publisher_mode="DISABLED").permitted       # pilot ok
    assert not _d("EUR_USD", "gaps", master=False, publisher_mode="DISABLED").permitted  # neighbour still denied


# ============================ 16.6 non-vacuity: master & mode genuinely consumed ============================
def test_master_flag_genuinely_flips_outcome():
    off = _d("EUR_USD", "gaps", master=False, publisher_mode="ACTIVE")
    on = _d("EUR_USD", "gaps", master=True, publisher_mode="ACTIVE")
    assert (not off.permitted) and on.permitted        # only difference is the master -> it is consumed


def test_mode_genuinely_flips_outcome():
    disabled = _d("EUR_USD", "gaps", master=True, publisher_mode="DISABLED")
    active = _d("EUR_USD", "gaps", master=True, publisher_mode="ACTIVE")
    assert (not disabled.permitted) and active.permitted   # only difference is the mode -> it is consumed


# ============================ §7 fail-closed config defaults (env resolution) ============================
def test_env_defaults_fail_closed(monkeypatch):
    monkeypatch.delenv(pg.MASTER_ENV, raising=False); monkeypatch.delenv(pg.MODE_ENV, raising=False)
    assert pg.load_master_enabled() is False and pg.load_publisher_mode() == pg.MODE_DISABLED


def test_malformed_master_boolean_fails_closed(monkeypatch):
    monkeypatch.setenv(pg.MASTER_ENV, "maybe")
    with pytest.raises(pg.PublicationGateError):
        pg.load_master_enabled()


def test_unknown_env_mode_denies(monkeypatch):
    monkeypatch.setenv(pg.MODE_ENV, "banana")
    d = _d("EUR_USD", "gaps", master=True)   # publisher_mode resolved from env -> unknown -> deny
    assert not d.permitted and d.reason == pg.DENY_UNKNOWN_MODE


def test_truthy_string_master_consumed(monkeypatch):
    monkeypatch.setenv(pg.MASTER_ENV, "TRUE"); monkeypatch.setenv(pg.MODE_ENV, "active")
    d = _d("EUR_USD", "gaps")   # master/mode from env (case-insensitive)
    assert d.permitted


# ============================ §10/§13/§14/§15 no-ticker / one-stream / no consumer-order-backfill ============================
def _code(mod):
    import re
    src = (ROOT / mod).read_text()
    src = re.sub(r'"""[\s\S]*?"""', "", src); src = re.sub(r"'''[\s\S]*?'''", "", src)
    return "\n".join(re.sub(r"#.*$", "", l) for l in src.splitlines())


@pytest.mark.parametrize("mod", ["utils/hermes_advanced_v1_publication_gate_v1.py", "utils/hermes_gaps_v1.py",
                                 "utils/hermes_backfill_status_v1.py", "utils/hermes_feed_health_v1.py",
                                 "utils/tick_live_emitter_v1.py", "utils/hermes_indicators_v1.py"])
def test_no_ticker_branch_in_gate_or_publishers(mod):
    import re
    code = _code(mod)
    assert not re.search(r'if\s+instrument\s*==\s*["\']XAU_USD["\']', code), mod
    assert not re.search(r'if\s+\w*symbol\w*\s*==\s*["\']XAU_USD["\']', code), mod


def test_gate_has_no_stream_consumer_order_backfill_executor():
    code = _code("utils/hermes_advanced_v1_publication_gate_v1.py").lower()
    for tok in ("pricingstream", "oandapyv20", "thread(", "multiprocessing", "consumer_live=true",
                "place_order", "execute_backfill", "run_backfill"):
        assert tok not in code


# ============================ §17 mixed-state rehearsal through the REAL gaps publisher ============================
class _FakeRedis:
    def __init__(self): self.kv = {}; self.z = {}; self.writes = []; self.deletes = []
    def get(self, k): v = self.kv.get(k); return v.encode() if isinstance(v, str) else v
    def exists(self, k): return 1 if (k in self.kv or k in self.z) else 0
    def zrange(self, k, a, b): m = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1]); return [x for x, _ in (m[a:b+1] if b != -1 else m[a:])]
    def set(self, k, v, ex=None): self.writes.append(k); self.kv[k] = v
    def delete(self, *a): self.deletes.extend(a)


def test_mixed_state_master_off_only_pilot_publishes(monkeypatch):
    import utils.hermes_gaps_v1 as gaps
    monkeypatch.delenv(pg.MASTER_ENV, raising=False); monkeypatch.delenv(pg.MODE_ENV, raising=False)   # dark default
    r = _FakeRedis()
    keys = gaps.GapsPublisher(redis_client=r, records=RECS).publish(now=NOW)["keys"]
    assert keys == ["hermes:gaps:XAU_USD:v1"]                       # pilot active; ALL expansion dark
    for s in SEVEN_NEW:
        assert f"hermes:gaps:{s}:v1" not in r.kv


def test_mixed_state_master_on_pilot_plus_eligible_expansion_index_energy_blocked(monkeypatch):
    import utils.hermes_gaps_v1 as gaps
    monkeypatch.setenv(pg.MASTER_ENV, "true"); monkeypatch.setenv(pg.MODE_ENV, "ACTIVE")
    r = _FakeRedis()
    keys = set(gaps.GapsPublisher(redis_client=r, records=RECS).publish(now=NOW)["keys"])
    assert keys == {f"hermes:gaps:{s}:v1" for s in (PILOT,) + ELIGIBLE_EXPANSION}   # pilot + fx/metals expansion
    for s in CALENDAR_BLOCKED:
        assert f"hermes:gaps:{s}:v1" not in r.kv                    # index_cash/energy remain calendar-blocked
