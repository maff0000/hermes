"""market_truth.acquisition.hmt2_slice_guard — fail-closed HMT-2 cgroup-containment guard.

WHY THIS EXISTS
----------------
`hmt2.slice` (`/etc/systemd/system/hmt2.slice`) is a host-level systemd cgroup boundary that
caps the WHOLE HMT-2 process tree (`MemoryMax=10G`/`MemoryHigh=8G`/`MemorySwapMax=0`/
`CPUQuota=400%`) so that real-corpus compute (canonicalisation, acquisition) can never starve
the shared production services also running on this host (MariaDB, Redis, Graylog, Elasticsearch,
an active IBGateway live-trading process). The INTENDED mechanism is
`/usr/local/sbin/hmt2-run.sh`, which places any command inside the slice via
`systemd-run --scope --slice=hmt2.slice ...` before exec'ing it.

That mechanism is opt-in and depends entirely on the invoking engineer remembering to use the
wrapper. A real full-corpus regression sweep was found running OUTSIDE the slice (in
`user.slice` instead) precisely because it was launched directly (`nohup python3 ... &`) rather
than through `hmt2-run.sh` — a process-discipline lapse, not a data-integrity one, but proof the
boundary can silently be bypassed by anyone who just runs Python directly.

This module makes that bypass structurally harder: `require_hmt2_slice()` is a small, fail-closed
check that every real-data HMT-2 entry point's `main()` calls FIRST, before any file I/O against
retained corpus data (`research-source/`, `research-canonical-store/`). It reads this process's
own cgroup membership and refuses to proceed (raises `RuntimeError`) unless it can see that the
process is running inside `hmt2.slice` (or a scope/service nested under it — the shape
`hmt2-run.sh` produces, e.g. `0::/hmt2.slice/hmt2-20260924T194554Z-1996826.scope`).

USAGE
-----
    from market_truth.acquisition.hmt2_slice_guard import require_hmt2_slice

    def main() -> None:
        require_hmt2_slice(script_name="hmt2i_gc_corpus_canonicalise.py")
        ...  # everything else, unchanged

TEST-ONLY BYPASS
-----------------
A tiny synthetic/unit-fixture test that imports and exercises a real-data entry point's `main()`
directly (rather than the pure functions it delegates to) would otherwise be unable to run
outside the slice in CI/dev. For that narrow case ONLY, setting the environment variable named
by `ALLOW_OUTSIDE_SLICE_ENV_VAR` (`HMT2_ALLOW_OUTSIDE_SLICE_FOR_TESTS`) to the exact string
`"1"` lets `require_hmt2_slice()` proceed anyway — but it ALWAYS prints a loud warning to stderr
when it does, so the bypass is never silently invisible. This is deliberately NOT a short or
easily-fat-fingered flag name, and it is NEVER the default. It must never be set for a real
retained-corpus session run — only for tiny synthetic/unit-fixture workloads.

TESTING SEAM
------------
`require_hmt2_slice()` takes an injectable `read_cgroup` callable (zero-argument, returns the
raw text of `/proc/self/cgroup`) so tests can simulate being inside/outside the slice without
touching the real filesystem or environment. Production callers should never pass it.
"""
from __future__ import annotations

import os
import sys

# The exact slice name this guard looks for — must match the `Slice=` unit name in
# /etc/systemd/system/hmt2.slice and the `--slice=` argument /usr/local/sbin/hmt2-run.sh passes
# to `systemd-run`.
HMT2_SLICE_NAME = "hmt2.slice"

# Deliberately long and unambiguous — never a short/easily-fat-fingered flag name (requirement:
# this bypass must never be easy to set by accident). Exact-string "1" is the only value that
# activates it; anything else (including "true"/"yes"/empty string) is treated as unset.
ALLOW_OUTSIDE_SLICE_ENV_VAR = "HMT2_ALLOW_OUTSIDE_SLICE_FOR_TESTS"
_ALLOW_OUTSIDE_SLICE_ACTIVATION_VALUE = "1"

DEFAULT_CGROUP_PATH = "/proc/self/cgroup"
HMT2_RUN_WRAPPER_PATH = "/usr/local/sbin/hmt2-run.sh"


def read_own_cgroup_text(cgroup_path: str = DEFAULT_CGROUP_PATH) -> str:
    """Reads this process's own cgroup membership straight from the kernel
    (`/proc/self/cgroup` by default). Isolated into its own function purely so
    `require_hmt2_slice()`'s `read_cgroup` parameter has a real default to fall back to —
    production callers should never need to call this directly or pass `cgroup_path`.
    """
    with open(cgroup_path, "r", encoding="utf-8") as fh:
        return fh.read()


