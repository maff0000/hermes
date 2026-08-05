"""Static architecture scan for the adoption-support modules (§22). The per-module rewires will be added to
ADOPTED_MODULES as each lands; today the readiness guard + seam must be clean of XAU authority patterns."""
import pathlib
import re

ADOPTED_SUPPORT = [
    "utils/hermes_advanced_v1_selection_v1.py",
    "utils/hermes_advanced_v1_readiness_v1.py",
    "utils/hermes_instrument_registry_v1.py",
    "utils/tick_live_emitter_v1.py",   # ADOPTED (WO-...-XAU-MODULE-ADOPTION-0001)
]
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
