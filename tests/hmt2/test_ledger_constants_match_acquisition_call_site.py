"""HMT-2 (real-money checkpoint) — direct, automated proof that `research/hmt2/
hmt2h_gc_corpus_ledger.py`'s own `DATASET`/`SCHEMA`/`STYPE_IN` constants (redefined there rather
than imported, to keep that module a pure/network-free leaf — see its own module docstring)
never silently drift from the literal constants
`DatabentoHistoricalProvider.acquire_mbp1_pilot_session_data()` actually hardcodes at its one
`.get_range(...)` call site.

Mirrors `tests/hmt2/test_hardcoded_acquisition_call_sites.py`'s own AST-based, never-trust-a-
docstring discipline exactly.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ADAPTER_PATH = REPO_ROOT / "market_truth" / "acquisition" / "providers" / "databento_historical.py"


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ledger_mod = _load_module("hmt2h_gc_corpus_ledger", "research/hmt2/hmt2h_gc_corpus_ledger.py")


def _literal_kwarg(call: ast.Call, name: str) -> str:
    for kw in call.keywords:
        if kw.arg == name:
            assert isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
            return kw.value.value
    raise AssertionError(f"no keyword argument {name!r} found")


def test_ledger_dataset_schema_stype_in_match_the_real_call_site_literals():
    tree = ast.parse(ADAPTER_PATH.read_text(encoding="utf-8"), filename=str(ADAPTER_PATH))
    function_node = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "acquire_mbp1_pilot_session_data"
    )
    call = next(
        n for n in ast.walk(function_node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get_range"
    )
    assert ledger_mod.DATASET == _literal_kwarg(call, "dataset")
    assert ledger_mod.SCHEMA == _literal_kwarg(call, "schema")
    assert ledger_mod.STYPE_IN == _literal_kwarg(call, "stype_in")
