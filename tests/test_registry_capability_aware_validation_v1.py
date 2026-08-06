"""Capability-aware registry validation + real 14-row production-shape compatibility.
WO-HELM-HERMES-REGISTRY-INACTIVE-ROW-VALIDATION-AND-PRODUCTION-SHAPE-0001. Pure, no I/O.

Proves: the real post-migration-025 production shape (14 rows, 6 non-cohort with NULL Advanced-v1 metadata) LOADS;
inactive rows classify NOT_ENABLED with no invented defaults and cannot publish; a capability-active row with
incomplete metadata FAILS CLOSED; universal fields stay strictly validated; selectors/gate/pilot exclude inactive
rows; the five publishers construct safely on the 14-row shape; no ticker-specific exemption exists.
"""
import json
import pathlib
from datetime import datetime, timezone

import pytest

import utils.hermes_instrument_registry_v1 as reg
import utils.hermes_advanced_v1_selection_v1 as sel
import utils.hermes_advanced_v1_publication_gate_v1 as pg
import utils.hermes_gaps_v1 as gaps
import utils.hermes_backfill_status_v1 as bfs

UTC = timezone.utc
NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
ROOT = pathlib.Path(__file__).resolve().parents[1]

COHORT = ("XAU_USD", "XAG_USD", "EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY", "SPX500_USD", "WTICO_USD")
NON_COHORT = ("XPT_USD", "XCU_USD", "USD_CHF", "USD_CAD", "NZD_USD", "EUR_GBP")   # NULL Advanced-v1 metadata in prod
_POLICY = {"XAU_USD": "metals", "XAG_USD": "metals", "EUR_USD": "fx_24x5", "GBP_USD": "fx_24x5", "AUD_USD": "fx_24x5",
           "USD_JPY": "fx_24x5", "SPX500_USD": "index_cash", "WTICO_USD": "energy"}
_CAT = {"metals": "precious_metals", "fx_24x5": "forex_major", "index_cash": "indices", "energy": "energy"}
_NONCOHORT_CAT = {"XPT_USD": "precious_metals", "XCU_USD": "base_metals", "USD_CHF": "forex_major",
                  "USD_CAD": "forex_major", "NZD_USD": "forex_major", "EUR_GBP": "forex_minor"}


def _complete_row(symbol, *, caps):
    pol = _POLICY[symbol]
    return {"symbol": symbol, "broker_symbol": symbol, "name": symbol, "category": _CAT[pol], "enabled": 1,
            "oanda_compatible": 1, "price_precision": 3, "tick_size": 0.001, "price_authority": "mid",
            "market_hours_policy": pol, "expected_freshness_sec": 120,
            "enabled_timeframes": '["M1","M5","M15","H1","H4","D1"]', "indicator_profile": "standard_v1",
            "tick_contract_enabled": caps, "indicator_contract_enabled": caps, "gap_detection_enabled": caps,
            "backfill_policy": "status_only", "retention_policy": "default_v1", "metadata_version": "v1"}


def _inactive_null_row(symbol):
    """A capability-inactive row with NULL Advanced-v1 metadata — the real production shape for the six non-cohort rows.
    Universal + NOT-NULL-default fields present; price_precision/tick_size/market_hours_policy/expected_freshness_sec/
    enabled_timeframes are NULL; all capability flags false."""
    return {"symbol": symbol, "broker_symbol": symbol, "name": symbol, "category": _NONCOHORT_CAT[symbol],
            "enabled": 1, "oanda_compatible": 1, "price_precision": None, "tick_size": None, "price_authority": "mid",
            "market_hours_policy": None, "expected_freshness_sec": None, "enabled_timeframes": None,
            "indicator_profile": "standard_v1", "tick_contract_enabled": 0, "indicator_contract_enabled": 0,
            "gap_detection_enabled": 0, "backfill_policy": "status_only", "retention_policy": "default_v1",
            "metadata_version": "v1"}


