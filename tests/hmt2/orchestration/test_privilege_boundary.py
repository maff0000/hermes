"""Privilege-boundary proof: the canonical-orchestration code path has NO reference, anywhere,
to the Databento secret env var or any acquisition-identity credential path -- only the
diagnostics/acquisition-side code (and the acquisition launcher shell script) may reference it.
Mirrors the existing `tests/hmt2/test_no_network_guard.py` credential-token-scan pattern."""
import ast
from pathlib import Path

ORCHESTRATION_DIR = Path(__file__).resolve().parents[3] / "market_truth" / "acquisition" / "orchestration"
DIAGNOSTICS_DIR = Path(__file__).resolve().parents[3] / "market_truth" / "acquisition" / "diagnostics"
DEPLOYMENT_DIR = Path(__file__).resolve().parents[3] / "deployment" / "hmt2"

_SECRET_TOKENS = ("DATABENTO_HISTORICAL_API_KEY_PATH", "databento_historical_api_key", "load_databento_api_key")


def _orchestration_python_files():
    return sorted(ORCHESTRATION_DIR.rglob("*.py"))


def test_orchestration_package_exists_with_expected_modules():
    names = {p.name for p in _orchestration_python_files()}
    for expected in (
        "config.py", "host_guards.py", "chunk_allowlist.py", "exceptional_envelope.py",
        "quarantine.py", "retry.py", "canonical_root_orchestrator.py",
    ):
        assert expected in names


def test_canonical_orchestration_code_never_references_the_acquisition_secret():
    """No file under market_truth/acquisition/orchestration/ may contain the Databento secret
    env var name, the credential file basename, or the loader function name -- the canonical
    compute identity must never even be ABLE to accidentally read the acquisition credential,
    and this guard proves the source text gives it no path to do so."""
    for path in _orchestration_python_files():
        text = path.read_text(encoding="utf-8")
        for token in _SECRET_TOKENS:
            assert token not in text, f"{path}: forbidden acquisition-secret token {token!r} found"


def test_canonical_launcher_shell_script_never_references_the_secret():
    hmt2_run = DEPLOYMENT_DIR / "hmt2-run.sh"
    text = hmt2_run.read_text(encoding="utf-8")
    for token in ("DATABENTO_HISTORICAL_API_KEY_PATH", "HMT2_ACQUIRE_SECRET_PATH", "HMT2_ACQUIRE_SECRETS_GID"):
        assert token not in text, f"{hmt2_run}: forbidden acquisition-secret token {token!r} found"


def _non_comment_shell_lines(text: str):
    """Lines that are not blank and not a `#`-comment -- i.e. the ACTUAL invocation shape,
    excluding explanatory header comments (which legitimately name both flags when explaining
    why the two launchers differ)."""
    return [line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def test_acquisition_launcher_shell_script_is_the_one_place_that_does_reference_it():
    acquire_run = DEPLOYMENT_DIR / "hmt2-acquire-run.sh"
    text = acquire_run.read_text(encoding="utf-8")
    assert "DATABENTO_HISTORICAL_API_KEY_PATH" in text
    assert "HMT2_ACQUIRE_SECRETS_GID" in text
    # And its ACTUAL invocation must use --unit= (a service unit), never --scope -- the whole
    # reason SupplementaryGroups= actually takes effect. See the script's own header comment for
    # the full explanation (which legitimately names --scope too, so this checks executable
    # lines only, not the prose).
    code_lines = "\n".join(_non_comment_shell_lines(text))
    assert "--unit=" in code_lines
    assert "--scope" not in code_lines


def test_canonical_launcher_uses_scope_never_unit():
    """The inverse shape check: hmt2-run.sh's ACTUAL invocation must use --scope (cheaper, no
    secret access needed), never --unit=/SupplementaryGroups= -- if it ever gained those, it
    would be silently reproducing the acquisition launcher's own secret-bearing shape for
    canonical work."""
    text = (DEPLOYMENT_DIR / "hmt2-run.sh").read_text(encoding="utf-8")
    code_lines = "\n".join(_non_comment_shell_lines(text))
    assert "--scope" in code_lines
    assert "--unit=" not in code_lines
    assert "SupplementaryGroups" not in code_lines


def test_orchestrator_module_never_imports_the_databento_provider_module():
    """AST-level (not just text) proof: canonical_root_orchestrator.py must never import
    `market_truth.acquisition.providers.databento_historical` at all -- there is no legitimate
    reason for the canonical dispatch loop to import the module that knows how to load the
    acquisition credential, even indirectly."""
    path = ORCHESTRATION_DIR / "canonical_root_orchestrator.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "databento_historical" not in node.module, f"{path}: forbidden import of {node.module}"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "databento_historical" not in alias.name, f"{path}: forbidden import of {alias.name}"


def test_diagnostics_credential_check_is_the_only_orchestration_adjacent_file_naming_the_secret():
    """Confirms the secret env var name is confined to exactly the diagnostic tool built for it
    (and the acquisition launcher, checked above) -- not scattered anywhere else in the new
    orchestration/diagnostics code this dispatch adds."""
    diagnostics_files = sorted(DIAGNOSTICS_DIR.rglob("*.py"))
    referencing = [p for p in diagnostics_files if "DATABENTO_HISTORICAL_API_KEY_PATH" in p.read_text(encoding="utf-8")]
    assert [p.name for p in referencing] == ["credential_check.py"]
