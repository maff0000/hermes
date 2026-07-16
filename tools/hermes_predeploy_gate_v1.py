#!/usr/bin/env python3
"""HERMES market-hours PRE-DEPLOYMENT validation gate v1.
WO-HELM-HERMES-MARKET-HOURS-PREDEPLOY-GATE-HARNESS-0001. Owner: HERMES (Helm). Created (UTC): 2026-07-16.

INERT pre-deployment CLI. NOT wired into main.py / compose / systemd / startup. This tool is run by an operator or CI
BEFORE a deploy to prove that the configured instrument inventory and the governed market-hours config
(config/market_hours_schedule.v1.json) are mutually complete and free of the historical WTICO_USD->ICO_USD phantom-
substitution defect (a `[A-Z]{3}_[A-Z]{3}` regex once misread "ICO_USD" out of "WTICO_USD"). It reuses the already-
governed PURE validator utils.hermes_market_hours_health_v1.validate_config_completeness() and adds pre-deploy-only
guards on top.

PURITY: reads files (the config, and optional inventory files) and reads the git HEAD of the worktree via `git rev-parse`
(a subprocess, read-only). NO Redis, NO SQL, NO network, NO env-derived defaults, NO writes to any datastore. It only
prints a machine-readable JSON report to stdout and returns a deterministic exit code. UTC only. Project-relative paths.

DOCTRINE (anti-substring / fail-loud):
  * EXACT tokenisation only. Inventory tokens are split on comma/newline and stripped of surrounding whitespace, then
    treated ATOMICALLY. We NEVER regex-EXTRACT an instrument-looking substring out of a token; "WTICO_USD" is never
    silently turned into "ICO_USD". A token that, after surrounding-whitespace strip, still contains internal
    whitespace, or is not fully UPPER-CASE, or does not fully match ^[A-Z0-9]+_[A-Z0-9]+$ is recorded as INVALID and
    fails the gate. Contaminated tokens are NEVER silently normalised into a pass.
  * No hidden defaults. Every failure class fails loud with its own non-zero exit code.

USAGE (see docs/tools/hermes_predeploy_gate_v1.md):
  python3 -m tools.hermes_predeploy_gate_v1 --inventory-file tools/hermes_predeploy_gate.inventory.example
  python3 -m tools.hermes_predeploy_gate_v1 --inventory "XAU_USD,XAG_USD,..." \
      --expected-inventory-file tools/hermes_predeploy_gate.inventory.example
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from typing import Dict, List, Optional, Sequence, Tuple

import utils.hermes_market_hours_health_v1 as M

TOOL_NAME = "hermes_predeploy_gate_v1"
TOOL_VERSION = "1"
UTC = datetime.timezone.utc

# strict atomic-token grammar. Validation only — NEVER used to EXTRACT a substring out of a larger token.
_TOKEN_RE = re.compile(r"^[A-Z0-9]+_[A-Z0-9]+$")
_INTERNAL_WS_RE = re.compile(r"\s")

# ---- deterministic exit-code matrix (each failure class has its own non-zero code) --------------------------------
EXIT_OK = 0
EXIT_INVALID_TOKEN = 10
EXIT_INVENTORY_DRIFT = 11
EXIT_DUPLICATE = 12
EXIT_UNMAPPED = 13
EXIT_UNSUPPORTED_CONFIG_VERSION = 14
EXIT_INVALID_TIMEZONE = 15
EXIT_MISSING_SCHEDULE_MAPPING = 16
EXIT_FAILCLOSED_SUPPRESSIVE = 17
EXIT_PHANTOM_SUBSTITUTION = 18
EXIT_INVALID_CONFIG = 19
EXIT_NO_INVENTORY = 64          # usage: no/both inventory source(s)
EXIT_CONFIG_UNREADABLE = 66     # config file missing / not JSON

# failure classes in the order they are reported and the exit code chosen (first present wins -> deterministic).
_PRIORITY: List[Tuple[str, int]] = [
    ("INVALID_TOKEN", EXIT_INVALID_TOKEN),
    ("PHANTOM_SUBSTITUTION", EXIT_PHANTOM_SUBSTITUTION),
    ("INVENTORY_DRIFT", EXIT_INVENTORY_DRIFT),
    ("UNSUPPORTED_CONFIG_VERSION", EXIT_UNSUPPORTED_CONFIG_VERSION),
    ("INVALID_TIMEZONE", EXIT_INVALID_TIMEZONE),
    ("INVALID_CONFIG", EXIT_INVALID_CONFIG),
    ("MISSING_SCHEDULE_MAPPING", EXIT_MISSING_SCHEDULE_MAPPING),
    ("DUPLICATE", EXIT_DUPLICATE),
    ("FAILCLOSED_SUPPRESSIVE", EXIT_FAILCLOSED_SUPPRESSIVE),
    ("UNMAPPED", EXIT_UNMAPPED),
]


def repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1]


def default_config_path() -> pathlib.Path:
    return repo_root() / "config" / "market_hours_schedule.v1.json"


# --------------------------------------------------------------------------- exact tokenisation (atomic, no extraction)
def tokenise(raw: str) -> Tuple[List[str], List[str]]:
    """Split raw inventory text on comma/newline ONLY, strip surrounding whitespace, drop blanks. Return
    (valid_tokens, invalid_tokens) preserving input order. A token is atomic: it is validated as a whole, never
    substring-extracted. Contamination (internal whitespace, lowercase, malformed) -> invalid (never normalised)."""
    valid: List[str] = []
    invalid: List[str] = []
    for piece in re.split(r"[,\n]", raw):
        tok = piece.strip()
        if tok == "":
            continue  # blank line / trailing comma / formatting only
        if tok.startswith("#"):
            continue  # explicit comment line (documented convention) — not an instrument token
        if _INTERNAL_WS_RE.search(tok) or tok != tok.upper() or not _TOKEN_RE.match(tok):
            invalid.append(tok)
        else:
            valid.append(tok)
    return valid, invalid


def inventory_digest(all_tokens: Sequence[str]) -> str:
    """sha256 over the SORTED exact tokens (order-independent, stable). Includes duplicates so the digest reflects the
    exact provided multiset."""
    payload = "\n".join(sorted(all_tokens)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def source_sha() -> str:
    """git rev-parse HEAD of the worktree (read-only subprocess). Never fatal; returns a sentinel if git is absent."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo_root()),
            capture_output=True, text=True, timeout=10, check=False)
        sha = out.stdout.strip()
        return sha if sha else "UNKNOWN_NO_GIT"
    except Exception:  # noqa: BLE001
        return "UNKNOWN_NO_GIT"