def production_shape_rows(*, xau_active=True):
    """The real 14-row post-025 production shape: XAU pilot active (caps on, complete); the 7 other rollout instruments
    inactive-but-metadata-complete (caps off); the 6 non-cohort inactive with NULL capability metadata."""
    rows = [_complete_row("XAU_USD", caps=(1 if xau_active else 0))]
    rows += [_complete_row(s, caps=0) for s in COHORT if s != "XAU_USD"]   # rollout, caps off, metadata complete
    rows += [_inactive_null_row(s) for s in NON_COHORT]                    # non-cohort, caps off, NULL metadata
    return rows


def _records():
    return reg.load_registry(production_shape_rows())


# ============================ §11 positive: 14-row load ============================
def test_production_shape_loads_all_14():
    recs = _records()
    assert len(recs) == 14 and {r.symbol for r in recs} == set(COHORT + NON_COHORT)


def test_six_non_cohort_not_enabled_no_invented_defaults():
    recs = {r.symbol: r for r in _records()}
    for s in NON_COHORT:
        r = recs[s]
        assert r.effective_state == reg.STATE_NOT_ENABLED and r.advanced_v1_active is False
        # NULL capability metadata is preserved as None — NOT coerced to zero/default
        assert r.price_precision is None and r.tick_size is None and r.market_hours_policy is None
        assert r.expected_freshness_sec is None and r.enabled_timeframes is None


def test_xau_active_and_metadata_complete():
    r = {x.symbol: x for x in _records()}["XAU_USD"]
    assert r.advanced_v1_active and r.effective_state == reg.STATE_ACTIVE
    assert r.price_precision == 3 and r.tick_size == 0.001 and r.market_hours_policy == "metals"


def test_rollout_inactive_but_metadata_complete_still_not_enabled():
    recs = {r.symbol: r for r in _records()}
    for s in ("XAG_USD", "EUR_USD", "SPX500_USD", "WTICO_USD"):
        assert recs[s].effective_state == reg.STATE_NOT_ENABLED   # caps off in production -> NOT_ENABLED
        assert recs[s].price_precision is not None                # but metadata is complete (seeded by 025)


@pytest.mark.parametrize("cap", ["tick", "indicator", "gap"])
def test_selectors_exclude_all_inactive(cap):
    recs = _records()
    picked = set(sel.selection_for(cap, recs))
    assert picked == {"XAU_USD"}                                  # only the active pilot
    for s in NON_COHORT:
        assert s not in picked


def test_backfill_and_pilot_scope_exclude_inactive():
    recs = _records()
    assert set(sel.backfill_status_instruments(recs)) == {"XAU_USD"}
    pilot = pg.load_pilot_scope()
    for s in NON_COHORT:
        assert s not in pilot                                     # inactive rows are not in pilot scope


# ============================ §9/§12 active-incomplete fails closed ============================
@pytest.mark.parametrize("field", ["price_precision", "tick_size", "market_hours_policy", "expected_freshness_sec"])
def test_capability_active_missing_field_fails_closed(field):
    row = _inactive_null_row("XPT_USD"); row["tick_contract_enabled"] = 1   # activate a capability
    # give the OTHER required fields so only `field` is missing
    complete = {"price_precision": 3, "tick_size": 0.001, "market_hours_policy": "metals", "expected_freshness_sec": 120}
    for k, v in complete.items():
        if k != field:
            row[k] = v
    with pytest.raises(reg.RegistryError, match="INCOMPLETE-ACTIVE"):
        reg.validate_record(row)


def test_indicator_active_missing_timeframes_fails_closed():
    row = _complete_row("EUR_USD", caps=0); row["indicator_contract_enabled"] = 1; row["enabled_timeframes"] = None
    with pytest.raises(reg.RegistryError, match="INCOMPLETE-ACTIVE"):
        reg.validate_record(row)


def test_present_but_invalid_metadata_still_fails_even_on_inactive_row():
    for bad in ({"price_precision": -1}, {"tick_size": 0}, {"market_hours_policy": "mars"},
                {"expected_freshness_sec": 0}, {"enabled_timeframes": ["M2"]}):
        row = _inactive_null_row("XCU_USD"); row.update(bad)      # inactive, but a PRESENT value must be valid
        with pytest.raises(reg.RegistryError):
            reg.validate_record(row)


