"""Reusable instrument-registry loader tests + data-only onboarding proof.

WO-HELM-HERMES-ADVANCED-V1-REGISTRY-FOUNDATION-0001. No DB dependency: rows are injected. Cases derive from
registry fixtures (never one test per ticker).
"""
import copy
import pytest

from utils.hermes_instrument_registry_v1 import (
    InstrumentRecord, RegistryError, validate_record, load_registry, enabled_instruments,
    load_from_db, consistency_check, SUPPORTED_TIMEFRAMES,
)

TF = list(SUPPORTED_TIMEFRAMES)


def _row(symbol, category, precision, tick, mhp, freshness=120, enabled=1, oanda=1,
         broker=None, tick_cap=0, ind_cap=0, gap_cap=0, tfs=None, backfill="status_only"):
    return {
        "symbol": symbol, "broker_symbol": broker, "name": symbol, "category": category,
        "enabled": enabled, "oanda_compatible": oanda, "price_precision": precision, "tick_size": tick,
        "price_authority": "mid", "market_hours_policy": mhp, "expected_freshness_sec": freshness,
        "enabled_timeframes": tfs if tfs is not None else TF, "indicator_profile": "standard_v1",
        "tick_contract_enabled": tick_cap, "indicator_contract_enabled": ind_cap,
        "gap_detection_enabled": gap_cap, "backfill_policy": backfill, "retention_policy": "default_v1",
        "metadata_version": "v1",
    }


def rollout_rows():
    """The 8-instrument rollout: XAU is the active pilot (caps on); the 7 new stay NOT_ENABLED (caps off)."""
    return [
        _row("XAU_USD", "precious_metals", 3, 0.001, "metals", tick_cap=1, ind_cap=1, gap_cap=1),
        _row("XAG_USD", "precious_metals", 4, 0.0001, "metals"),
        _row("EUR_USD", "forex_major", 5, 0.00001, "fx_24x5"),
        _row("GBP_USD", "forex_major", 5, 0.00001, "fx_24x5"),
        _row("AUD_USD", "forex_major", 5, 0.00001, "fx_24x5"),
        _row("USD_JPY", "forex_major", 3, 0.001, "fx_24x5"),
        _row("SPX500_USD", "indices", 1, 0.1, "index_cash", freshness=300),
        _row("WTICO_USD", "energy", 3, 0.001, "energy", freshness=300),
    ]


def test_loads_all_eight_enabled_sorted():
    recs = load_registry(rollout_rows())
    assert enabled_instruments(recs) == (
        "AUD_USD", "EUR_USD", "GBP_USD", "SPX500_USD", "USD_JPY", "WTICO_USD", "XAG_USD", "XAU_USD",
    )


@pytest.mark.parametrize("instrument", [r["symbol"] for r in rollout_rows()])
def test_each_enabled_instrument_has_valid_typed_metadata(instrument):
    recs = {r.symbol: r for r in load_registry(rollout_rows())}
    r = recs[instrument]
    assert isinstance(r, InstrumentRecord)
    assert r.price_precision >= 0 and r.tick_size > 0
    assert r.market_hours_policy in {"metals", "fx_24x5", "index_cash", "energy"}
    assert set(r.enabled_timeframes) <= set(SUPPORTED_TIMEFRAMES)


def test_wtico_is_energy():
    recs = {r.symbol: r for r in load_registry(rollout_rows())}
    assert recs["WTICO_USD"].category == "energy"


def test_only_xau_pilot_active_seven_new_not_enabled():
    recs = {r.symbol: r for r in load_registry(rollout_rows())}
    assert recs["XAU_USD"].capabilities == {"tick_contract_enabled": True,
                                            "indicator_contract_enabled": True,
                                            "gap_detection_enabled": True}
    for s in ("XAG_USD", "EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY", "SPX500_USD", "WTICO_USD"):
        assert recs[s].capabilities == {"tick_contract_enabled": False,
                                        "indicator_contract_enabled": False,
                                        "gap_detection_enabled": False}, f"{s} must be NOT_ENABLED"


# ---- fail-closed validation cases (one parameterised test, not one per ticker) ----------------------
def test_duplicate_symbol_rejected():
    rows = rollout_rows() + [_row("XAU_USD", "precious_metals", 3, 0.001, "metals")]
    with pytest.raises(RegistryError, match="duplicate canonical symbol"):
        load_registry(rows)


def test_duplicate_broker_mapping_rejected():
    rows = [_row("EUR_USD", "forex_major", 5, 0.00001, "fx_24x5", broker="X"),
            _row("GBP_USD", "forex_major", 5, 0.00001, "fx_24x5", broker="X")]
    with pytest.raises(RegistryError, match="duplicate broker mapping"):
        load_registry(rows)