# --------------------------------------------------------------------------- failure classification
def _classify_validator_error(msg: str) -> str:
    m = msg.lower()
    if "unsupported config_version" in m:
        return "UNSUPPORTED_CONFIG_VERSION"
    if "market_timezone" in m:
        return "INVALID_TIMEZONE"
    if "reopening_grace_seconds" in m:
        return "INVALID_CONFIG"
    if "missing named schedule" in m or "malformed market-hours schedule" in m:
        return "MISSING_SCHEDULE_MAPPING"
    if "duplicate configured instrument" in m:
        return "DUPLICATE"
    return "INVALID_CONFIG"


def _detect_failclosed_suppressive(cfg: dict, fail_closed: Sequence[str]) -> List[str]:
    """A fail_closed_unvalidated instrument MUST resolve to None (fail loud). If, ignoring the fail-closed short-circuit,
    it would resolve to a REAL (non-None) schedule, the operator has contradicted themselves: the entry could suppress.
    That is a governance defect -> fail the gate."""
    fcu = cfg.get("fail_closed_unvalidated", {}) or {}
    bad: List[str] = []
    for inst in fail_closed:
        shadow = dict(cfg)
        shadow["fail_closed_unvalidated"] = {k: v for k, v in fcu.items() if k != inst}
        try:
            sched = M.load_schedule(shadow, inst)
        except M.ScheduleError:
            sched = None  # malformed still fails loud; not suppressive
        if sched is not None:
            bad.append(inst)
    return bad