def is_inside_hmt2_slice(cgroup_text: str) -> bool:
    """True if `cgroup_text` (the raw contents of `/proc/self/cgroup`) shows this process living
    under `hmt2.slice` — either directly, or in any scope/service nested under it (e.g.
    `0::/hmt2.slice/hmt2-20260924T194554Z-1996826.scope`, the exact shape
    `/usr/local/sbin/hmt2-run.sh` produces via `systemd-run --scope --slice=hmt2.slice`).

    Handles both the cgroup v2 unified-hierarchy line format (`0::/<path>`) and the legacy
    cgroup v1 per-controller format (`<id>:<controller-list>:/<path>`) — in both, the path is
    everything after the LAST `:` on the line. Matches `hmt2.slice` as a whole path SEGMENT
    (never a bare substring), so a differently-named slice that merely contains the string
    (e.g. a hypothetical `not-hmt2.slice-foo`) can never produce a false positive.
    """
    for line in cgroup_text.splitlines():
        line = line.strip()
        if not line:
            continue
        path = line.rsplit(":", 1)[-1]
        segments = [seg for seg in path.split("/") if seg]
        if HMT2_SLICE_NAME in segments:
            return True
    return False


def _bypass_env_value(env) -> str:
    return env.get(ALLOW_OUTSIDE_SLICE_ENV_VAR, "")


def require_hmt2_slice(
    *,
    script_name: str,
    read_cgroup=None,
    env=None,
) -> None:
    """Fail-closed HMT-2 slice-containment guard — call this as the FIRST thing inside a
    real-data HMT-2 entry point's `main()`, before ANY file I/O against retained corpus data
    (`research-source/`, `research-canonical-store/`).

    Raises `RuntimeError` — never silently warns and continues — if this process is not running
    inside `hmt2.slice`, UNLESS the explicit, narrow test-only bypass
    (`HMT2_ALLOW_OUTSIDE_SLICE_FOR_TESTS=1`) is set, in which case it prints a loud warning to
    stderr and returns.

    Parameters
    ----------
    script_name:
        The calling script's own filename (e.g. `"hmt2i_gc_corpus_canonicalise.py"`) — included
        in both the warning and the error so a caller always knows exactly which entry point
        tripped this guard.
    read_cgroup:
        Injectable, zero-argument callable returning the raw text of `/proc/self/cgroup`.
        Defaults to `read_own_cgroup_text`. TESTS ONLY — production callers should never pass
        this; it exists purely so tests can simulate being inside/outside the slice without
        touching the real filesystem.
    env:
        Injectable mapping to read `HMT2_ALLOW_OUTSIDE_SLICE_FOR_TESTS` from. Defaults to
        `os.environ`. TESTS ONLY.
    """
    env = os.environ if env is None else env
    read_cgroup = read_own_cgroup_text if read_cgroup is None else read_cgroup

    if _bypass_env_value(env) == _ALLOW_OUTSIDE_SLICE_ACTIVATION_VALUE:
        print(
            f"HMT2_SLICE_GUARD WARNING: {script_name} is proceeding OUTSIDE {HMT2_SLICE_NAME} "
            f"because {ALLOW_OUTSIDE_SLICE_ENV_VAR}=1 is set. This bypass exists ONLY for tiny "
            f"synthetic/unit-fixture workloads and must NEVER be set for a real retained-corpus "
            f"session run. If you did not deliberately set this for a test, unset "
            f"{ALLOW_OUTSIDE_SLICE_ENV_VAR} now and re-invoke via {HMT2_RUN_WRAPPER_PATH}.",
            file=sys.stderr,
        )
        return

    cgroup_text = read_cgroup()
    if is_inside_hmt2_slice(cgroup_text):
        return

    raise RuntimeError(
        f"REFUSING TO PROCEED: {script_name} is real HMT-2 corpus compute and must run inside "
        f"the {HMT2_SLICE_NAME} cgroup boundary (see /etc/systemd/system/{HMT2_SLICE_NAME}), but "
        f"this process is not running inside it (own cgroup: {cgroup_text.strip()!r}). This "
        f"boundary exists to protect shared production services (MariaDB, Redis, Graylog, "
        f"Elasticsearch, an active IBGateway live-trading process) on this host from HMT-2 "
        f"compute. Re-invoke this exact command through the mandatory launcher instead:\n\n"
        f"    {HMT2_RUN_WRAPPER_PATH} python3 <this script> <its original arguments>\n\n"
        f"If this is a tiny synthetic/unit-fixture test that genuinely needs to run outside the "
        f"slice, set {ALLOW_OUTSIDE_SLICE_ENV_VAR}=1 explicitly for that invocation only — never "
        f"for a real retained-corpus session run."
    )
