"""HMT-2 canonicalisation checkpoint — static, structural proof that
`market_truth.acquisition.canonical_worker` (the durable canonicalisation pipeline, WO Part 4)
never touches any network-capable or vendor-SDK-capable code path directly.

Mirrors `tests/hmt2/test_no_network_guard.py` / `tests/hmt2/test_mbp1_pilot_replay_script_guard.
py`'s established AST-based pattern exactly: real, committed source is inspected structurally
(`ast.Import`/`ast.ImportFrom`/`ast.Attribute` nodes), never a docstring, comment, or runtime
side-effect. This module also already lives inside `market_truth/acquisition/` and is therefore
automatically covered by `tests/hmt2/test_no_network_guard.py`'s own package-wide scan (which
forbids importing `databento` from anywhere except `providers/databento_historical.py`) — this
file is the EXPLICIT, additional, WO-required guard naming the canonical worker specifically, so
the guarantee does not depend on where this module happens to live in the package.
"""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CANONICAL_WORKER_MODULE = REPO_ROOT / "market_truth" / "acquisition" / "canonical_worker.py"

_FORBIDDEN_IMPORT_ROOTS = {
    "socket", "requests", "urllib", "urllib2", "http", "httpx", "aiohttp",
    "ftplib", "smtplib", "telnetlib", "databento",
}

# The one network-capable / credential-touching acquisition surface anywhere in this codebase —
# checked as real AST identifiers (Name/Attribute nodes), never a raw substring-of-source-text
# scan, so this module's own honest docstring (which legitimately NAMES what it never calls) can
# never trip this guard.
_FORBIDDEN_NETWORK_CAPABLE_NAMES = frozenset(
    {
        "Historical", "load_databento_api_key", "DatabentoHistoricalProvider",
        "acquire_mbp1_pilot_session_data", "acquire_reference_series_ohlcv1h", "acquire_gc_definitions",
        "get_cost_estimate", "get_record_count_estimate", "get_billable_size_estimate",
    }
)


def _parse() -> ast.Module:
    return ast.parse(CANONICAL_WORKER_MODULE.read_text(encoding="utf-8"), filename=str(CANONICAL_WORKER_MODULE))


def _referenced_names(tree: ast.Module) -> set:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_canonical_worker_module_exists():
    assert CANONICAL_WORKER_MODULE.exists()


def test_canonical_worker_never_imports_databento_or_any_network_capable_module():
    tree = _parse()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in _FORBIDDEN_IMPORT_ROOTS, f"{CANONICAL_WORKER_MODULE}: forbidden import {alias.name!r}"
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            root = node.module.split(".")[0]
            assert root not in _FORBIDDEN_IMPORT_ROOTS, f"{CANONICAL_WORKER_MODULE}: forbidden import-from {node.module!r}"
            # The network-capable acquisition module itself must never be imported here at all —
            # only market_truth.providers.databento_mbp1 (the read-only adapter) may reach the
            # one governed decode helper, and only for that.
            assert node.module != "market_truth.acquisition.providers.databento_historical", (
                f"{CANONICAL_WORKER_MODULE}: must never import the network-capable acquisition "
                f"module directly"
            )


def test_canonical_worker_never_references_a_network_capable_acquisition_token():
    tree = _parse()
    found = _referenced_names(tree) & _FORBIDDEN_NETWORK_CAPABLE_NAMES
    assert not found, f"{CANONICAL_WORKER_MODULE}: forbidden network-capable identifier(s) referenced: {found!r}"


def test_canonical_worker_never_calls_get_range_or_live_streaming():
    tree = _parse()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr != "get_range", f"{CANONICAL_WORKER_MODULE}:{node.lineno}: forbidden `.get_range(...)` access"
            assert node.attr != "Live", f"{CANONICAL_WORKER_MODULE}:{node.lineno}: forbidden `.Live` access"


def test_canonical_worker_only_reaches_the_one_read_only_provider_adapter():
    """The only databento-adjacent module this worker may import from is
    `market_truth.providers.databento_mbp1` (the HMT-2 pilot's own read-only, zero-vendor-import
    adapter) — never the network-capable acquisition module, never the vendor SDK itself."""
    tree = _parse()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "market_truth.providers.databento_mbp1":
            imported_names = {alias.name for alias in node.names}
            assert imported_names <= {"DatabentoMbp1PilotProvider", "DATABENTO_MBP1_PROVIDER_VERSION"}, (
                f"{CANONICAL_WORKER_MODULE}: unexpected import(s) from databento_mbp1: {imported_names!r}"
            )
