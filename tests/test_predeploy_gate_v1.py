"""WO-HELM-HERMES-MARKET-HOURS-PREDEPLOY-GATE-HARNESS-0001 — tests for the INERT pre-deployment market-hours gate.

Proves: the real 14-instrument inventory passes; WTICO_USD is accepted (fail-closed); standalone ICO_USD never appears
in a passing config; the historical WTICO_USD->ICO_USD phantom substitution FAILS; unknown/duplicate/contaminated
tokens FAIL; config-level defects (unsupported version, invalid tz, missing schedule mapping, suppressive fail-closed)
FAIL with distinct exit codes; input-order independence; deterministic JSON; digest stability.

These tests import the tool module directly and also drive it as a subprocess (for byte-identical output + exit codes).
"""
import copy
import json
import pathlib
import subprocess
import sys

import pytest

import tools.hermes_predeploy_gate_v1 as G

ROOT = pathlib.Path(__file__).resolve().parents[1]
REAL_CONFIG = ROOT / "config" / "market_hours_schedule.v1.json"
EXAMPLE_INV = ROOT / "tools" / "hermes_predeploy_gate.inventory.example"

# real runtime inventory (WTICO_USD is the true instrument; ICO_USD is the historical regex misread and MUST be absent)
REAL14 = ["XAU_USD", "XAG_USD", "XPT_USD", "XCU_USD", "GBP_USD", "EUR_USD", "USD_JPY",
          "AUD_USD", "NZD_USD", "USD_CAD", "USD_CHF", "EUR_GBP", "WTICO_USD", "SPX500_USD"]
SCHEDULED12 = ["AUD_USD", "EUR_GBP", "EUR_USD", "GBP_USD", "NZD_USD", "USD_CAD",
               "USD_CHF", "USD_JPY", "XAG_USD", "XAU_USD", "XCU_USD", "XPT_USD"]


# --------------------------------------------------------------------------- helpers
def run_cli(argv, capsys):
    """Invoke main() with argv; return (exit_code, parsed_report)."""
    code = G.main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out)


def run_subprocess(argv):
    """Invoke the tool as a real subprocess from the repo root; return (returncode, stdout)."""
    proc = subprocess.run([sys.executable, "-m", "tools.hermes_predeploy_gate_v1", *argv],
                          cwd=str(ROOT), capture_output=True, text=True)
    return proc.returncode, proc.stdout


def write_config(tmp_path, mutate):
    cfg = json.loads(REAL_CONFIG.read_text())
    mutate(cfg)
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(cfg))
    return p


def classes(report):
    return {f["class"] for f in report["failures"]}


# --------------------------------------------------------------------------- clean-pass cases
def test_real_14_inventory_passes_via_file(capsys):
    code, rep = run_cli(["--inventory-file", str(EXAMPLE_INV), "--config", str(REAL_CONFIG)], capsys)
    assert code == G.EXIT_OK
    assert rep["verdict"] == "PASS"
    assert rep["failures"] == []
    assert rep["scheduled"] == SCHEDULED12
    assert rep["intentionally_fail_closed"] == ["SPX500_USD", "WTICO_USD"]
    assert rep["unmapped"] == []
    assert rep["invalid"] == []
    assert rep["inventory_token_count"] == 14
    assert rep["config_version"] == "3"


def test_real_14_inventory_passes_via_string(capsys):
    code, rep = run_cli(["--inventory", ",".join(REAL14), "--config", str(REAL_CONFIG),
                         "--expected-inventory", ",".join(REAL14)], capsys)
    assert code == G.EXIT_OK and rep["verdict"] == "PASS"
    assert rep["inventory_drift"] == {"missing": [], "unexpected": [], "phantom_substitutions": []}


def test_wtico_usd_present_accepted(capsys):
    """WTICO_USD (the real instrument) must be accepted as an intentional fail-closed entry, not rejected."""
    code, rep = run_cli(["--inventory", ",".join(REAL14), "--config", str(REAL_CONFIG)], capsys)
    assert code == G.EXIT_OK
    assert "WTICO_USD" in rep["intentionally_fail_closed"]
    assert "WTICO_USD" not in rep["unmapped"]
    assert "WTICO_USD" not in rep["invalid"]


def test_standalone_ico_usd_absent_from_passing_config(capsys):
    """The passing config must contain NO standalone ICO_USD anywhere (scheduled/fail_closed/unmapped/invalid)."""
    code, rep = run_cli(["--inventory", ",".join(REAL14), "--config", str(REAL_CONFIG)], capsys)
    assert code == G.EXIT_OK
    for section in ("scheduled", "intentionally_fail_closed", "unmapped", "invalid"):
        assert "ICO_USD" not in rep[section]
    raw_cfg = REAL_CONFIG.read_text()
    assert '"ICO_USD"' not in raw_cfg  # never a standalone key
    assert "WTICO_USD" in raw_cfg      # the real token is present


