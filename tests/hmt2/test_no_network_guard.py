"""HMT-2A — static no-network guard for market_truth/acquisition/.

Mirrors the HMT-1 pattern (tests/hmt1/test_fixture_provider.py docstring; tests/hmt1/
test_fail_closed.py's forbidden-token static guard): prove, by inspecting the source text
itself, that nothing in this checkpoint's new package ever imports a networking-capable
module. HMT-2A's acceptance criteria require zero network access and zero real provider
dependency — this test makes that a structurally-enforced, CI-checked fact rather than a
claim.
"""
import ast
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent.parent / "market_truth" / "acquisition"

# Any import of these (or a submodule of these) would indicate a network/provider dependency
# that has no place in HMT-2A's pure calendar/selection/manifest code.
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
    "databento",
    "oandapyV20",
    "ib_insync",
    "redis",
    "pymysql",
    "PyMySQL",
)


def _python_files():
    return sorted(PACKAGE_DIR.rglob("*.py"))


def test_package_exists_and_has_expected_modules():
    names = {p.name for p in _python_files()}
    assert "session_calendar.py" in names
    assert "corpus_selection.py" in names
    assert "corpus_manifest.py" in names
    # HMT-2B/2C scope — must NOT exist in this checkpoint.
    for forbidden_name in ("quote.py", "source_store.py", "lineage.py", "quality.py"):
        assert forbidden_name not in names, f"{forbidden_name} is out of scope for HMT-2A"
    assert not (PACKAGE_DIR / "providers").exists(), "no provider adapter directory is authorised in HMT-2A"


def test_no_forbidden_network_or_provider_imports():
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in _FORBIDDEN_MODULE_ROOTS, (
                        f"{path}: forbidden import {alias.name!r}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue
                root = node.module.split(".")[0]
                assert root not in _FORBIDDEN_MODULE_ROOTS, (
                    f"{path}: forbidden import-from {node.module!r}"
                )


def test_no_credential_looking_tokens_in_source():
    """Cheap defence-in-depth: this checkpoint has no credentials of any kind, so none of the
    common credential-shaped tokens should appear anywhere in the new package's source."""
    forbidden_substrings = ("api_key", "apikey", "secret_key", "access_token", "password=")
    for path in _python_files():
        lowered = path.read_text(encoding="utf-8").lower()
        for token in forbidden_substrings:
            assert token not in lowered, f"{path}: credential-shaped token {token!r} found"