# ============================ §7 guarded accessor ============================
def test_require_complete_guard():
    recs = {r.symbol: r for r in _records()}
    with pytest.raises(reg.RegistryError, match="REG-INACTIVE"):
        recs["XPT_USD"].require_complete()                        # NOT_ENABLED -> never for publisher use
    assert recs["XAU_USD"].require_complete().symbol == "XAU_USD"  # active + complete -> ok


# ============================ §13 publisher construction safety on 14-row shape ============================
class _FakeRedis:
    def __init__(self): self.kv = {}; self.z = {}; self.writes = []; self.deletes = []
    def get(self, k): v = self.kv.get(k); return v.encode() if isinstance(v, str) else v
    def exists(self, k): return 1 if (k in self.kv or k in self.z) else 0
    def zrange(self, k, a, b): m = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1]); return [x for x, _ in (m[a:b+1] if b != -1 else m[a:])]
    def set(self, k, v, ex=None): self.writes.append(k); self.kv[k] = v
    def delete(self, *a): self.deletes.extend(a)


def test_publishers_construct_on_14_row_shape_and_only_pilot(monkeypatch):
    # dark controls (master false, mode DISABLED) -> only XAU pilot publishes; inactive rows inert
    monkeypatch.delenv("HERMES_ADVANCED_V1_MASTER_ENABLED", raising=False)
    monkeypatch.delenv("HERMES_ADVANCED_V1_PUBLISHER_MODE", raising=False)
    recs = _records()
    r = _FakeRedis()
    gres = gaps.GapsPublisher(redis_client=r, records=recs).publish(now=NOW)
    assert gres["keys"] == ["hermes:gaps:XAU_USD:v1"]             # construction succeeds; only pilot published
    for s in NON_COHORT:
        assert f"hermes:gaps:{s}:v1" not in r.kv                  # inactive rows never emit


def test_inactive_row_cannot_publish_even_with_master_and_mode(monkeypatch):
    monkeypatch.setenv("HERMES_ADVANCED_V1_MASTER_ENABLED", "true")
    monkeypatch.setenv("HERMES_ADVANCED_V1_PUBLISHER_MODE", "ACTIVE")
    recs = _records()
    # an inactive row is not selected, and the gate denies it (capability disabled) even if reached directly
    d = pg.decide("XPT_USD", "gaps", now=NOW, records=recs, master=True, publisher_mode="ACTIVE")
    assert not d.permitted and d.reason == pg.DENY_CAPABILITY_DISABLED
    r = _FakeRedis()
    keys = gaps.GapsPublisher(redis_client=r, records=recs).publish(now=NOW)["keys"]
    assert keys == ["hermes:gaps:XAU_USD:v1"]                     # still only pilot (inactive not selected)


# ============================ §12 abuse / bypass ============================
def test_alias_row_rejected_even_inactive():
    row = _inactive_null_row("XPT_USD"); row["symbol"] = "XAUUSD"
    r = reg.validate_record(row)                                  # loads as a row, but selection/gate reject the alias
    assert not pg.decide("XAUUSD", "gaps", now=NOW, records=[r], master=True, publisher_mode="ACTIVE").permitted


def test_malformed_capability_flag_rejected_not_coerced():
    # STRICT parsing: a malformed capability authority value is CONFIGURATION_INVALID -> raises; NEVER coerced to False
    # and NEVER interpreted as NOT_ENABLED.
    row = _inactive_null_row("XCU_USD"); row["tick_contract_enabled"] = "garbage"
    with pytest.raises(reg.RegistryError, match="INVALID-CAPABILITY-FLAG"):
        reg.validate_record(row)


@pytest.mark.parametrize("bad", [None, "", " ", "0", "1", "true", "false", "yes", "no", "on", "off", "maybe", "2",
                                 2, -1, 0.0, 1.0, [1], {"a": 1}, b"1", object()])
@pytest.mark.parametrize("field", ["tick_contract_enabled", "indicator_contract_enabled", "gap_detection_enabled"])
def test_strict_capability_flag_rejects_malformed(field, bad):
    row = _inactive_null_row("XPT_USD"); row[field] = bad
    with pytest.raises(reg.RegistryError, match="INVALID-CAPABILITY-FLAG"):
        reg.validate_record(row)


