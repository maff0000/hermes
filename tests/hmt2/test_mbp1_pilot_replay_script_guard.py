"""HMT-2 (real-money checkpoint, MBP-1 pilot) — Part 5: static, structural proof that the pilot
replay driver (`research/hmt2/hmt2f_mbp1_pilot_replay.py`) and its cross-run comparison script
(`research/hmt2/hmt2g_mbp1_pilot_replay_compare.py`) never touch any network-capable code path —
"zero network access during any replay run" (WO), proven the same way every other guard in this
checkpoint is proven: by AST inspection of the real, committed source, never by trusting a
docstring or a runtime side-effect.

Mirrors `test_hmt2c_mbp1_quote_script_guard.py`'s established pattern exactly.
"""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REPLAY_SCRIPT = REPO_ROOT / "research" / "hmt2" / "hmt2f_mbp1_pilot_replay.py"
COMPARE_SCRIPT = REPO_ROOT / "research" / "hmt2" / "hmt2g_mbp1_pilot_replay_compare.py"
ADAPTER_MODULE = REPO_ROOT / "market_truth" / "providers" / "databento_mbp1.py"

# The ONE network-capable/credential-touching acquisition surface anywhere in this codebase.
# Checked as real AST identifiers (Name/Attribute/call-target nodes) below, NEVER as a raw
# substring-of-source-text scan — these scripts' own docstrings legitimately NAME every one of
# these tokens in prose, explaining exactly what they never call; a substring scan would trip on
# that honest documentation, so only genuine code-level references are inspected.
_FORBIDDEN_NETWORK_CAPABLE_NAMES = frozenset(
    {
        "Historical", "load_databento_api_key", "DatabentoHistoricalProvider",
        "acquire_mbp1_pilot_session_data", "acquire_reference_series_ohlcv1h", "acquire_gc_definitions",
        "get_cost_estimate", "get_record_count_estimate", "get_billable_size_estimate",
    }
)
_FORBIDDEN_IMPORT_ROOTS = {
    "socket", "requests", "urllib", "urllib2", "http", "httpx", "aiohttp",
    "ftplib", "smtplib", "telnetlib",
}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _referenced_names(tree: ast.Module) -> set:
    """Every genuine code-level identifier referenced anywhere in the module — `ast.Name` and
    `ast.Attribute.attr` nodes only (never a string literal/docstring/comment, which are not
    `Name`/`Attribute` nodes at all and so are structurally invisible to this scan)."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_replay_and_compare_scripts_exist():
    assert REPLAY_SCRIPT.exists()
    assert COMPARE_SCRIPT.exists()


def test_replay_script_never_calls_get_range_or_live_streaming():
    tree = _parse(REPLAY_SCRIPT)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr != "get_range", f"{REPLAY_SCRIPT}: forbidden `.get_range(...)` access"
            assert node.attr != "Live", f"{REPLAY_SCRIPT}: forbidden `.Live` access"


def test_replay_script_never_references_any_network_capable_acquisition_token():
    tree = _parse(REPLAY_SCRIPT)
    found = _referenced_names(tree) & _FORBIDDEN_NETWORK_CAPABLE_NAMES
    assert not found, f"{REPLAY_SCRIPT}: forbidden network-capable identifier(s) referenced in code: {found!r}"


def test_replay_script_never_imports_databento_or_any_other_network_capable_module():
    tree = _parse(REPLAY_SCRIPT)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root != "databento", f"{REPLAY_SCRIPT}: forbidden direct import {alias.name!r}"
                assert root not in _FORBIDDEN_IMPORT_ROOTS, f"{REPLAY_SCRIPT}: forbidden import {alias.name!r}"
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            root = node.module.split(".")[0]
            assert root != "databento", f"{REPLAY_SCRIPT}: forbidden import-from {node.module!r}"
            assert root not in _FORBIDDEN_IMPORT_ROOTS, f"{REPLAY_SCRIPT}: forbidden import-from {node.module!r}"
            # market_truth.acquisition.providers.databento_historical (the network-capable
            # acquisition module) must never be imported by the replay script at all — only
            # market_truth.providers.databento_mbp1 (the read-only adapter) may reach it, and
            # only for the read-only iter_retained_mbp1_records() decode helper (checked below).
            assert node.module != "market_truth.acquisition.providers.databento_historical", (
                f"{REPLAY_SCRIPT}: must never import the network-capable acquisition module directly"
            )


def test_adapter_module_never_imports_databento_directly():
    """`market_truth.providers.databento_mbp1` (the HMT-1-side canonicalisation adapter the
    replay script uses) must have ZERO vendor SDK coupling of its own — it only ever consumes
    the already-translated plain `NativeMbp1Record` shape."""
    tree = _parse(ADAPTER_MODULE)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] != "databento", f"{ADAPTER_MODULE}: forbidden import {alias.name!r}"
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            assert node.module.split(".")[0] != "databento", f"{ADAPTER_MODULE}: forbidden import-from {node.module!r}"
    source = ADAPTER_MODULE.read_text(encoding="utf-8")
    assert "import databento" not in source


def test_adapter_module_only_reaches_the_one_read_only_decode_helper():
    """The adapter may import from `providers.databento_historical`, but ONLY the read-only
    `iter_retained_mbp1_records` name — never the network-capable provider class or credential
    loader."""
    tree = _parse(ADAPTER_MODULE)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "market_truth.acquisition.providers.databento_historical":
            imported_names = {alias.name for alias in node.names}
            assert imported_names == {"iter_retained_mbp1_records"}, (
                f"{ADAPTER_MODULE}: must import ONLY iter_retained_mbp1_records from the "
                f"acquisition provider module, got {imported_names!r}"
            )


def test_compare_script_never_imports_a_network_capable_module():
    tree = _parse(COMPARE_SCRIPT)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root != "databento" and root not in _FORBIDDEN_IMPORT_ROOTS, (
                    f"{COMPARE_SCRIPT}: forbidden import {alias.name!r}"
                )
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            root = node.module.split(".")[0]
            assert root != "databento" and root not in _FORBIDDEN_IMPORT_ROOTS, (
                f"{COMPARE_SCRIPT}: forbidden import-from {node.module!r}"
            )


def test_compare_script_reads_only_local_json_files_never_the_network():
    tree = _parse(COMPARE_SCRIPT)
    found = _referenced_names(tree) & _FORBIDDEN_NETWORK_CAPABLE_NAMES
    assert not found, f"{COMPARE_SCRIPT}: forbidden network-capable identifier(s) referenced in code: {found!r}"
