"""Static architecture scan for the adoption-support modules (§22). The per-module rewires will be added to
ADOPTED_MODULES as each lands; today the readiness guard + seam must be clean of XAU authority patterns."""
import pathlib
import re

ADOPTED_SUPPORT = [
    "utils/hermes_advanced_v1_selection_v1.py",
    "utils/hermes_advanced_v1_readiness_v1.py",
    "utils/hermes_instrument_registry_v1.py",
    "utils/tick_live_emitter_v1.py",       # ADOPTED
    "utils/hermes_indicators_v1.py",       # ADOPTED
    "utils/hermes_gaps_v1.py",             # ADOPTED
    "utils/hermes_backfill_status_v1.py",  # ADOPTED
    "utils/hermes_feed_health_v1.py",      # ADOPTED
    "utils/hermes_market_hours_policy_v1.py",  # ADOPTED (metadata-driven market-hours authority)
    "utils/hermes_advanced_v1_publication_gate_v1.py",  # ADOPTED (central master/scope publication gate; pilot scope is DATA)
]
# Cohort tickers that must never appear as behavioural authority in production market-hours/policy logic.
_COHORT_TICKERS = ("XAU_USD", "XAG_USD", "EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY", "SPX500_USD", "WTICO_USD")
ROOT = pathlib.Path(__file__).resolve().parents[1]


def _code_only(src: str) -> str:
    # drop docstrings + line comments so prose describing prohibitions is not a false positive
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    src = re.sub(r"'''[\s\S]*?'''", "", src)
    return "\n".join(re.sub(r"#.*$", "", l) for l in src.splitlines())


import pytest


@pytest.mark.parametrize("mod", ADOPTED_SUPPORT)
def test_no_xau_authority_or_ticker_branch(mod):
    code = _code_only((ROOT / mod).read_text())
    assert 'CANONICAL_INSTRUMENT = "XAU_USD"' not in code, mod
    assert not re.search(r'if\s+instrument\s*==', code), mod
    assert not re.search(r'if\s+symbol\s*==', code), mod
    assert not re.search(r'=\s*\[\s*"XAU_USD"\s*,\s*"XAG_USD"', code), mod  # no hard-coded rollout array


def test_market_hours_policy_module_has_no_ticker_or_uniform_default():
    """§14: the reusable market-hours policy module must resolve by governed policy key only — no ticker in policy
    logic, no ticker->policy dict, no asset-category->single-ticker behaviour, no silent uniform default, no
    host-local timezone call, no naive datetime comparison, no env-driven market-hours selection."""
    code = _code_only((ROOT / "utils/hermes_market_hours_policy_v1.py").read_text())
    for ticker in _COHORT_TICKERS:
        assert ticker not in code, f"cohort ticker {ticker} must not appear in policy logic"
    assert "SPX500_USD" not in code and "WTICO_USD" not in code
    assert "datetime.now(" not in code and ".today(" not in code           # no host-local 'now'
    assert "astimezone()" not in code                                       # no implicit host-local tz conversion
    assert "get_env" not in code and "os.environ" not in code               # no env-driven market-hours selection
    assert "ZoneInfo(" in code                                              # explicit IANA timezone (DST-correct)


def test_gaps_market_phase_is_policy_parametrised_not_uniform():
    """§9/§14: the adopted gap path resolves market hours through the policy (no hidden parallel uniform authority as
    the sole path). market_phase/_period_fully_open/classify_timeframe all accept a policy; the publisher resolves it."""
    code = _code_only((ROOT / "utils/hermes_gaps_v1.py").read_text())
    assert "def market_phase(dt, policy=None)" in code
    assert "def _period_fully_open(open_epoch, tf, policy=None)" in code
    assert "mhp.resolve_policy(" in code               # publisher resolves the reusable policy by registry key
    assert "market_hours_policy" in code               # contract records the governed policy key