@pytest.mark.parametrize("field", ["tick_contract_enabled", "indicator_contract_enabled", "gap_detection_enabled"])
@pytest.mark.parametrize("good,expected", [(0, False), (1, True), (False, False), (True, True)])
def test_strict_capability_flag_accepts_governed_forms(field, good, expected):
    # a fully inactive base row; set exactly one flag to a governed form. When True, the row becomes active and must
    # carry complete metadata (use a complete base so True is valid); when False it stays NOT_ENABLED with null metadata.
    base = _complete_row("EUR_USD", caps=0) if expected else _inactive_null_row("XPT_USD")
    base["tick_contract_enabled"] = 0; base["indicator_contract_enabled"] = 0; base["gap_detection_enabled"] = 0
    base[field] = good
    rec = reg.validate_record(base)
    assert rec.capability(field) is expected


def test_one_malformed_among_valid_flags_rejected():
    row = _complete_row("EUR_USD", caps=0); row["tick_contract_enabled"] = 1; row["gap_detection_enabled"] = "x"
    with pytest.raises(reg.RegistryError, match="INVALID-CAPABILITY-FLAG"):
        reg.validate_record(row)


def test_14_row_fixture_not_silently_truncated_to_8():
    assert len(production_shape_rows()) == 14                     # guard against accidental cohort-only fixture use
    assert len([r for r in production_shape_rows() if r["price_precision"] is None]) == 6


# ============================ §10 require_complete() load-bearing at all five family boundaries ============================
def _active_incomplete_record(symbol="XPT_USD"):
    """A directly-CONSTRUCTED InstrumentRecord that bypassed validate_record: a capability is active but required
    metadata is None. This is exactly what require_complete() at the family boundary must catch."""
    return reg.InstrumentRecord(
        symbol=symbol, broker_symbol=symbol, name=symbol, category="precious_metals", enabled=True,
        oanda_compatible=True, price_precision=None, tick_size=None, price_authority="mid",
        market_hours_policy=None, expected_freshness_sec=None, enabled_timeframes=None, indicator_profile="standard_v1",
        tick_contract_enabled=True, indicator_contract_enabled=True, gap_detection_enabled=True,
        backfill_policy="status_only", retention_policy="default_v1", metadata_version="v1")


def test_gaps_boundary_rejects_injected_incomplete_active():
    bad = _active_incomplete_record()
    r = _FakeRedis()
    with pytest.raises(reg.RegistryError, match="INCOMPLETE-ACTIVE|REG-INACTIVE"):
        gaps.GapsPublisher(redis_client=r, records=[bad])         # boundary guard fires at construction
    assert r.writes == []                                          # no key emitted


def test_backfill_boundary_rejects_injected_incomplete_active():
    bad = _active_incomplete_record()
    r = _FakeRedis()
    with pytest.raises(reg.RegistryError, match="INCOMPLETE-ACTIVE|REG-INACTIVE"):
        bfs.BackfillStatusPublisher(redis_client=r, records=[bad])
    assert r.writes == []


def test_feed_health_boundary_rejects_injected_incomplete_active():
    import utils.hermes_feed_health_v1 as fh
    bad = _active_incomplete_record()
    with pytest.raises(reg.RegistryError, match="INCOMPLETE-ACTIVE|REG-INACTIVE"):
        fh.FeedHealthPublisher(allowed_instruments=frozenset({"XPT_USD"}), source_name="X", records=[bad])


def test_tick_boundary_rejects_injected_incomplete_active():
    import utils.tick_live_emitter_v1 as tk
    bad = _active_incomplete_record()
    with pytest.raises(reg.RegistryError, match="INCOMPLETE-ACTIVE|REG-INACTIVE"):
        tk.LiveTickEmitter(allowed_instruments=frozenset({"XPT_USD"}), redis_client=_FakeRedis(), records=[bad])


def test_indicator_boundary_rejects_injected_incomplete_active():
    import utils.hermes_indicators_v1 as ind
    bad = _active_incomplete_record()
    with pytest.raises(reg.RegistryError, match="INCOMPLETE-ACTIVE|REG-INACTIVE"):
        ind.IndicatorPublisher(allowed_instruments=frozenset({"XPT_USD"}), timeframes=("M5",), records=[bad])