@pytest.mark.parametrize("mutate,err", [
    (lambda r: r.update(category="not_a_category"), "unsupported asset category"),
    (lambda r: r.update(price_precision=-1), "price_precision out of range"),
    (lambda r: r.update(tick_size=0), "tick_size must be > 0"),
    (lambda r: r.update(market_hours_policy="mars"), "unknown market_hours_policy"),
    (lambda r: r.update(expected_freshness_sec=0), "expected_freshness_sec must be > 0"),
    (lambda r: r.update(enabled_timeframes=["M2"]), "unsupported timeframe"),
    (lambda r: r.update(enabled_timeframes=[]), "non-empty list"),
    (lambda r: r.update(price_authority="oops"), "invalid price_authority"),
    (lambda r: r.update(backfill_policy="whatever"), "invalid backfill_policy"),
    # a capability-ACTIVE row missing a required capability field still fails closed (strict validation preserved)
    (lambda r: r.pop("market_hours_policy"), "INCOMPLETE-ACTIVE"),
])
def test_invalid_metadata_fails_closed(mutate, err):
    # capability-ACTIVE row (tick_cap=1): present-but-invalid capability metadata fails, and a missing required
    # capability field fails closed under capability-aware validation. Inactive-row tolerance is tested separately.
    row = _row("EUR_USD", "forex_major", 5, 0.00001, "fx_24x5", tick_cap=1)
    mutate(row)
    with pytest.raises(RegistryError, match=err):
        validate_record(row)


def test_sql_unavailable_fails_closed_no_fallback():
    def boom():
        raise RuntimeError("db down")
    with pytest.raises(RegistryError, match="fail-closed, no fallback"):
        load_from_db(fetch=boom)


def test_empty_registry_refused_not_defaulted():
    with pytest.raises(RegistryError, match="no enabled instruments"):
        load_registry([_row("EUR_USD", "forex_major", 5, 0.00001, "fx_24x5", enabled=0)])


def test_env_instruments_is_validator_not_authority():
    recs = load_registry(rollout_rows())
    consistency_check(recs, ["XAU_USD", "EUR_USD"])  # subset ok
    with pytest.raises(RegistryError, match="not enabled in the canonical registry"):
        consistency_check(recs, ["XAU_USD", "DOGE_USD"])  # env names a non-registry instrument


# ---- DATA-ONLY ONBOARDING PROOF (registry record only; zero code change) ----------------------------
def test_add_one_instrument_is_data_only():
    base = rollout_rows()
    before = enabled_instruments(load_from_db(fetch=lambda: copy.deepcopy(base)))
    assert "TST_USD" not in before

    # 1) add ONE registry record (data only) — a synthetic supported instrument
    synthetic = _row("TST_USD", "forex_major", 5, 0.00001, "fx_24x5")
    with_new = base + [synthetic]

    # 2..8) it is AUTOMATICALLY discovered, enumerated, typed — with NO code change
    recs = load_from_db(fetch=lambda: copy.deepcopy(with_new))
    assert "TST_USD" in enabled_instruments(recs)
    rec = {r.symbol: r for r in recs}["TST_USD"]
    assert rec.capabilities == {"tick_contract_enabled": False, "indicator_contract_enabled": False,
                                "gap_detection_enabled": False}  # inactive until data flips a flag

    # a parameterised suite iterating enabled_instruments() would now include it automatically:
    assert all(isinstance(s, str) for s in enabled_instruments(recs))

    # 9) disable via the SAME registry record (data only)
    disabled = copy.deepcopy(with_new)
    disabled[-1]["enabled"] = 0
    recs2 = load_from_db(fetch=lambda: disabled)
    assert "TST_USD" not in enabled_instruments(recs2)

    # 10) remove the record entirely -> clean disappearance, no stale authority
    recs3 = load_from_db(fetch=lambda: copy.deepcopy(base))
    assert enabled_instruments(recs3) == before


# ---- §17 health foundation: registry component state -------------------------------------------------
def test_registry_component_state_loaded_and_seven_new_not_enabled():
    from utils.hermes_instrument_registry_v1 import registry_component_state
    st = registry_component_state(fetch=lambda: rollout_rows())
    assert st["state"] == "LOADED" and st["fault"] is None and st["enabled_count"] == 8
    assert st["instruments"]["XAU_USD"]["capabilities"] == {
        "tick_contract_enabled": "ACTIVE", "indicator_contract_enabled": "ACTIVE",
        "gap_detection_enabled": "ACTIVE"}
    for s in ("XAG_USD", "EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY", "SPX500_USD", "WTICO_USD"):
        assert set(st["instruments"][s]["capabilities"].values()) == {"NOT_ENABLED"}
    assert st["instruments"]["WTICO_USD"]["category"] == "energy"


def test_registry_component_state_unavailable_fails_closed():
    from utils.hermes_instrument_registry_v1 import registry_component_state
    st = registry_component_state(fetch=lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    assert st["state"] == "UNAVAILABLE" and st["enabled_count"] == 0 and st["instruments"] == {}
