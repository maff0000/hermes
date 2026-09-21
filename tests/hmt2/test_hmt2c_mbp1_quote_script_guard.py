"""HMT-2 (real-money checkpoint, MBP-1 quote resolution) — static no-bulk-download guard for
research/hmt2/hmt2c_mbp1_quote.py.

Extends the HMT-2B.1 no-bulk-download-guard pattern
(tests/hmt2/test_hmt2b1_quote_reproduction_script_guard.py) to this new call site: the MBP-1
quote-resolution script also lives under research/hmt2/, outside `market_truth/acquisition/`'s
own guarded PACKAGE_DIR (tests/hmt2/test_no_network_guard.py), so it needs its own guard rather
than a change to that file's scan root.

Absolute requirement (this checkpoint): this script may call ONLY the three free metadata-
estimate methods (`get_cost_estimate`, `get_record_count_estimate`, `get_billable_size_estimate`)
— it must never call `download_historical_range()`, `acquire_reference_series_ohlcv1h()`, or
`acquire_gc_definitions()` (the only two real bulk-acquisition methods that exist anywhere in
this codebase), never access a `.get_range`-shaped attribute, and never reference `.Live`
(live-streaming client) anywhere. This is the exact "quote-only, provably" guarantee the WO
requires for the final MBP-1 quote step — no MBP-1 (or any other schema's) data is ever
acquired by this file.
"""
import ast
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "research"
    / "hmt2"
    / "hmt2c_mbp1_quote.py"
)

# Every real bulk-acquisition method that exists anywhere in this codebase (see
# market_truth/acquisition/providers/databento_historical.py) — this script may call NEITHER.
_FORBIDDEN_ACQUISITION_METHOD_NAMES = frozenset(
    {"download_historical_range", "acquire_reference_series_ohlcv1h", "acquire_gc_definitions"}
)


def test_quote_script_exists():
    assert SCRIPT_PATH.exists()


def test_quote_script_never_calls_get_range_or_live_streaming():
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"), filename=str(SCRIPT_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr != "get_range", f"{SCRIPT_PATH}: forbidden `.get_range(...)`-shaped attribute access"
            assert node.attr != "Live", f"{SCRIPT_PATH}: forbidden live-streaming client attribute access (`.Live`)"


def test_quote_script_never_calls_any_real_bulk_acquisition_method():
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"), filename=str(SCRIPT_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in _FORBIDDEN_ACQUISITION_METHOD_NAMES, (
                f"{SCRIPT_PATH}: forbidden acquisition call `{node.func.attr}(...)` — this "
                f"script is quote-only"
            )


def test_quote_script_only_calls_the_three_authorised_estimate_methods():
    """Every `provider.<method>(...)` call site in this script must be one of the three
    authorised free metadata-estimate methods — nothing else."""
    allowed = {"get_cost_estimate", "get_record_count_estimate", "get_billable_size_estimate"}
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"), filename=str(SCRIPT_PATH))
    found_calls = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "provider"
        ):
            found_calls.add(node.func.attr)
    assert found_calls, "expected at least one provider.<method>(...) call in the quote script"
    assert found_calls <= allowed, f"unauthorised provider method call(s): {found_calls - allowed!r}"


def test_quote_script_never_imports_a_network_capable_module_other_than_the_provider_module():
    """This script may import `market_truth.acquisition.providers.databento_historical` (which
    itself is the one file allowed to import the `databento` SDK), plus the pure
    `mbp1_quote_request`/`session_calendar` modules — never `databento` directly, and never any
    other network-capable library."""
    forbidden_roots = {
        "databento", "socket", "requests", "urllib", "urllib2", "http", "httpx", "aiohttp",
        "ftplib", "smtplib", "telnetlib",
    }
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"), filename=str(SCRIPT_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden_roots, (
                    f"{SCRIPT_PATH}: forbidden direct import {alias.name!r}"
                )
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            assert node.module.split(".")[0] not in forbidden_roots, (
                f"{SCRIPT_PATH}: forbidden direct import-from {node.module!r}"
            )