def test_boundary_accepts_valid_active_complete_records():
    # a valid active-complete registry -> all boundaries construct without raising (guard is not over-eager)
    import utils.hermes_feed_health_v1 as fh, utils.tick_live_emitter_v1 as tk, utils.hermes_indicators_v1 as ind
    recs = reg.load_registry([_complete_row("XAU_USD", caps=1)])
    gaps.GapsPublisher(redis_client=_FakeRedis(), records=recs)
    bfs.BackfillStatusPublisher(redis_client=_FakeRedis(), records=recs)
    fh.FeedHealthPublisher(allowed_instruments=frozenset({"XAU_USD"}), source_name="X", records=recs)
    tk.LiveTickEmitter(allowed_instruments=frozenset({"XAU_USD"}), redis_client=_FakeRedis(), records=recs)
    ind.IndicatorPublisher(allowed_instruments=frozenset({"XAU_USD"}), timeframes=("M5",), records=recs)


# ============================ §16 isolated production-shape rehearsal ============================
def test_production_shape_rehearsal_mixed_state(monkeypatch):
    """PRODUCTION_SHAPE_REHEARSAL_GREEN — the 14-row shape loads; effective-state summary is truthful; the pilot
    publishes under dark controls; the 6 inactive rows are inert; enabling a capability on an incomplete row fails
    closed (non-vacuity for strict validation)."""
    monkeypatch.delenv("HERMES_ADVANCED_V1_MASTER_ENABLED", raising=False)
    monkeypatch.delenv("HERMES_ADVANCED_V1_PUBLISHER_MODE", raising=False)   # dark: master false, mode DISABLED
    recs = _records()
    summ = reg.registry_effective_summary(recs)
    assert summ["registry_rows"] == 14
    assert summ["advanced_v1_active"] == ["XAU_USD"]                          # only the pilot active
    assert set(NON_COHORT).issubset(set(summ["not_enabled"])) and summ["not_enabled_count"] == 13
    assert summ["selection"]["tick"] == ("XAU_USD",) and summ["selection"]["gap"] == ("XAU_USD",)

    # pilot publishes gaps + backfill; inactive rows never emit; no second stream/consumer/order in these modules
    r = _FakeRedis()
    gk = gaps.GapsPublisher(redis_client=r, records=recs).publish(now=NOW)["keys"]
    assert gk == ["hermes:gaps:XAU_USD:v1"]
    rb = _FakeRedis()
    rb.kv["hermes:gaps:XAU_USD:v1"] = r.kv["hermes:gaps:XAU_USD:v1"]
    bk = bfs.BackfillStatusPublisher(redis_client=rb, records=recs).publish(now=NOW)["keys"]
    assert bk == ["hermes:backfill:status:XAU_USD:v1"]
    for s in NON_COHORT:
        assert f"hermes:gaps:{s}:v1" not in r.kv and f"hermes:backfill:status:{s}:v1" not in rb.kv

    # non-vacuity: enable a capability on an incomplete inactive row -> the whole registry load fails closed
    bad = production_shape_rows()
    for row in bad:
        if row["symbol"] == "XPT_USD":
            row["gap_detection_enabled"] = 1          # activate a capability but leave NULL market_hours_policy etc.
    with pytest.raises(reg.RegistryError, match="INCOMPLETE-ACTIVE"):
        reg.load_registry(bad)


def test_registry_module_has_no_ticker_exemption():
    import re
    src = (ROOT / "utils/hermes_instrument_registry_v1.py").read_text()
    code = re.sub(r'"""[\s\S]*?"""', "", src)
    code = "\n".join(re.sub(r"#.*$", "", l) for l in code.splitlines())
    for ticker in ("XPT_USD", "XCU_USD", "USD_CHF", "USD_CAD", "NZD_USD", "EUR_GBP", "XAU_USD", "SPX500_USD", "WTICO_USD"):
        assert ticker not in code, f"registry validation must not name {ticker} (no ticker-specific exemption)"
