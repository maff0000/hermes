"""Registry-driven Advanced-v1 selection: parity, 7-new inactivity, generic-key byte-parity, onboarding.

WO-HELM-HERMES-ADVANCED-V1-XAU-REGISTRY-REWIRE-0001. Registry-driven, parameterised; no per-ticker test.
"""
import copy
import pathlib
import re

import pytest

from utils.hermes_instrument_registry_v1 import load_registry, load_from_db, RegistryError
from utils import hermes_advanced_v1_selection_v1 as sel

# reuse the foundation test's fixture builder
from tests.test_hermes_instrument_registry_v1 import rollout_rows, _row

SEVEN_NEW = ("XAG_USD", "EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY", "SPX500_USD", "WTICO_USD")


def _recs():
    return load_registry(rollout_rows())


@pytest.mark.parametrize("capability", ["tick", "indicator", "gap"])
def test_selection_is_xau_only_today(capability):
    assert sel.selection_for(capability, _recs()) == ("XAU_USD",)


def test_backfill_status_selection_is_xau_only():
    assert sel.backfill_status_instruments(_recs()) == ("XAU_USD",)


@pytest.mark.parametrize("capability", ["tick", "indicator", "gap"])
def test_seven_new_are_inactive(capability):
    active = set(sel.selection_for(capability, _recs()))
    for s in SEVEN_NEW:
        assert s not in active, f"{s} must be NOT_ENABLED for {capability}"


def test_no_capability_defaults_enabled():
    # a registry with all flags off -> empty selection (never a default-on)
    rows = [_row("XAU_USD", "precious_metals", 3, 0.001, "metals")]  # caps default 0
    recs = load_registry(rows)
    for cap in ("tick", "indicator", "gap"):
        assert sel.selection_for(cap, recs) == ()


# ---- generic key factory is byte-identical to the EXISTING XAU key strings (parity) -----------------
def test_indicator_key_parity_with_existing_module():
    from utils.hermes_indicators_v1 import indicator_key as legacy
    for tf in ("M1", "M5", "M15", "H1", "H4"):
        assert sel.indicator_key("XAU_USD", tf) == legacy("XAU_USD", tf)


def test_gaps_key_parity_with_existing_module():
    # WO-...-XAU-MODULE-ADOPTION-0001: the gaps module now derives its per-instrument key from the seam; parity is proven
    # against the byte-identical XAU key it emitted as its former single aggregate.
    from utils import hermes_gaps_v1 as g
    assert sel.gaps_key("XAU_USD") == g.gaps_key("XAU_USD") == "hermes:gaps:XAU_USD:v1"


def test_backfill_status_key_parity_with_existing_module():
    from utils import hermes_backfill_status_v1 as b
    assert sel.backfill_status_key("XAU_USD") == b.backfill_status_key("XAU_USD") == "hermes:backfill:status:XAU_USD:v1"


def test_tick_key_parity_with_existing_module():
    from utils.tick_contract_v1 import canonical_key as legacy
    assert sel.tick_latest_key("XAU_USD") == legacy("XAU_USD")


def test_parity_ok_helper():
    assert sel.parity_ok("indicator", _recs(), ["XAU_USD"]) is True
    assert sel.parity_ok("indicator", _recs(), ["XAU_USD", "EUR_USD"]) is False


# ---- fail-closed + registry-driven onboarding -------------------------------------------------------
def test_registry_unavailable_fails_closed():
    with pytest.raises(RegistryError):
        sel.load_selection(fetch=lambda: (_ for _ in ()).throw(RuntimeError("db down")))


def test_load_selection_shape():
    s = sel.load_selection(fetch=lambda: rollout_rows())
    assert s["tick"] == ("XAU_USD",) and s["indicator"] == ("XAU_USD",) and s["gap"] == ("XAU_USD",)
    assert s["backfill_status"] == ("XAU_USD",)


def test_synthetic_instrument_activates_by_data_only():
    # enabling a NEW instrument's capability flags in the REGISTRY (data only) adds it to selection — no code.
    rows = rollout_rows()
    rows.append(_row("TST_USD", "forex_major", 5, 0.00001, "fx_24x5", tick_cap=1, ind_cap=1, gap_cap=1))
    s = sel.load_selection(fetch=lambda: copy.deepcopy(rows))
    assert s["tick"] == ("TST_USD", "XAU_USD") and s["indicator"] == ("TST_USD", "XAU_USD")
    # disabling the flags (data only) removes it
    rows[-1].update(tick_contract_enabled=0, indicator_contract_enabled=0, gap_detection_enabled=0)
    s2 = sel.load_selection(fetch=lambda: copy.deepcopy(rows))
    assert "TST_USD" not in s2["tick"] and "TST_USD" not in s2["indicator"]


# ---- static architecture scan: the NEW selection seam has zero XAU authority / ticker logic ----------
def test_selection_module_has_no_ticker_authority():
    src = pathlib.Path(sel.__file__).read_text()
    assert 'CANONICAL_INSTRUMENT = "XAU_USD"' not in src
    assert not re.search(r'if\s+instrument\s*==', src)
    assert not re.search(r'if\s+symbol\s*==', src)
    # no hard-coded rollout symbol array in the seam
    assert not re.search(r'=\s*\[\s*"XAU_USD"\s*,\s*"XAG_USD"', src)
    # no capability defaulting to True
    assert "= True" not in src.replace("is True", "")
