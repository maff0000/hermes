"""HMT-2B.1 — static no-bulk-download guard for research/hmt2/hmt2b1_reference_and_definition_quotes.py.

Extends the HMT-2B no-network-guard pattern (tests/hmt2/test_no_network_guard.py) to cover this
new call site: the WO Part 3/4 quote-reproduction script lives under research/hmt2/, outside
`market_truth/acquisition/`'s own guarded PACKAGE_DIR, so it needed its own guard rather than a
change to that file's scan root (which is specifically scoped to the acquisition package).

Absolute requirement (WO Part 3/4): this script may call ONLY the three free metadata-estimate
methods (`get_cost_estimate`, `get_record_count_estimate`, `get_billable_size_estimate`) — it
must never call `download_historical_range()`, never access a `.get_range`-shaped attribute, and
never reference `.Live` (live-streaming client) anywhere.
"""
import ast
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "research"
    / "hmt2"
    / "hmt2b1_reference_and_definition_quotes.py"
)


def test_quote_script_exists():
    assert SCRIPT_PATH.exists()


def test_quote_script_never_calls_get_range_or_live_streaming():
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"), filename=str(SCRIPT_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr != "get_range", f"{SCRIPT_PATH}: forbidden `.get_range(...)`-shaped attribute access"
            assert node.attr != "Live", f"{SCRIPT_PATH}: forbidden live-streaming client attribute access (`.Live`)"


def test_quote_script_never_calls_download_historical_range():
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"), filename=str(SCRIPT_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "download_historical_range", (
                f"{SCRIPT_PATH}: forbidden `download_historical_range(...)` call — Part 3/4 are quote-only"
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
