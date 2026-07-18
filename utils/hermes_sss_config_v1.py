"""HERMES shared-stream recovery Phase-2 — SHADOW ADAPTER CONFIG MODEL (PRODUCTION-OWNED, INERT).

WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE2-SHADOW-ADAPTER-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-17. Owner: HERMES (Helm).
Contract: docs/design/shared_stream_recovery/architecture_phase2_v1.md §17 +
schemas/shared_stream_recovery/shadow_adapter_config.v1.schema.json (binding). Contract version "1".

STATUS: INERT / NOT WIRED. Imported by NO live runtime path; only tests + sibling utils/hermes_sss_* modules.
Config CONTENT lives in governed hermes_config (DB), NOT in code. This module is only the versioned, validated
MODEL + a pure loader. Loading is inert unless explicitly invoked and reads NOTHING from os.environ / files — the
raw config mapping is passed in by the (future) caller.

BINDING FAIL-SAFE RULES (§17):
  * shadow_enabled DEFAULTS false. Absent/malformed config -> DISABLED. No implicit enable anywhere. No source
    constant or env fallback can turn it on.
  * shadow_consumer_live is a const false; a true value is a validation error -> DISABLED.
  * Any unknown mandatory / invalid value -> fail closed (DISABLED), never a partially-applied enable.
  * heartbeat / shared-progress horizons are PROVISIONAL (flagged, not calibrated-truth); hard >= soft; out-of-range
    -> DISABLED.
  * No secrets in config (rejected via the redaction layer).

Standard-library only + the redaction layer. Pure; frozen dataclass; deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Tuple

from utils.hermes_sss_redaction_v1 import scan_for_prohibited

CONFIG_VERSION = "1"

# Horizons are PROVISIONAL — flagged for Phase-2 empirical calibration, never presented as calibrated truth.
HORIZONS_PROVISIONAL = True

_DEFAULT_OUTPUT_PATH = "ops/evidence/shadow_stream_recovery/decisions.jsonl"


@dataclass(frozen=True)
class ShadowAdapterConfig:
    """Immutable, validated shadow-adapter configuration. Construct ONLY via load_config (which fails closed)."""
    config_version: str
    shadow_enabled: bool
    shadow_consumer_live: bool
    shadow_eval_cadence_sec: int
    heartbeat_soft_horizon_s: float
    heartbeat_hard_horizon_s: float
    shared_progress_soft_horizon_s: float
    shared_progress_hard_horizon_s: float
    callback_dedup_window_sec: float
    max_callback_shadow_events_per_min: int
    snapshot_retention_days: int
    max_jsonl_bytes: int
    max_retained_files: int
    write_timeout_s: float
    log_sampling_ratio: float
    evidence_output_path: str
    alert_on_comparison_classes: Tuple[str, ...]
    horizons_provisional: bool = HORIZONS_PROVISIONAL
    disabled_reason: Optional[str] = None

    @property
    def enabled(self) -> bool:
        """The ONLY gate the adapter consults. Never true unless a valid config explicitly set shadow_enabled True
        AND consumer_live remained False."""
        return bool(self.shadow_enabled) and self.shadow_consumer_live is False


def disabled_config(reason: str) -> ShadowAdapterConfig:
    """The canonical DISABLED, fail-safe config. shadow_enabled is False; enabled is False."""
    return ShadowAdapterConfig(
        config_version=CONFIG_VERSION,
        shadow_enabled=False,
        shadow_consumer_live=False,
        shadow_eval_cadence_sec=15,
        heartbeat_soft_horizon_s=15.0,
        heartbeat_hard_horizon_s=45.0,
        shared_progress_soft_horizon_s=15.0,
        shared_progress_hard_horizon_s=60.0,
        callback_dedup_window_sec=2.0,
        max_callback_shadow_events_per_min=20,
        snapshot_retention_days=30,
        max_jsonl_bytes=8 * 1024 * 1024,
        max_retained_files=14,
        write_timeout_s=2.0,
        log_sampling_ratio=1.0,
        evidence_output_path=_DEFAULT_OUTPUT_PATH,
        alert_on_comparison_classes=(
            "SHADOW_DENIES_CURRENT_RECONNECT", "SHADOW_AUTHORIZES_CURRENT_NO_RECONNECT",
            "EVIDENCE_CONFLICT", "ADAPTER_ERROR",
        ),
        disabled_reason=reason,
    )


def _num(raw: Mapping[str, object], key: str, default: float, lo: float, hi: float) -> float:
    v = raw.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise _Invalid(f"{key} must be a number")
    if not (lo <= float(v) <= hi):
        raise _Invalid(f"{key}={v} out of range [{lo},{hi}]")
    return float(v)


def _int(raw: Mapping[str, object], key: str, default: int, lo: int, hi: int) -> int:
    v = raw.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int):
        raise _Invalid(f"{key} must be an int")
    if not (lo <= v <= hi):
        raise _Invalid(f"{key}={v} out of range [{lo},{hi}]")
    return int(v)


class _Invalid(Exception):
    pass


def load_config(raw: Optional[Mapping[str, object]]) -> ShadowAdapterConfig:
    """Validate an externally-supplied config mapping (from governed hermes_config) into a ShadowAdapterConfig.
    FAIL CLOSED: any problem -> disabled_config(reason). Never raises to the caller; never reads os.environ or a
    file; never partially enables. Loading is a no-op until explicitly invoked here.

    A MISSING config, a non-mapping, a malformed value, an out-of-range horizon, shadow_consumer_live=True, or a
    secret in the config all resolve to a DISABLED config."""
    if raw is None:
        return disabled_config("config absent -> disabled")
    if not isinstance(raw, Mapping):
        return disabled_config("config is not a mapping -> disabled")

    # No secrets in config (§17). A prohibited field disables the whole adapter loudly.
    secret_violations = scan_for_prohibited(dict(raw))
    if secret_violations:
        return disabled_config(f"config contains prohibited field(s) -> disabled ({secret_violations[0].kind})")

    try:
        # shadow_enabled MUST be an explicit bool True. Anything else (absent, string 'true', 1) -> disabled.
        enabled_raw = raw.get("shadow_enabled", False)
        if not isinstance(enabled_raw, bool):
            raise _Invalid("shadow_enabled must be an explicit boolean (no implicit/coerced enable)")

        consumer_live_raw = raw.get("shadow_consumer_live", False)
        if not isinstance(consumer_live_raw, bool):
            raise _Invalid("shadow_consumer_live must be a boolean")
        if consumer_live_raw is not False:
            raise _Invalid("shadow_consumer_live MUST be false in Phase 2 (no live consumer is permitted)")

        cadence = _int(raw, "shadow_eval_cadence_sec", 15, 5, 300)
        hb_soft = _num(raw, "heartbeat_soft_horizon_s", 15.0, 1, 120)
        hb_hard = _num(raw, "heartbeat_hard_horizon_s", 45.0, 1, 300)
        if hb_hard < hb_soft:
            raise _Invalid("heartbeat_hard_horizon_s must be >= heartbeat_soft_horizon_s")
        sp_soft = _num(raw, "shared_progress_soft_horizon_s", 15.0, 1, 300)
        sp_hard = _num(raw, "shared_progress_hard_horizon_s", 60.0, 1, 600)
        if sp_hard < sp_soft:
            raise _Invalid("shared_progress_hard_horizon_s must be >= shared_progress_soft_horizon_s")
        dedup = _num(raw, "callback_dedup_window_sec", 2.0, 0.1, 60)
        max_cb = _int(raw, "max_callback_shadow_events_per_min", 20, 1, 120)
        retention = _int(raw, "snapshot_retention_days", 30, 1, 365)
        max_bytes = _int(raw, "max_jsonl_bytes", 8 * 1024 * 1024, 1024, 1024 * 1024 * 1024)
        max_files = _int(raw, "max_retained_files", 14, 1, 3650)
        write_timeout = _num(raw, "write_timeout_s", 2.0, 0.05, 60)
        sampling = _num(raw, "log_sampling_ratio", 1.0, 0, 1)

        out_path = raw.get("evidence_output_path", _DEFAULT_OUTPUT_PATH)
        if not isinstance(out_path, str) or not out_path:
            raise _Invalid("evidence_output_path must be a non-empty string")

        alerts_raw = raw.get("alert_on_comparison_classes",
                             ["SHADOW_DENIES_CURRENT_RECONNECT", "SHADOW_AUTHORIZES_CURRENT_NO_RECONNECT",
                              "EVIDENCE_CONFLICT", "ADAPTER_ERROR"])
        if not isinstance(alerts_raw, (list, tuple)) or not all(isinstance(a, str) for a in alerts_raw):
            raise _Invalid("alert_on_comparison_classes must be a list of strings")
    except _Invalid as exc:
        return disabled_config(f"invalid config -> disabled ({exc})")

    return ShadowAdapterConfig(
        config_version=str(raw.get("config_version", CONFIG_VERSION)),
        shadow_enabled=enabled_raw,
        shadow_consumer_live=False,
        shadow_eval_cadence_sec=cadence,
        heartbeat_soft_horizon_s=hb_soft,
        heartbeat_hard_horizon_s=hb_hard,
        shared_progress_soft_horizon_s=sp_soft,
        shared_progress_hard_horizon_s=sp_hard,
        callback_dedup_window_sec=dedup,
        max_callback_shadow_events_per_min=max_cb,
        snapshot_retention_days=retention,
        max_jsonl_bytes=max_bytes,
        max_retained_files=max_files,
        write_timeout_s=write_timeout,
        log_sampling_ratio=sampling,
        evidence_output_path=out_path,
        alert_on_comparison_classes=tuple(alerts_raw),
        horizons_provisional=HORIZONS_PROVISIONAL,
        disabled_reason=None,
    )