# --------------------------------------------------------------------------- phantom substitution (the core defect)
def test_wtico_to_ico_substitution_fails(capsys):
    """Historical defect: WTICO_USD replaced by ICO_USD. With expected=real inventory, the gate must FAIL as a phantom
    substitution AND exit with the dedicated phantom code."""
    actual = [t for t in REAL14 if t != "WTICO_USD"] + ["ICO_USD"]
    code, rep = run_cli(["--inventory", ",".join(actual), "--config", str(REAL_CONFIG),
                         "--expected-inventory", ",".join(REAL14)], capsys)
    assert code == G.EXIT_PHANTOM_SUBSTITUTION
    assert rep["verdict"] == "FAIL"
    assert "PHANTOM_SUBSTITUTION" in classes(rep)
    phantoms = rep["inventory_drift"]["phantom_substitutions"]
    assert {"expected": "WTICO_USD", "substituted_with": "ICO_USD"} in phantoms


def test_no_substring_extraction_of_wtico(capsys):
    """WTICO_USD as a lone atomic token must NOT be split into ICO_USD; it stays a single fail-closed instrument."""
    code, rep = run_cli(["--inventory", "WTICO_USD", "--config", str(REAL_CONFIG)], capsys)
    assert code == G.EXIT_OK
    assert rep["intentionally_fail_closed"] == ["WTICO_USD"]
    assert "ICO_USD" not in rep["invalid"] and "ICO_USD" not in rep["unmapped"]
    assert rep["inventory_token_count"] == 1


# --------------------------------------------------------------------------- inventory-level failures
def test_unknown_instrument_fails(capsys):
    code, rep = run_cli(["--inventory", "ZZZ_UNKNOWN", "--config", str(REAL_CONFIG)], capsys)
    assert code == G.EXIT_UNMAPPED
    assert "UNMAPPED" in classes(rep)
    assert rep["unmapped"] == ["ZZZ_UNKNOWN"]


def test_duplicate_instrument_fails(capsys):
    code, rep = run_cli(["--inventory", "XAU_USD,XAG_USD,XAU_USD", "--config", str(REAL_CONFIG)], capsys)
    assert code == G.EXIT_DUPLICATE
    assert "DUPLICATE" in classes(rep)
    assert "XAU_USD" in rep["duplicates"]


def test_whitespace_contaminated_token_fails(capsys):
    """Internal whitespace survives surrounding-strip -> INVALID, never normalised into two tokens."""
    code, rep = run_cli(["--inventory", "XAU USD\nXAG_USD", "--config", str(REAL_CONFIG)], capsys)
    assert code == G.EXIT_INVALID_TOKEN
    assert "INVALID_TOKEN" in classes(rep)
    assert "XAU USD" in rep["invalid"]
    assert "XAU_USD" not in rep["scheduled"]  # NOT silently extracted


def test_lowercase_contaminated_token_fails(capsys):
    code, rep = run_cli(["--inventory", "xau_usd,XAG_USD", "--config", str(REAL_CONFIG)], capsys)
    assert code == G.EXIT_INVALID_TOKEN
    assert "INVALID_TOKEN" in classes(rep)
    assert "xau_usd" in rep["invalid"]


def test_inventory_drift_fails(capsys):
    """Non-phantom drift (an extra unrelated instrument) fails as INVENTORY_DRIFT."""
    code, rep = run_cli(["--inventory", ",".join(REAL14 + ["ZZZ_EXTRA"]), "--config", str(REAL_CONFIG),
                         "--expected-inventory", ",".join(REAL14)], capsys)
    assert code != G.EXIT_OK
    assert "INVENTORY_DRIFT" in classes(rep)
    assert "ZZZ_EXTRA" in rep["inventory_drift"]["unexpected"]


# --------------------------------------------------------------------------- config-level failures (distinct codes)
def test_unsupported_config_version_fails(tmp_path, capsys):
    cfg = write_config(tmp_path, lambda c: c.update(config_version="9"))
    code, rep = run_cli(["--inventory", "XAU_USD", "--config", str(cfg)], capsys)
    assert code == G.EXIT_UNSUPPORTED_CONFIG_VERSION
    assert "UNSUPPORTED_CONFIG_VERSION" in classes(rep)


def test_missing_schedule_mapping_fails(tmp_path, capsys):
    def mutate(c):
        c["instrument_map"]["XAU_USD"] = "does_not_exist"
    cfg = write_config(tmp_path, mutate)
    code, rep = run_cli(["--inventory", "XAU_USD", "--config", str(cfg)], capsys)
    assert code == G.EXIT_MISSING_SCHEDULE_MAPPING
    assert "MISSING_SCHEDULE_MAPPING" in classes(rep)