def _detect_drift(actual: Sequence[str], expected: Sequence[str]) -> Dict[str, List]:
    a, e = set(actual), set(expected)
    missing = sorted(e - a)          # expected but absent
    unexpected = sorted(a - e)       # present but not expected
    phantom: List[Dict[str, str]] = []
    for miss in missing:
        for extra in unexpected:
            # phantom substitution: an unexpected token is a strict substring of a missing expected token
            # (e.g. missing WTICO_USD, present ICO_USD where "ICO_USD" in "WTICO_USD").
            if extra != miss and extra in miss:
                phantom.append({"expected": miss, "substituted_with": extra})
    return {"missing": missing, "unexpected": unexpected, "phantom_substitutions": phantom}


# --------------------------------------------------------------------------- core evaluation (pure)
def evaluate(*, inventory_raw: str, config_path: pathlib.Path,
             expected_raw: Optional[str], config_text: str, cfg: dict,
             now_utc: Optional[datetime.datetime] = None) -> dict:
    """Pure evaluation. Returns the full report dict including a deterministic exit_code. Does no I/O beyond what the
    caller already read (config already loaded; git sha computed here is read-only)."""
    valid, invalid = tokenise(inventory_raw)
    all_tokens = valid + invalid

    # duplicates over ALL provided tokens (an alias/instrument must not appear twice -> double-voting risk).
    seen: set = set()
    duplicates: List[str] = []
    for t in all_tokens:
        if t in seen and t not in duplicates:
            duplicates.append(t)
        seen.add(t)

    failures: List[Dict[str, object]] = []

    for tok in invalid:
        failures.append({"class": "INVALID_TOKEN",
                         "detail": f"contaminated/malformed inventory token {tok!r} "
                                   "(internal whitespace, lowercase, or not ^[A-Z0-9]+_[A-Z0-9]+$)"})
    for tok in sorted(set(duplicates)):
        failures.append({"class": "DUPLICATE", "detail": f"duplicate inventory token {tok!r}"})

    # inventory drift vs optional expected inventory (exact tokens, atomic).
    drift = {"missing": [], "unexpected": [], "phantom_substitutions": []}
    expected_provided = expected_raw is not None
    if expected_provided:
        exp_valid, exp_invalid = tokenise(expected_raw)
        for tok in exp_invalid:
            failures.append({"class": "INVALID_TOKEN",
                             "detail": f"contaminated/malformed EXPECTED-inventory token {tok!r}"})
        drift = _detect_drift(all_tokens, exp_valid + exp_invalid)
        for ph in drift["phantom_substitutions"]:
            failures.append({"class": "PHANTOM_SUBSTITUTION",
                             "detail": f"phantom substitution: expected {ph['expected']!r} but found "
                                       f"{ph['substituted_with']!r} (substring of the expected token)"})
        # any non-phantom drift is still drift
        phantom_extras = {p["substituted_with"] for p in drift["phantom_substitutions"]}
        residual_missing = [m for m in drift["missing"]
                            if m not in {p["expected"] for p in drift["phantom_substitutions"]}]
        residual_unexpected = [u for u in drift["unexpected"] if u not in phantom_extras]
        if residual_missing or residual_unexpected:
            failures.append({"class": "INVENTORY_DRIFT",
                             "detail": f"inventory drift vs expected: missing={residual_missing} "
                                       f"unexpected={residual_unexpected}"})

    # run the governed pure completeness validator over the VALID tokens (atomic).
    ok, vreport = M.validate_config_completeness(cfg, valid)
    for err in vreport.get("errors", []):
        failures.append({"class": _classify_validator_error(err), "detail": err})
    for inst in vreport.get("unmapped", []):
        failures.append({"class": "UNMAPPED",
                         "detail": f"instrument {inst!r} resolves to no governed schedule and is not declared "
                                   "fail-closed (ungoverned gap)"})

    fail_closed = sorted(vreport.get("fail_closed", []))
    for inst in _detect_failclosed_suppressive(cfg, fail_closed):
        failures.append({"class": "FAILCLOSED_SUPPRESSIVE",
                         "detail": f"fail-closed instrument {inst!r} would resolve to a REAL (non-None) schedule if "
                                   "its fail-closed entry were removed -> could suppress; contradictory config"})

    # choose deterministic exit code by fixed priority.
    present = {f["class"] for f in failures}
    exit_code = EXIT_OK
    for cls, code in _PRIORITY:
        if cls in present:
            exit_code = code
            break
    verdict = "PASS" if exit_code == EXIT_OK else "FAIL"

    ts = (now_utc or datetime.datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "validator_version": M.DECISION_VERSION,
        "verdict": verdict,
        "exit_code": exit_code,
        "config_path": str(config_path),
        "config_version": str(cfg.get("config_version")),
        "config_sha256": hashlib.sha256(config_text.encode("utf-8")).hexdigest(),
        "inventory_digest": inventory_digest(all_tokens),
        "inventory_token_count": len(all_tokens),
        "scheduled": sorted(vreport.get("resolved", {}).keys()),
        "intentionally_fail_closed": fail_closed,
        "unmapped": sorted(vreport.get("unmapped", [])),
        "invalid": sorted(invalid),
        "duplicates": sorted(set(duplicates)),
        "expected_inventory_provided": expected_provided,
        "inventory_drift": drift,
        "failures": sorted(failures, key=lambda f: (f["class"], f["detail"])),
        "validator_ok": ok,
        "supported_config_versions": sorted(M.SUPPORTED_CONFIG_VERSIONS),
        "source_sha": source_sha(),
        "utc_execution_timestamp": ts,
    }
    return report


