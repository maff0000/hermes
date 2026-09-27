"""market_truth.acquisition.orchestration.config -- HMT-2 ops configuration (3-tier model).

This module is the single place every Trinity-specific (or dell-debian-specific, or any future
host's) value used by the HMT-2 canonical-orchestration mechanism is resolved from, so that the
SAME implementation (`canonical_root_orchestrator.py`, the launcher templates, the disk guard)
can run correctly on any host via EXTERNAL CONFIGURATION ONLY -- never an application constant.

Mirrors the 3-tier doctrine already established elsewhere in this repository:

  Tier 1 -- defaults/schema (this file): safe, host-agnostic defaults and the typed shape of
            every configurable value.
  Tier 2 -- environment/deployment configuration: every field below is overridable by an
            environment variable of the same name (see `ENV_VAR_NAMES`), exactly mirroring the
            already-in-Git `market_truth.acquisition.canonical_worker.resolve_canonical_research_root()`
            convention (explicit override > environment variable > default).
  Tier 3 -- secrets (file-backed, never read here): this module resolves only the PATH to the
            Databento credential file (`acquire_secret_path`) for documentation/wiring purposes;
            the credential VALUE is read exclusively by the acquisition-side loader function in
            `market_truth.acquisition.providers.databento_historical` (deliberately not named
            again here -- see that module directly), inside the acquisition worker process
            (uid=hmt-data), never by any orchestration module, and never logged/printed/returned
            by anything in this package.

No host-specific numeric uid/gid, path, or resource-envelope value is ever hardcoded as a Python
constant outside this file's DEFAULT_* fallbacks (which exist only so tests and docs have a
concrete, clearly-labelled example to point at -- production deployments are expected to set the
environment variables, not rely on these defaults).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional


class ConfigError(ValueError):
    """Raised when a required HMT-2 ops configuration value is missing or malformed."""


# ---------------------------------------------------------------------------------------------
# Environment variable names (Tier 2). Every one of these is a *convention*, not a secret --
# safe to document, log, and commit.
# ---------------------------------------------------------------------------------------------
ENV_REPO_DIR = "HMT2_OPS_REPO_DIR"
ENV_VENV_PYTHON = "HMT2_OPS_VENV_PYTHON"
ENV_LAUNCHER_PATH = "HMT2_OPS_LAUNCHER_PATH"
ENV_ACQUIRE_LAUNCHER_PATH = "HMT2_OPS_ACQUIRE_LAUNCHER_PATH"
ENV_CANONICAL_RESEARCH_ROOT = "HMT2_CANONICAL_RESEARCH_ROOT"  # already-in-Git name, reused as-is
ENV_SCRATCH_DIR = "HMT2_OPS_SCRATCH_DIR"
ENV_EVIDENCE_ROOT = "HMT2_OPS_EVIDENCE_ROOT"
ENV_ORACLE_PATH = "HMT2_OPS_ORACLE_PATH"
ENV_ACQUIRE_SECRET_PATH = "HMT2_OPS_ACQUIRE_SECRET_PATH"  # path only -- never the secret value
ENV_SLICE_NAME = "HMT2_OPS_SLICE_NAME"
ENV_DISK_GUARD_MOUNT = "HMT2_OPS_DISK_GUARD_MOUNT"
ENV_DISK_GUARD_WARN_PCT = "HMT2_OPS_DISK_GUARD_WARN_PCT"
ENV_DISK_GUARD_STOP_PCT = "HMT2_OPS_DISK_GUARD_STOP_PCT"
ENV_PER_SESSION_TIMEOUT_SEC = "HMT2_OPS_PER_SESSION_TIMEOUT_SEC"
ENV_PRE_SESSION_MIN_MEMAVAILABLE_KB = "HMT2_OPS_PRE_SESSION_MIN_MEMAVAILABLE_KB"
ENV_IN_SESSION_MIN_MEMAVAILABLE_KB = "HMT2_OPS_IN_SESSION_MIN_MEMAVAILABLE_KB"
ENV_SUSTAINED_LOW_MEM_SECONDS = "HMT2_OPS_SUSTAINED_LOW_MEM_SECONDS"
ENV_POLL_INTERVAL_SEC = "HMT2_OPS_POLL_INTERVAL_SEC"
ENV_PSI_FULL_AVG10_STALL_THRESHOLD = "HMT2_OPS_PSI_FULL_AVG10_STALL_THRESHOLD"
ENV_ROUTINE_MEMORY_HIGH_BYTES = "HMT2_OPS_ROUTINE_MEMORY_HIGH_BYTES"
ENV_ROUTINE_MEMORY_MAX_BYTES = "HMT2_OPS_ROUTINE_MEMORY_MAX_BYTES"
ENV_EXCEPTIONAL_PREFLIGHT_MIN_MEMAVAILABLE_KB = "HMT2_OPS_EXCEPTIONAL_PREFLIGHT_MIN_MEMAVAILABLE_KB"
ENV_EXCEPTIONAL_AUTHORISED_CEILING_MEMORY_MAX_BYTES = "HMT2_OPS_EXCEPTIONAL_AUTHORISED_CEILING_MEMORY_MAX_BYTES"
ENV_MAX_CANONICAL_CHUNK_SESSIONS = "HMT2_OPS_MAX_CANONICAL_CHUNK_SESSIONS"

ENV_VAR_NAMES = tuple(
    value for name, value in list(globals().items()) if name.startswith("ENV_") and isinstance(value, str)
)

_GIB = 1024 * 1024  # in KiB units, i.e. 1 GiB expressed in KiB
_GIB_BYTES = 1024 * 1024 * 1024


def _get(env: Mapping[str, str], name: str) -> Optional[str]:
    val = env.get(name)
    return val if val not in (None, "") else None


def _get_str(env: Mapping[str, str], name: str, default: Optional[str]) -> Optional[str]:
    val = _get(env, name)
    return val if val is not None else default


def _get_int(env: Mapping[str, str], name: str, default: int) -> int:
    val = _get(env, name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError as exc:
        raise ConfigError(f"{name}={val!r} is not a valid integer") from exc


def _get_float(env: Mapping[str, str], name: str, default: float) -> float:
    val = _get(env, name)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError as exc:
        raise ConfigError(f"{name}={val!r} is not a valid float") from exc


@dataclass(frozen=True)
class Hmt2OpsConfig:
    """Fully-resolved HMT-2 ops configuration for one host. Every field here is either a path,
    an identity/naming convention, a policy threshold, or a resource-envelope value -- never
    application/domain logic (that stays in the already-in-Git ledger/canonicalise modules)."""

    # Paths -- all Trinity-specific / host-specific in value, generic in convention.
    repo_dir: str
    venv_python: str
    launcher_path: str
    acquire_launcher_path: str
    canonical_research_root: Optional[str]  # None => defer to canonical_worker's own resolution
    scratch_dir: str
    evidence_root: str
    oracle_path: Optional[str]
    acquire_secret_path: Optional[str]  # PATH only, never the secret value

    # Naming / identity conventions.
    slice_name: str

    # Disk guard policy.
    disk_guard_mount: str
    disk_guard_warn_pct: int
    disk_guard_stop_pct: int

    # Per-session / host-guard policy.
    per_session_timeout_sec: int
    pre_session_min_memavailable_kb: int
    in_session_min_memavailable_kb: int
    sustained_low_mem_seconds: int
    poll_interval_sec: int
    psi_full_avg10_stall_threshold: float

    # Routine resource envelope (cgroup, hmt2.slice).
    routine_memory_high_bytes: int
    routine_memory_max_bytes: int

    # Exceptional envelope policy.
    exceptional_preflight_min_memavailable_kb: int
    exceptional_authorised_ceiling_memory_max_bytes: int

    # Bounded chunking doctrine.
    max_canonical_chunk_sessions: int

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Hmt2OpsConfig":
        """Resolve every field from `env` (defaults to `os.environ`), falling back to the
        documented, clearly-host-agnostic-example defaults below. `env` is an injectable seam
        purely for tests -- production callers should never pass it."""
        env = os.environ if env is None else env

        repo_dir = _get_str(env, ENV_REPO_DIR, "/srv/hmt-code/hermes")
        cfg = cls(
            repo_dir=repo_dir,
            venv_python=_get_str(env, ENV_VENV_PYTHON, "/srv/hmt-code/hmt2-venv/bin/python3"),
            launcher_path=_get_str(env, ENV_LAUNCHER_PATH, "/usr/local/sbin/hmt2-run.sh"),
            acquire_launcher_path=_get_str(env, ENV_ACQUIRE_LAUNCHER_PATH, "/usr/local/sbin/hmt2-acquire-run.sh"),
            canonical_research_root=_get(env, ENV_CANONICAL_RESEARCH_ROOT),
            scratch_dir=_get_str(env, ENV_SCRATCH_DIR, "/srv/hmt-data/scratch"),
            evidence_root=_get_str(env, ENV_EVIDENCE_ROOT, "/srv/hmt-data/evidence"),
            oracle_path=_get(env, ENV_ORACLE_PATH),
            acquire_secret_path=_get(env, ENV_ACQUIRE_SECRET_PATH),
            slice_name=_get_str(env, ENV_SLICE_NAME, "hmt2.slice"),
            disk_guard_mount=_get_str(env, ENV_DISK_GUARD_MOUNT, "/srv"),
            disk_guard_warn_pct=_get_int(env, ENV_DISK_GUARD_WARN_PCT, 70),
            disk_guard_stop_pct=_get_int(env, ENV_DISK_GUARD_STOP_PCT, 80),
            per_session_timeout_sec=_get_int(env, ENV_PER_SESSION_TIMEOUT_SEC, 900),
            pre_session_min_memavailable_kb=_get_int(env, ENV_PRE_SESSION_MIN_MEMAVAILABLE_KB, 40 * _GIB),
            in_session_min_memavailable_kb=_get_int(env, ENV_IN_SESSION_MIN_MEMAVAILABLE_KB, 24 * _GIB),
            sustained_low_mem_seconds=_get_int(env, ENV_SUSTAINED_LOW_MEM_SECONDS, 60),
            poll_interval_sec=_get_int(env, ENV_POLL_INTERVAL_SEC, 5),
            psi_full_avg10_stall_threshold=_get_float(env, ENV_PSI_FULL_AVG10_STALL_THRESHOLD, 5.0),
            routine_memory_high_bytes=_get_int(env, ENV_ROUTINE_MEMORY_HIGH_BYTES, 12 * _GIB_BYTES),
            routine_memory_max_bytes=_get_int(env, ENV_ROUTINE_MEMORY_MAX_BYTES, 16 * _GIB_BYTES),
            exceptional_preflight_min_memavailable_kb=_get_int(
                env, ENV_EXCEPTIONAL_PREFLIGHT_MIN_MEMAVAILABLE_KB, 40 * _GIB
            ),
            exceptional_authorised_ceiling_memory_max_bytes=_get_int(
                env, ENV_EXCEPTIONAL_AUTHORISED_CEILING_MEMORY_MAX_BYTES, 24 * _GIB_BYTES
            ),
            max_canonical_chunk_sessions=_get_int(env, ENV_MAX_CANONICAL_CHUNK_SESSIONS, 10),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.in_session_min_memavailable_kb >= self.pre_session_min_memavailable_kb:
            # Not strictly required to be smaller, but a routine misconfiguration (in-session
            # floor set >= the pre-session gate) would make the pre-session gate meaningless --
            # fail loud rather than silently accept a nonsensical policy.
            raise ConfigError(
                "in_session_min_memavailable_kb must be strictly less than "
                "pre_session_min_memavailable_kb (got "
                f"{self.in_session_min_memavailable_kb} >= {self.pre_session_min_memavailable_kb})"
            )
        if self.routine_memory_high_bytes >= self.routine_memory_max_bytes:
            raise ConfigError(
                "routine_memory_high_bytes must be strictly less than routine_memory_max_bytes"
            )
        if self.disk_guard_warn_pct >= self.disk_guard_stop_pct:
            raise ConfigError("disk_guard_warn_pct must be strictly less than disk_guard_stop_pct")
        if self.max_canonical_chunk_sessions <= 0:
            raise ConfigError("max_canonical_chunk_sessions must be positive")
        if self.exceptional_authorised_ceiling_memory_max_bytes <= self.routine_memory_max_bytes:
            raise ConfigError(
                "exceptional_authorised_ceiling_memory_max_bytes must be greater than "
                "routine_memory_max_bytes (an 'exceptional' envelope that is not larger than "
                "routine is not exceptional)"
            )

    def research_hmt2_dir(self) -> str:
        """Where the already-in-Git `hmt2h_gc_corpus_ledger.py` / `hmt2i_gc_corpus_canonical_ledger.py`
        modules live -- these are imported as top-level modules (not a package), exactly like
        `research/hmt2/hmt2i_gc_corpus_canonicalise.py` itself does, via `sys.path` insertion.
        See `canonical_root_orchestrator._import_ledger_modules()`."""
        return os.path.join(self.repo_dir, "research", "hmt2")
