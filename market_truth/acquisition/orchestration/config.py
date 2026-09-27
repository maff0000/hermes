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
constant outside this file. Two different kinds of "default" exist here, deliberately treated
differently:

  * Generic policy/tuning fields (timeouts, memory floors/ceilings, percentages, the slice name,
    chunk-size limits) are not host-identifying -- the same sane value is expected to be correct
    on any host until a deployer chooses to override it -- so these keep concrete numeric
    defaults below.
  * Path fields (`repo_dir`, `venv_python`, `launcher_path`, `acquire_launcher_path`,
    `scratch_dir`, `evidence_root`, `disk_guard_mount`) have NO default. A plausible-looking
    fallback path here is actively dangerous: an unset env var on some future host would
    silently resolve to a real-looking-but-wrong location instead of failing. These fields
    resolve to `None` when their env var is unset, and `validate()` raises a `ConfigError`
    naming every missing env var before `from_env()` ever returns -- fail loud, not silently
    plausible.
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

    # Paths -- all host-specific in value, generic in convention. No safe cross-host default
    # exists for any of these; None means "not yet configured" and `validate()` rejects it.
    repo_dir: Optional[str]
    venv_python: Optional[str]
    launcher_path: Optional[str]
    acquire_launcher_path: Optional[str]
    canonical_research_root: Optional[str]  # None => defer to canonical_worker's own resolution
    scratch_dir: Optional[str]
    evidence_root: Optional[str]
    oracle_path: Optional[str]
    acquire_secret_path: Optional[str]  # PATH only, never the secret value

    # Naming / identity conventions.
    slice_name: str

    # Disk guard policy. `disk_guard_mount` is a path -- no safe cross-host default (see above).
    disk_guard_mount: Optional[str]
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
        """Resolve every field from `env` (defaults to `os.environ`). Generic policy/tuning
        fields fall back to the documented host-agnostic defaults below; path fields have no
        fallback and raise a `ConfigError` via `validate()` if left unset. `env` is an
        injectable seam purely for tests -- production callers should never pass it."""
        env = os.environ if env is None else env

        repo_dir = _get(env, ENV_REPO_DIR)
        cfg = cls(
            # Path fields: no fallback. An unset env var must surface as a loud ConfigError from
            # validate() below, never as a silently-plausible-looking default.
            repo_dir=repo_dir,
            venv_python=_get(env, ENV_VENV_PYTHON),
            launcher_path=_get(env, ENV_LAUNCHER_PATH),
            acquire_launcher_path=_get(env, ENV_ACQUIRE_LAUNCHER_PATH),
            canonical_research_root=_get(env, ENV_CANONICAL_RESEARCH_ROOT),
            scratch_dir=_get(env, ENV_SCRATCH_DIR),
            evidence_root=_get(env, ENV_EVIDENCE_ROOT),
            oracle_path=_get(env, ENV_ORACLE_PATH),
            acquire_secret_path=_get(env, ENV_ACQUIRE_SECRET_PATH),
            slice_name=_get_str(env, ENV_SLICE_NAME, "hmt2.slice"),
            disk_guard_mount=_get(env, ENV_DISK_GUARD_MOUNT),
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

    # Fields with no safe cross-host default: (attribute name, env var name). Every one of these
    # MUST be set explicitly per host (e.g. via /etc/hmt2/hmt2-ops.env) -- there is no plausible
    # value that is safe to guess on a host that hasn't configured it.
    _REQUIRED_NO_DEFAULT_FIELDS = (
        ("repo_dir", ENV_REPO_DIR),
        ("venv_python", ENV_VENV_PYTHON),
        ("launcher_path", ENV_LAUNCHER_PATH),
        ("acquire_launcher_path", ENV_ACQUIRE_LAUNCHER_PATH),
        ("scratch_dir", ENV_SCRATCH_DIR),
        ("evidence_root", ENV_EVIDENCE_ROOT),
        ("disk_guard_mount", ENV_DISK_GUARD_MOUNT),
    )

    def validate(self) -> None:
        missing_env_vars = [
            env_name
            for field_name, env_name in self._REQUIRED_NO_DEFAULT_FIELDS
            if getattr(self, field_name) is None
        ]
        if missing_env_vars:
            raise ConfigError(
                "missing required HMT-2 ops configuration -- the following environment "
                "variable(s) must be set (no safe cross-host default exists for a host-specific "
                "path): " + ", ".join(missing_env_vars)
            )
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