def render(report: dict) -> str:
    """Stable machine-readable JSON: sorted keys, fixed indent, deterministic separators."""
    return json.dumps(report, sort_keys=True, indent=2, separators=(",", ": "), default=str)


# --------------------------------------------------------------------------- CLI
def _read_text(path: str) -> str:
    return pathlib.Path(path).read_text(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hermes_predeploy_gate_v1",
        description="HERMES INERT pre-deployment market-hours config/inventory validation gate. "
                    "Exit 0 = clean pass; distinct non-zero code per failure class. UTC only. Not runtime-wired.")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--inventory", help="configured inventory as a comma/newline-separated string (atomic tokens)")
    src.add_argument("--inventory-file", help="path to a file with the configured inventory "
                                              "(one instrument per line, or comma-separated)")
    p.add_argument("--config", default=str(default_config_path()),
                   help="path to the governed market-hours config (default: repo config/market_hours_schedule.v1.json)")
    p.add_argument("--expected-inventory", help="optional expected inventory string for drift detection")
    p.add_argument("--expected-inventory-file", help="optional path to an expected-inventory file for drift detection")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if bool(args.inventory) == bool(args.inventory_file):
        print("ERROR: provide exactly one of --inventory or --inventory-file", file=sys.stderr)
        return EXIT_NO_INVENTORY
    if args.expected_inventory and args.expected_inventory_file:
        print("ERROR: provide at most one of --expected-inventory or --expected-inventory-file", file=sys.stderr)
        return EXIT_NO_INVENTORY

    try:
        inventory_raw = args.inventory if args.inventory else _read_text(args.inventory_file)
    except OSError as exc:
        print(f"ERROR: cannot read inventory file: {exc}", file=sys.stderr)
        return EXIT_NO_INVENTORY

    expected_raw: Optional[str] = None
    if args.expected_inventory:
        expected_raw = args.expected_inventory
    elif args.expected_inventory_file:
        try:
            expected_raw = _read_text(args.expected_inventory_file)
        except OSError as exc:
            print(f"ERROR: cannot read expected-inventory file: {exc}", file=sys.stderr)
            return EXIT_NO_INVENTORY

    config_path = pathlib.Path(args.config)
    try:
        config_text = config_path.read_text(encoding="utf-8")
        cfg = json.loads(config_text)
    except (OSError, ValueError) as exc:
        print(f"ERROR: cannot read/parse config {config_path}: {exc}", file=sys.stderr)
        return EXIT_CONFIG_UNREADABLE

    report = evaluate(inventory_raw=inventory_raw, config_path=config_path,
                      expected_raw=expected_raw, config_text=config_text, cfg=cfg)
    print(render(report))
    return int(report["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
