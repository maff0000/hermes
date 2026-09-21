"""HMT-2B — static no-network guard for market_truth/acquisition/.

Mirrors the HMT-1 pattern (tests/hmt1/test_fixture_provider.py docstring; tests/hmt1/
test_fail_closed.py's forbidden-token static guard) and HMT-2A's own version of this same file.

HMT-2A's version of this file asserted that no provider adapter / source-store / lineage module
existed at all, because HMT-2A's acceptance criteria required zero network access and zero real
provider dependency anywhere in the package. HMT-2B genuinely adds a real (metadata-only) vendor
adapter, so this file is EXTENDED, not weakened: the guard now permits exactly one controlled
import site for the `databento` vendor SDK — `providers/databento_historical.py` — and continues
to forbid every genuine network-capable module (sockets/HTTP/DB-driver libraries) and every OTHER
vendor SDK, in every file in this package, including that one.
"""
import ast
import re
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent.parent / "market_truth" / "acquisition"

# Any import of these (or a submodule of these) would indicate a network/provider dependency
# that has no place in this checkpoint's calendar/selection/manifest/source-store/lineage code.
# `databento` is deliberately NOT listed here — it is checked separately below, with a single
# named exception for the one adapter file authorised to import it.
_FORBIDDEN_MODULE_ROOTS = (
    "socket",
    "requests",
    "urllib",
    "urllib2",
    "http",
    "httpx",
    "aiohttp",
    "ftplib",
    "smtplib",
    "telnetlib",
    "asyncio",  # not needed for pure sync stdlib logic; its presence would be a scope smell
    "oandapyV20",
    "ib_insync",
    "redis",
    "pymysql",
    "PyMySQL",
)

# The ONLY file in this package authorised to import the `databento` vendor SDK (HMT-2B Part 1).
_DATABENTO_IMPORT_ALLOWED_RELATIVE_PATH = Path("providers") / "databento_historical.py"

# Files legitimately concerned with the real Databento credential (parameter/variable names,
# never a hardcoded value — see the stricter check further down) are excluded from the blanket
# credential-shaped-token scan that still applies to every other file in this package.
_CREDENTIAL_TOKEN_SCAN_EXEMPT_RELATIVE_PATHS = {_DATABENTO_IMPORT_ALLOWED_RELATIVE_PATH}


def _python_files():
    return sorted(PACKAGE_DIR.rglob("*.py"))


def test_package_exists_and_has_expected_modules():
    names = {p.name for p in _python_files()}
    # HMT-2A modules — unchanged, still present.
    assert "session_calendar.py" in names
    assert "corpus_selection.py" in names
    assert "corpus_manifest.py" in names
    # HMT-2B modules — new in this checkpoint.
    assert "source_store.py" in names
    assert "lineage.py" in names
    assert (PACKAGE_DIR / "providers" / "databento_historical.py").exists()
    assert (PACKAGE_DIR / "providers" / "__init__.py").exists()
    # Still out of scope for HMT-2B — genuine future checkpoints only.
    for forbidden_name in ("quote.py", "quality.py"):
        assert forbidden_name not in names, f"{forbidden_name} is out of scope for HMT-2B"


def test_no_forbidden_network_or_provider_imports():
    for path in _python_files():
        relative = path.relative_to(PACKAGE_DIR)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in _FORBIDDEN_MODULE_ROOTS, f"{path}: forbidden import {alias.name!r}"
                    if root == "databento":
                        assert relative == _DATABENTO_IMPORT_ALLOWED_RELATIVE_PATH, (
                            f"{path}: 'databento' may only be imported by "
                            f"{_DATABENTO_IMPORT_ALLOWED_RELATIVE_PATH}"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue
                root = node.module.split(".")[0]
                assert root not in _FORBIDDEN_MODULE_ROOTS, f"{path}: forbidden import-from {node.module!r}"
                if root == "databento":
                    assert relative == _DATABENTO_IMPORT_ALLOWED_RELATIVE_PATH, (
                        f"{path}: 'databento' may only be imported by "
                        f"{_DATABENTO_IMPORT_ALLOWED_RELATIVE_PATH}"
                    )


def test_no_bulk_download_or_live_streaming_call_sites():
    """HMT-2B Part 1 absolute scope boundary: no bulk historical-data-download method
    (`timeseries.get_range` or equivalent, for ANY schema) and no live-streaming client of any
    kind may ever be CALLED or CONSTRUCTED anywhere in this package.

    This is an AST-based check (not a plain text/substring scan) so that documenting WHY these
    are forbidden — which necessarily requires naming them in docstrings/comments, exactly as
    `providers/databento_historical.py`'s module docstring and `download_historical_range()`'s
    own docstring do — never trips this guard. Only genuine `ast.Call`/`ast.Attribute` nodes
    are inspected; string literals (docstrings, comments-as-text) are not `Call`/`Attribute`
    nodes and are structurally invisible to this scan.

    `download_historical_range()` itself is permitted to exist ONLY as a stub whose body
    unconditionally raises — it contains no `.get_range(...)` call of its own, so it passes this
    guard trivially; if a future edit ever made it actually call a `get_range`-named method, this
    test would catch that regression.
    """
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr != "get_range", f"{path}: forbidden `.get_range(...)`-shaped attribute access"
                assert node.attr != "Live", f"{path}: forbidden live-streaming client attribute access (`.Live`)"


def test_no_credential_looking_tokens_in_source():
    """Cheap defence-in-depth: outside the one exempt adapter file (which legitimately names an
    `api_key` parameter/variable — never a hardcoded value, see the stricter check below), no
    file in this package should contain any credential-shaped token at all."""
    forbidden_substrings = ("api_key", "apikey", "secret_key", "access_token", "password=")
    for path in _python_files():
        relative = path.relative_to(PACKAGE_DIR)
        if relative in _CREDENTIAL_TOKEN_SCAN_EXEMPT_RELATIVE_PATHS:
            continue
        lowered = path.read_text(encoding="utf-8").lower()
        for token in forbidden_substrings:
            assert token not in lowered, f"{path}: credential-shaped token {token!r} found"


def test_databento_adapter_never_hardcodes_a_credential_value():
    """The one exempt file is still held to a strict rule: `api_key` (or any of the other
    credential-shaped tokens) may appear as a parameter/variable/attribute NAME, but never as
    the left-hand side of an assignment to a quoted string literal — that would be a hardcoded
    credential, which is never permitted anywhere in this repository."""
    adapter_path = PACKAGE_DIR / _DATABENTO_IMPORT_ALLOWED_RELATIVE_PATH
    text = adapter_path.read_text(encoding="utf-8")
    hardcoded_assignment = re.compile(
        r"(api_key|apikey|secret_key|access_token)\s*[:=]\s*['\"][^'\"]{4,}['\"]", re.IGNORECASE
    )
    matches = hardcoded_assignment.findall(text)
    assert not matches, f"{adapter_path}: apparent hardcoded credential literal(s): {matches!r}"