def test_invalid_timezone_fails(tmp_path, capsys):
    cfg = write_config(tmp_path, lambda c: c.update(market_timezone="Not/AReal_Zone"))
    code, rep = run_cli(["--inventory", "XAU_USD", "--config", str(cfg)], capsys)
    assert code == G.EXIT_INVALID_TIMEZONE
    assert "INVALID_TIMEZONE" in classes(rep)


def test_failclosed_pointed_at_real_schedule_fails(tmp_path, capsys):
    """WTICO_USD declared fail-closed BUT also mapped to a real 'fx' schedule -> it could resolve to a suppressive
    schedule -> contradictory config -> FAIL with the dedicated code."""
    def mutate(c):
        c["instrument_map"]["WTICO_USD"] = "fx"  # still present in fail_closed_unvalidated
    cfg = write_config(tmp_path, mutate)
    code, rep = run_cli(["--inventory", "WTICO_USD", "--config", str(cfg)], capsys)
    assert code == G.EXIT_FAILCLOSED_SUPPRESSIVE
    assert "FAILCLOSED_SUPPRESSIVE" in classes(rep)


# --------------------------------------------------------------------------- determinism / digest
def test_input_order_independence(capsys):
    code_a, rep_a = run_cli(["--inventory", ",".join(REAL14), "--config", str(REAL_CONFIG)], capsys)
    code_b, rep_b = run_cli(["--inventory", ",".join(reversed(REAL14)), "--config", str(REAL_CONFIG)], capsys)
    assert code_a == code_b == G.EXIT_OK
    assert rep_a["verdict"] == rep_b["verdict"] == "PASS"
    assert rep_a["inventory_digest"] == rep_b["inventory_digest"]     # digest is order-independent
    assert rep_a["scheduled"] == rep_b["scheduled"]
    assert rep_a["intentionally_fail_closed"] == rep_b["intentionally_fail_closed"]


def test_digest_stability_and_definition(capsys):
    import hashlib
    _, rep = run_cli(["--inventory", ",".join(REAL14), "--config", str(REAL_CONFIG)], capsys)
    expected = hashlib.sha256("\n".join(sorted(REAL14)).encode()).hexdigest()
    assert rep["inventory_digest"] == expected
    # a different multiset yields a different digest
    _, rep2 = run_cli(["--inventory", ",".join(REAL14[:-1]), "--config", str(REAL_CONFIG)], capsys)
    assert rep2["inventory_digest"] != rep["inventory_digest"]


def test_deterministic_json_output_modulo_timestamp():
    argv = ["--inventory-file", str(EXAMPLE_INV), "--config", str(REAL_CONFIG),
            "--expected-inventory-file", str(EXAMPLE_INV)]
    rc1, out1 = run_subprocess(argv)
    rc2, out2 = run_subprocess(argv)
    assert rc1 == rc2 == 0
    a, b = json.loads(out1), json.loads(out2)
    a.pop("utc_execution_timestamp"), b.pop("utc_execution_timestamp")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["source_sha"] == b["source_sha"]  # git HEAD stable across runs


# --------------------------------------------------------------------------- exit-code matrix
def test_exit_code_matrix():
    """Each failure class produces its own documented non-zero exit code (driven as a real subprocess)."""
    base_cfg = str(REAL_CONFIG)
    cases = [
        (["--inventory-file", str(EXAMPLE_INV), "--config", base_cfg], G.EXIT_OK),
        (["--inventory", "xau_usd", "--config", base_cfg], G.EXIT_INVALID_TOKEN),
        (["--inventory", "XAU_USD,XAU_USD", "--config", base_cfg], G.EXIT_DUPLICATE),
        (["--inventory", "ZZZ_UNKNOWN", "--config", base_cfg], G.EXIT_UNMAPPED),
        (["--inventory", ",".join([t for t in REAL14 if t != "WTICO_USD"] + ["ICO_USD"]),
          "--config", base_cfg, "--expected-inventory", ",".join(REAL14)], G.EXIT_PHANTOM_SUBSTITUTION),
    ]
    for argv, expected in cases:
        rc, _ = run_subprocess(argv)
        assert rc == expected, f"{argv} -> {rc}, expected {expected}"


def test_report_sections_present(capsys):
    _, rep = run_cli(["--inventory", ",".join(REAL14), "--config", str(REAL_CONFIG)], capsys)
    for key in ("scheduled", "intentionally_fail_closed", "unmapped", "invalid",
                "inventory_digest", "config_sha256", "validator_version", "source_sha",
                "utc_execution_timestamp", "tool_version"):
        assert key in rep
    assert rep["utc_execution_timestamp"].endswith("Z")  # UTC only


def test_usage_error_when_no_inventory_source():
    rc, _ = run_subprocess(["--config", str(REAL_CONFIG)])
    assert rc == G.EXIT_NO_INVENTORY
