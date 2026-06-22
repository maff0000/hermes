#!/usr/bin/env python3
"""HERMES-OPS-STAGE-CAP-001 — staging resource-cap verification harness.

STAGING VERIFICATION TOOL — NOT part of the hot-path runtime. This module is
deliberately NOT imported, initialised, or invoked by `main.py` or anything on
the market-data hot path. It is run by hand (or a staging CI job) from INSIDE
the running hermes-signal container during the staging container cutover, to
prove that the docker-compose resource pins (cpus / mem_limit / pids_limit)
have actually bound as effective cgroup v2 limits.

It does two things:

  1. CAP ASSERTION (the governance gate). Read the CONFIGURED caps (parsed from
     docker-compose.yml, or supplied via args/env) and the EFFECTIVE cgroup v2
     caps the process is running under, and assert they agree. On any drift it
     fails loud with reason code GOV-STAGE-CAP-001 and exits non-zero.

  2. MOCK LOAD (optional, bounded, default DRY-RUN). A self-contained synthetic
     high-throughput tick/candle telemetry loop that allocates and processes
     synthetic records at a target rate for a bounded duration, so an operator
     can watch the caps hold under churn. It performs NO network egress, NO
     disk deletion, NO real DB/Redis/stream I/O. In --dry-run (the default) it
     only models the work without sustained allocation pressure.

SAFETY INVARIANTS:
  * Pure standard library. No third-party imports. No network. No deletes.
  * Bounded: every loop has an explicit iteration / duration / rate ceiling.
  * Default --dry-run so importing or fat-fingering it never stresses a box.
  * Read-only against the filesystem except stdout.

Reason codes:
  GOV-STAGE-CAP-001  effective cgroup cap != configured compose cap (cpus/mem/pids)
  GOV-STAGE-CAP-002  could not read an effective cgroup v2 limit (wrong host / cgroup v1 / not in container)
  GOV-STAGE-CAP-003  could not parse a configured cap from docker-compose.yml (and none supplied via arg/env)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time

# Reason codes -----------------------------------------------------------------
GOV_STAGE_CAP_MISMATCH = "GOV-STAGE-CAP-001"  # effective != configured
GOV_STAGE_CAP_READ = "GOV-STAGE-CAP-002"      # cannot read effective cgroup limit
GOV_STAGE_CAP_PARSE = "GOV-STAGE-CAP-003"     # cannot parse configured cap

# cgroup v2 control files ------------------------------------------------------
CG_MEM_MAX = "/sys/fs/cgroup/memory.max"
CG_CPU_MAX = "/sys/fs/cgroup/cpu.max"
CG_PIDS_MAX = "/sys/fs/cgroup/pids.max"

# Default location of the compose file relative to repo root (this file lives in ops/staging/).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_COMPOSE = os.path.join(_REPO_ROOT, "docker-compose.yml")

# cpu.max default period (microseconds) used by Docker / cgroup v2.
DEFAULT_CPU_PERIOD_US = 100_000


# ----------------------------------------------------------------------------- #
# Unit parsing                                                                   #
# ----------------------------------------------------------------------------- #
_MEM_UNITS = {
    "b": 1,
    "k": 1024, "kb": 1024, "ki": 1024, "kib": 1024,
    "m": 1024 ** 2, "mb": 1024 ** 2, "mi": 1024 ** 2, "mib": 1024 ** 2,
    "g": 1024 ** 3, "gb": 1024 ** 3, "gi": 1024 ** 3, "gib": 1024 ** 3,
}


def parse_mem_to_bytes(value):
    """'4g' / '4096m' / '4294967296' -> bytes (int). Docker mem_limit uses powers of 1024."""
    s = str(value).strip().lower()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([a-z]*)", s)
    if not m:
        raise ValueError(f"unparseable memory value: {value!r}")
    qty, unit = m.group(1), m.group(2) or "b"
    if unit not in _MEM_UNITS:
        raise ValueError(f"unknown memory unit in {value!r}")
    return int(float(qty) * _MEM_UNITS[unit])


def parse_cpus(value):
    """'4' / '4.0' -> float cores."""
    return float(str(value).strip())


# ----------------------------------------------------------------------------- #
# Configured caps — parse from docker-compose.yml (no YAML lib; targeted regex)  #
# ----------------------------------------------------------------------------- #
def _compose_default(text, key):
    """Extract the default from a compose line of the form
        key: ${ENV_VAR:-DEFAULT}
    or a bare literal
        key: VALUE
    Returns the default string or None.
    """
    # ${VAR:-default}
    m = re.search(rf"^\s*{re.escape(key)}\s*:\s*\$\{{[A-Z0-9_]+:-([^}}]+)\}}", text, re.MULTILINE)
    if m:
        return m.group(1).strip().strip('"').strip("'")
    # bare literal (skip ${...} forms with no default)
    m = re.search(rf"^\s*{re.escape(key)}\s*:\s*([^\s${{][^\n]*)$", text, re.MULTILINE)
    if m:
        return m.group(1).strip().strip('"').strip("'")
    return None


def configured_caps_from_compose(compose_path):
    """Return dict {cpus, mem_bytes, pids} parsed from the compose defaults.
    Missing keys are returned as None (caller may override via args/env)."""
    try:
        with open(compose_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return {"cpus": None, "mem_bytes": None, "pids": None, "_error": str(exc)}

    cpus_raw = _compose_default(text, "cpus")
    mem_raw = _compose_default(text, "mem_limit")
    pids_raw = _compose_default(text, "pids_limit")
    return {
        "cpus": parse_cpus(cpus_raw) if cpus_raw else None,
        "mem_bytes": parse_mem_to_bytes(mem_raw) if mem_raw else None,
        "pids": int(pids_raw) if pids_raw and pids_raw.isdigit() else None,
    }


# ----------------------------------------------------------------------------- #
# Effective caps — read cgroup v2                                                #
# ----------------------------------------------------------------------------- #
def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read().strip()


def effective_mem_bytes():
    """memory.max -> bytes, or None for 'max' (unlimited)."""
    raw = _read(CG_MEM_MAX)
    if raw == "max":
        return None
    return int(raw)


def effective_cpus():
    """cpu.max is '<quota> <period>'. quota='max' => unlimited. Else cores = quota/period."""
    raw = _read(CG_CPU_MAX)
    parts = raw.split()
    quota = parts[0]
    period = int(parts[1]) if len(parts) > 1 else DEFAULT_CPU_PERIOD_US
    if quota == "max":
        return None
    return int(quota) / period


def effective_pids():
    """pids.max -> int, or None for 'max'."""
    raw = _read(CG_PIDS_MAX)
    if raw == "max":
        return None
    return int(raw)


# ----------------------------------------------------------------------------- #
# Assertion                                                                      #
# ----------------------------------------------------------------------------- #
class CapFailure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def assert_caps(configured, *, mem_tolerance=0.0):
    """Compare configured vs effective. Raise CapFailure(reason_code) on any drift.
    Returns a list of human-readable PASS lines on success.

    mem_tolerance: fractional slack on memory (some runtimes round mem.max to a
    page/2MB boundary). Default 0.0 == exact. Keep tight; widen only with reason.
    """
    lines = []

    # --- effective reads (fail-loud if unreadable OR unparseable) ---
    # OSError = file missing / not a cgroup-v2 host; ValueError = cgroup file held a non-numeric /
    # malformed string (int()/float() parse failure). Both map to GOV-STAGE-CAP-002 rather than
    # escaping as a raw traceback.
    try:
        eff_mem = effective_mem_bytes()
        eff_cpu = effective_cpus()
        eff_pids = effective_pids()
    except (OSError, ValueError) as exc:
        raise CapFailure(
            GOV_STAGE_CAP_READ,
            f"{GOV_STAGE_CAP_READ}: cannot read/parse cgroup v2 limits ({exc}). "
            f"Run this INSIDE the hermes-signal container on a cgroup-v2 host."
        )

    checks = (
        ("cpus", configured.get("cpus"), eff_cpu, "cores", 0.0),
        ("mem_limit", configured.get("mem_bytes"), eff_mem, "bytes", mem_tolerance),
        ("pids_limit", configured.get("pids"), eff_pids, "pids", 0.0),
    )

    for name, cfg, eff, unit, tol in checks:
        if cfg is None:
            raise CapFailure(
                GOV_STAGE_CAP_PARSE,
                f"{GOV_STAGE_CAP_PARSE}: configured {name} not supplied "
                f"(absent from compose and no --{name.replace('_limit','')} / env override)."
            )
        if eff is None:
            raise CapFailure(
                GOV_STAGE_CAP_MISMATCH,
                f"{GOV_STAGE_CAP_MISMATCH}: {name} configured={cfg} {unit} but effective cap is "
                f"UNLIMITED ('max'). The compose pin did not bind — container is uncapped."
            )
        if tol:
            ok = abs(eff - cfg) <= cfg * tol
        else:
            ok = float(eff) == float(cfg)
        if not ok:
            raise CapFailure(
                GOV_STAGE_CAP_MISMATCH,
                f"{GOV_STAGE_CAP_MISMATCH}: {name} drift — configured={cfg} {unit}, "
                f"effective={eff} {unit} (tolerance={tol})."
            )
        lines.append(f"  PASS  {name:<11} configured={cfg} {unit}  effective={eff} {unit}")

    return lines


# ----------------------------------------------------------------------------- #
# Bounded mock load generator                                                    #
# ----------------------------------------------------------------------------- #
def run_mock_load(*, target_rate, duration_s, max_iterations, dry_run, batch=500):
    """Simulate high-throughput tick/candle telemetry, bounded on BOTH duration
    and iteration count. dry_run (default) models the work without sustained
    allocation; non-dry-run holds a small bounded working set per batch.

    NO network, NO disk writes, NO deletes. Pure CPU/alloc churn within caps.
    Emits periodic progress and returns the processed-record count.
    """
    print(f"[mock-load] start  rate={target_rate}/s  duration={duration_s}s  "
          f"max_iter={max_iterations}  dry_run={dry_run}")
    start = time.monotonic()
    processed = 0
    next_report = start + 1.0
    sleep_per_batch = (batch / target_rate) if target_rate > 0 else 0.0

    while True:
        now = time.monotonic()
        if now - start >= duration_s:
            break
        if processed >= max_iterations:
            print(f"[mock-load] iteration ceiling {max_iterations} reached — stopping (bounded).")
            break

        # Synthesise a bounded batch of fake tick/candle records.
        if dry_run:
            # Model the work: cheap arithmetic, no retained allocation.
            acc = 0
            for i in range(batch):
                acc += (i * 2654435761) & 0xFFFFFFFF
            processed += batch
            _ = acc  # discard
        else:
            # Hold a small, bounded working set to exercise the mem cap under churn.
            window = [
                {"ts": now, "bid": 1.10000 + (j % 7) * 1e-5, "ask": 1.10010 + (j % 5) * 1e-5,
                 "vol": j % 1000}
                for j in range(batch)
            ]
            # "Aggregate" into a synthetic candle, then drop the window (no growth).
            hi = max(r["ask"] for r in window)
            lo = min(r["bid"] for r in window)
            _candle = {"o": window[0]["bid"], "h": hi, "l": lo, "c": window[-1]["ask"]}
            processed += batch
            del window, _candle

        if sleep_per_batch:
            time.sleep(sleep_per_batch)

        if now >= next_report:
            elapsed = now - start
            rate = processed / elapsed if elapsed else 0.0
            print(f"[mock-load] t+{elapsed:5.1f}s  processed={processed:>10}  ~{rate:,.0f} rec/s")
            next_report = now + 1.0

    elapsed = time.monotonic() - start
    rate = processed / elapsed if elapsed else 0.0
    print(f"[mock-load] done   processed={processed} in {elapsed:.1f}s  (~{rate:,.0f} rec/s)")
    return processed


# ----------------------------------------------------------------------------- #
# CLI                                                                            #
# ----------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(
        description="HERMES staging resource-cap verification (cgroup v2 vs compose pins) "
                    "+ bounded mock telemetry load. Run INSIDE the hermes-signal container.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--compose-file", default=DEFAULT_COMPOSE,
                   help="docker-compose.yml to read configured cap defaults from.")
    # Manual overrides (take precedence over compose-parsed defaults; also honoured from env).
    p.add_argument("--cpus", type=float, default=None,
                   help="Override configured cpus (else compose default or HERMES_CPUS env).")
    p.add_argument("--mem", default=None,
                   help="Override configured mem_limit, e.g. '4g' (else compose / HERMES_MEM_LIMIT).")
    p.add_argument("--pids", type=int, default=None,
                   help="Override configured pids_limit (else compose / HERMES_PIDS_LIMIT).")
    p.add_argument("--mem-tolerance", type=float, default=0.0,
                   help="Fractional slack on memory cap comparison (0.0 = exact).")
    # Mock load (default OFF; when on, default DRY-RUN).
    p.add_argument("--load", action="store_true",
                   help="Run the bounded mock telemetry load after the cap assertion.")
    p.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                   help="Allow the mock load to hold a bounded working set (still bounded & safe).")
    p.add_argument("--rate", type=int, default=50_000,
                   help="Target synthetic records/sec for the mock load.")
    p.add_argument("--duration", type=float, default=10.0,
                   help="Mock-load duration ceiling in seconds.")
    p.add_argument("--max-iterations", type=int, default=5_000_000,
                   help="Mock-load processed-record ceiling (hard backstop).")
    p.add_argument("--skip-assert", action="store_true",
                   help="Skip the cap assertion (load-only smoke; NOT for sign-off).")
    p.set_defaults(dry_run=True)
    return p


def resolve_configured(args):
    """Merge: explicit CLI override > env var > compose default."""
    cfg = configured_caps_from_compose(args.compose_file)

    cpus = args.cpus if args.cpus is not None else os.getenv("HERMES_CPUS")
    if cpus is not None:
        cfg["cpus"] = parse_cpus(cpus)

    mem = args.mem if args.mem is not None else os.getenv("HERMES_MEM_LIMIT")
    if mem is not None:
        cfg["mem_bytes"] = parse_mem_to_bytes(mem)

    pids = args.pids if args.pids is not None else os.getenv("HERMES_PIDS_LIMIT")
    if pids is not None:
        cfg["pids"] = int(pids)

    return cfg


def main(argv=None):
    args = build_parser().parse_args(argv)

    print("=" * 70)
    print("HERMES-OPS-STAGE-CAP-001 — staging resource-cap verification")
    print("=" * 70)

    overall_pass = True
    fail_code = None

    if not args.skip_assert:
        configured = resolve_configured(args)
        print(f"[config]  cpus={configured.get('cpus')}  "
              f"mem_bytes={configured.get('mem_bytes')}  pids={configured.get('pids')}  "
              f"(source: {args.compose_file} + overrides)")
        try:
            for line in assert_caps(configured, mem_tolerance=args.mem_tolerance):
                print(line)
            print("[assert]  CAP ASSERTION: PASS — effective cgroup caps match compose pins.")
        except CapFailure as exc:
            print(f"[assert]  CAP ASSERTION: FAIL")
            print(f"          {exc.message}", file=sys.stderr)
            overall_pass = False
            fail_code = exc.code
    else:
        print("[assert]  SKIPPED (--skip-assert) — load-only smoke, NOT valid for sign-off.")

    if args.load and overall_pass:
        print("-" * 70)
        run_mock_load(
            target_rate=args.rate,
            duration_s=args.duration,
            max_iterations=args.max_iterations,
            dry_run=args.dry_run,
        )
    elif args.load and not overall_pass:
        print("[mock-load] SKIPPED — cap assertion failed; not exercising load under unknown caps.")

    print("=" * 70)
    if overall_pass:
        print("RESULT: PASS")
        return 0
    # Exit code ENCODES the GOV class (001->1, 002->2, 003->3) so a wrapping boot gate can map the
    # failure class straight from the return code — every failure no longer collapses to exit 1.
    exit_code = {GOV_STAGE_CAP_MISMATCH: 1, GOV_STAGE_CAP_READ: 2, GOV_STAGE_CAP_PARSE: 3}.get(fail_code, 1)
    print(f"RESULT: FAIL ({fail_code or GOV_STAGE_CAP_MISMATCH}) -> exit {exit_code}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
