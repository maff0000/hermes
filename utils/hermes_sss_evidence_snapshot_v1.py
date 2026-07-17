"""HERMES shared-stream recovery Phase-2 — EVIDENCE SNAPSHOT model (PRODUCTION-OWNED, INERT).

WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE2-SHADOW-ADAPTER-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-17. Owner: HERMES (Helm).
Contract: docs/design/shared_stream_recovery/architecture_phase2_v1.md (binding). Contract version "1".
Base: canonical main b50162a2 (Phase-1 pure core merged at utils/hermes_shared_stream_recovery_v1.py; PR#110
design merged). Promotes the audited inert evidence-snapshot prototype under design/ into a production-owned
module under utils/, matching the utils/hermes_<domain>_v1.py convention. The design/ prototype stays untouched
as the audited artefact.

STATUS: INERT / NOT WIRED / NOT FOR MERGE-BY-DEPLOY. This module is imported by NO live runtime path
(main.py / utils/watchdog.py / adapters/oanda.py / adapters/base.py / provider client / any runner /
docker-compose / Dockerfile / cron / systemd). It is imported ONLY by tests and by sibling utils/hermes_sss_*
Phase-2 modules (also inert). Proven by the static-guard test in
tests/test_hermes_sss_shadow_adapter_v1.py.

PURITY: standard-library only + the Phase-1 core (utils.hermes_shared_stream_recovery_v1). No Redis / SQL / HTTP /
socket / subprocess / provider SDK; no wall-clock read at call (all timestamps are supplied); no logging side
effect; no thread/task/timer; no hidden mutable global. The snapshot is a frozen dataclass; its ONLY behaviour is
deterministic (de)serialisation + a PURE mapping to the Phase-1 core input.

HONESTY MODEL: every transport field carries FieldProvenance. Provenance records whether the value was
DIRECTLY_OBSERVED (a logged/measured fact), a JUSTIFIED_INFERENCE (sound derivation, NOT a measurement), or
UNAVAILABLE. Missing evidence is UNAVAILABLE — NEVER silently defaulted to a favourable (healthy) value.
"""
from __future__ import annotations

import datetime
import enum
import hashlib
import json
from dataclasses import dataclass, replace
from typing import Mapping, Optional, Tuple

import utils.hermes_shared_stream_recovery_v1 as core

UTC = datetime.timezone.utc

SNAPSHOT_VERSION = "1"
SUPPORTED_SNAPSHOT_VERSIONS = frozenset({"1"})

# The authoritative governed instrument inventory (architecture_phase2_v1 §7/§9). NO substring parsing, NO invented
# schedule. WTICO_USD / SPX500_USD are UNVALIDATED (schedule None) -> fail-loud, no transport vote.
GOVERNED_INSTRUMENTS: Tuple[str, ...] = (
    "AUD_USD", "EUR_GBP", "EUR_USD", "GBP_USD", "NZD_USD", "SPX500_USD", "USD_CAD",
    "USD_CHF", "USD_JPY", "WTICO_USD", "XAG_USD", "XAU_USD", "XCU_USD", "XPT_USD",
)
UNVALIDATED_INSTRUMENTS: Tuple[str, ...] = ("SPX500_USD", "WTICO_USD")

# The 21 RecoveryDecisionInput authority-bearing / classification fields the snapshot must supply.
RECOVERY_INPUT_FIELDS: Tuple[str, ...] = (
    "evaluated_at_utc", "provider", "contract_version", "config_version",
    "socket_state", "provider_disconnect_event", "auth_failure", "heartbeat_available", "heartbeat_age_s",
    "shared_stream_silent", "parser_fatal", "reconnect_in_progress",
    "shared_progress_available", "shared_progress_age_s",
    "provider_maintenance_indication", "error_count_delta",
    "heartbeat_soft_horizon_s", "heartbeat_hard_horizon_s",
    "instruments", "limiter", "evidence_summary",
)


class AvailabilityClass(str, enum.Enum):
    """Per-field evidence availability (architecture_phase2_v1 §2). Missing != favourable default."""
    AVAILABLE_AUTHORITATIVE = "AVAILABLE_AUTHORITATIVE"   # explicit control-plane truth
    AVAILABLE_DERIVED = "AVAILABLE_DERIVED"               # computed from a real observed surface
    AVAILABLE_ADVISORY = "AVAILABLE_ADVISORY"             # real but never alone a transport vote
    NOT_CURRENTLY_AVAILABLE = "NOT_CURRENTLY_AVAILABLE"   # no source in current code -> unavailable
    AMBIGUOUS = "AMBIGUOUS"                               # source exists but truth is unclear -> fail closed
    UNSAFE_TO_INFER = "UNSAFE_TO_INFER"                   # could be guessed, but must NOT be -> unavailable


class Observation(str, enum.Enum):
    """Directly-observed fact vs justified inference vs unavailable (the honesty axis)."""
    DIRECTLY_OBSERVED = "DIRECTLY_OBSERVED"      # a logged / measured fact
    JUSTIFIED_INFERENCE = "JUSTIFIED_INFERENCE"  # sound derivation, but NOT a logged measurement
    UNAVAILABLE = "UNAVAILABLE"                   # no truth -> fail closed, never healthy-defaulted


class EvidenceCompleteness(str, enum.Enum):
    COMPLETE = "COMPLETE"       # every authority-bearing field present + observed/inferred
    PARTIAL = "PARTIAL"         # advisory fields missing but authority fields present
    INCOMPLETE = "INCOMPLETE"   # an authority-bearing field is UNAVAILABLE -> shadow must fail closed


class SnapshotError(Exception):
    """Structurally-impossible snapshot (bad version, naive datetime, unknown field). Construction-boundary only."""


@dataclass(frozen=True)
class FieldProvenance:
    """Provenance for ONE evidence field (architecture_phase2_v1 §1). Read-only, immutable."""
    field: str
    source_module: str
    source_symbol: str
    owner: str
    representation: str
    clock: str                       # e.g. "UTC wall (adapter)", "n/a", "monotonic"
    freshness_basis: str             # what the age/horizon means + basis
    availability: AvailabilityClass
    authority: str                   # "AUTHORITATIVE" | "ADVISORY"
    observation: Observation
    read_only_safe: bool = True
    note: str = ""

    @property
    def surface_id(self) -> str:
        """Stable identity of the physical evidence SURFACE this field reads: the source symbol with any
        parenthetical qualifier stripped. Two fields sharing a surface_id read the SAME live surface (e.g. the
        heartbeat AND the shared-progress signal both read AdapterHealth.last_tick_at) and therefore MUST NOT be
        counted as independent corroboration (architecture_phase2_v1 §8). Used by the mapper's provenance guard."""
        base = self.source_symbol.split("(", 1)[0].strip()
        return f"{self.source_module}::{base}"

    def to_dict(self) -> dict:
        return {
            "field": self.field, "source_module": self.source_module, "source_symbol": self.source_symbol,
            "owner": self.owner, "representation": self.representation, "clock": self.clock,
            "freshness_basis": self.freshness_basis, "availability": self.availability.value,
            "authority": self.authority, "observation": self.observation.value,
            "read_only_safe": self.read_only_safe, "note": self.note,
        }


@dataclass(frozen=True)
class SnapshotInstrument:
    """Per-instrument evidence (maps 1:1 to core.InstrumentObservation)."""
    instrument: str
    expected_flow: bool
    stale: bool
    validated: bool
    governed_closed: bool = False
    reopening_grace: bool = False

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument, "expected_flow": self.expected_flow, "stale": self.stale,
            "validated": self.validated, "governed_closed": self.governed_closed,
            "reopening_grace": self.reopening_grace,
        }

    def to_core(self) -> core.InstrumentObservation:
        return core.InstrumentObservation(
            instrument=self.instrument, expected_flow=self.expected_flow, stale=self.stale,
            validated=self.validated, governed_closed=self.governed_closed, reopening_grace=self.reopening_grace,
        )


@dataclass(frozen=True)
class EvidenceSnapshot:
    """Immutable, versioned LIVE-evidence snapshot. Sufficient to reproduce a shadow decision OFFLINE.

    A future Phase-2 shadow adapter would build this atomically from an immutable copy of the transport / watchdog
    surfaces (never a live global), then hand it to the Phase-1 core via the mapper. This class NEVER touches those
    surfaces — it is a pure container. All timestamps are supplied by the caller (no wall-clock read here).
    """
    # ---- identity / provenance ----
    provider: str
    captured_at_utc: str
    evaluated_at_utc: str
    config_version: Optional[str]
    source_sha: str
    adapter_version: str
    runtime_identity: Mapping[str, object]        # sanitised host/pid/env — NEVER secrets/tokens/account-ids
    connection_generation: int                    # for callback bounding / dedup scoping
    # ---- transport evidence (the 21 core inputs, minus derived collections) ----
    socket_state: str
    provider_disconnect_event: bool
    auth_failure: bool
    heartbeat_available: bool
    heartbeat_age_s: Optional[float]
    shared_stream_silent: bool
    parser_fatal: bool
    reconnect_in_progress: bool
    shared_progress_available: Optional[bool]
    shared_progress_age_s: Optional[float]
    provider_maintenance_indication: bool
    error_count_delta: int
    heartbeat_soft_horizon_s: float
    heartbeat_hard_horizon_s: float
    instruments: Tuple[SnapshotInstrument, ...]
    limiter_attempts_in_window: int
    limiter_max_per_window: int
    limiter_window_seconds: int
    limiter_available: bool
    # ---- honesty / audit meta ----
    field_provenance: Tuple[FieldProvenance, ...]
    unavailable_fields: Tuple[Mapping[str, str], ...]        # [{field, reason}]
    contradictory_evidence: Tuple[Mapping[str, str], ...]    # [{fields, description}]
    evidence_completeness: EvidenceCompleteness
    snapshot_version: str = SNAPSHOT_VERSION
    contract_version: str = core.CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.snapshot_version not in SUPPORTED_SNAPSHOT_VERSIONS:
            raise SnapshotError(f"unsupported snapshot_version {self.snapshot_version!r}")
        if not isinstance(self.provider, str) or not self.provider:
            raise SnapshotError("provider must be a non-empty str")
        for name in ("captured_at_utc", "evaluated_at_utc"):
            v = getattr(self, name)
            if not isinstance(v, str) or not v:
                raise SnapshotError(f"{name} must be an ISO-8601 UTC string")
            # Enforce tz-aware UTC at the snapshot boundary (naive/non-UTC is rejected, never coerced).
            _require_iso_utc(v, name)
        try:
            core.SocketState(self.socket_state)
        except ValueError:
            raise SnapshotError(f"socket_state {self.socket_state!r} not a SocketState value")
        if not isinstance(self.connection_generation, int) or isinstance(self.connection_generation, bool) \
                or self.connection_generation < 0:
            raise SnapshotError("connection_generation must be a non-negative int")
        names = [i.instrument for i in self.instruments]
        if len(names) != len(set(names)):
            raise SnapshotError("duplicate instrument in snapshot")
        # canonical instrument ordering (order MUST NOT change the decision)
        canonical = tuple(sorted(self.instruments, key=lambda i: i.instrument))
        if canonical != self.instruments:
            object.__setattr__(self, "instruments", canonical)

    # ---- deterministic serialisation ----
    def canonical_dict(self) -> dict:
        return {
            "snapshot_version": self.snapshot_version,
            "contract_version": self.contract_version,
            "provider": self.provider,
            "captured_at_utc": self.captured_at_utc,
            "evaluated_at_utc": self.evaluated_at_utc,
            "config_version": self.config_version,
            "source_sha": self.source_sha,
            "adapter_version": self.adapter_version,
            "runtime_identity": dict(self.runtime_identity),
            "connection_generation": self.connection_generation,
            "socket_state": self.socket_state,
            "provider_disconnect_event": self.provider_disconnect_event,
            "auth_failure": self.auth_failure,
            "heartbeat_available": self.heartbeat_available,
            "heartbeat_age_s": self.heartbeat_age_s,
            "shared_stream_silent": self.shared_stream_silent,
            "parser_fatal": self.parser_fatal,
            "reconnect_in_progress": self.reconnect_in_progress,
            "shared_progress_available": self.shared_progress_available,
            "shared_progress_age_s": self.shared_progress_age_s,
            "provider_maintenance_indication": self.provider_maintenance_indication,
            "error_count_delta": self.error_count_delta,
            "heartbeat_soft_horizon_s": self.heartbeat_soft_horizon_s,
            "heartbeat_hard_horizon_s": self.heartbeat_hard_horizon_s,
            "instruments": [i.to_dict() for i in self.instruments],
            "limiter_attempts_in_window": self.limiter_attempts_in_window,
            "limiter_max_per_window": self.limiter_max_per_window,
            "limiter_window_seconds": self.limiter_window_seconds,
            "limiter_available": self.limiter_available,
            "field_provenance": [p.to_dict() for p in self.field_provenance],
            "unavailable_fields": [dict(u) for u in self.unavailable_fields],
            "contradictory_evidence": [dict(c) for c in self.contradictory_evidence],
            "evidence_completeness": self.evidence_completeness.value,
        }

    def canonical_json(self) -> str:
        return json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":"))

    @property
    def snapshot_id(self) -> str:
        """Deterministic sha256[:16] over the canonical snapshot. No randomness, no wall-clock."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = self.canonical_dict()
        d["snapshot_id"] = self.snapshot_id
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    # ---- provenance lookup (used by the mapper's double-count guard) ----
    def provenance_for(self, field_name: str) -> Optional[FieldProvenance]:
        for p in self.field_provenance:
            if p.field == field_name:
                return p
        return None


def _require_iso_utc(value: str, name: str) -> None:
    """Reject a naive or non-UTC ISO timestamp at the snapshot construction boundary."""
    try:
        dt = datetime.datetime.fromisoformat(value)
    except ValueError:
        raise SnapshotError(f"{name} must be an ISO-8601 datetime string")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise SnapshotError(f"{name} must be timezone-aware (UTC)")
    if dt.utcoffset() != datetime.timedelta(0):
        raise SnapshotError(f"{name} must be UTC (offset 0)")


def snapshot_from_dict(payload: Mapping[str, object]) -> EvidenceSnapshot:
    """Decode a persisted snapshot, failing SAFELY on an unsupported version. No unsafe deserialisation (plain
    dict access, bounded, no eval). Reused by the offline replay path + tests."""
    ver = payload.get("snapshot_version")
    if ver not in SUPPORTED_SNAPSHOT_VERSIONS:
        raise SnapshotError(f"unsupported snapshot_version {ver!r} (supported: {sorted(SUPPORTED_SNAPSHOT_VERSIONS)})")

    def _prov(p: Mapping[str, object]) -> FieldProvenance:
        return FieldProvenance(
            field=str(p["field"]), source_module=str(p["source_module"]), source_symbol=str(p["source_symbol"]),
            owner=str(p["owner"]), representation=str(p["representation"]), clock=str(p["clock"]),
            freshness_basis=str(p["freshness_basis"]), availability=AvailabilityClass(str(p["availability"])),
            authority=str(p["authority"]), observation=Observation(str(p["observation"])),
            read_only_safe=bool(p.get("read_only_safe", True)), note=str(p.get("note", "")),
        )

    def _inst(p: Mapping[str, object]) -> SnapshotInstrument:
        return SnapshotInstrument(
            instrument=str(p["instrument"]), expected_flow=bool(p["expected_flow"]), stale=bool(p["stale"]),
            validated=bool(p["validated"]), governed_closed=bool(p.get("governed_closed", False)),
            reopening_grace=bool(p.get("reopening_grace", False)),
        )

    return EvidenceSnapshot(
        provider=str(payload["provider"]),
        captured_at_utc=str(payload["captured_at_utc"]),
        evaluated_at_utc=str(payload["evaluated_at_utc"]),
        config_version=payload.get("config_version"),
        source_sha=str(payload["source_sha"]),
        adapter_version=str(payload["adapter_version"]),
        runtime_identity=dict(payload.get("runtime_identity") or {}),
        connection_generation=int(payload["connection_generation"]),
        socket_state=str(payload["socket_state"]),
        provider_disconnect_event=bool(payload["provider_disconnect_event"]),
        auth_failure=bool(payload["auth_failure"]),
        heartbeat_available=bool(payload["heartbeat_available"]),
        heartbeat_age_s=payload.get("heartbeat_age_s"),
        shared_stream_silent=bool(payload["shared_stream_silent"]),
        parser_fatal=bool(payload["parser_fatal"]),
        reconnect_in_progress=bool(payload["reconnect_in_progress"]),
        shared_progress_available=payload.get("shared_progress_available"),
        shared_progress_age_s=payload.get("shared_progress_age_s"),
        provider_maintenance_indication=bool(payload["provider_maintenance_indication"]),
        error_count_delta=int(payload["error_count_delta"]),
        heartbeat_soft_horizon_s=float(payload["heartbeat_soft_horizon_s"]),
        heartbeat_hard_horizon_s=float(payload["heartbeat_hard_horizon_s"]),
        instruments=tuple(_inst(p) for p in (payload.get("instruments") or ())),
        limiter_attempts_in_window=int(payload["limiter_attempts_in_window"]),
        limiter_max_per_window=int(payload["limiter_max_per_window"]),
        limiter_window_seconds=int(payload["limiter_window_seconds"]),
        limiter_available=bool(payload["limiter_available"]),
        field_provenance=tuple(_prov(p) for p in (payload.get("field_provenance") or ())),
        unavailable_fields=tuple(dict(u) for u in (payload.get("unavailable_fields") or ())),
        contradictory_evidence=tuple(dict(c) for c in (payload.get("contradictory_evidence") or ())),
        evidence_completeness=EvidenceCompleteness(str(payload["evidence_completeness"])),
        snapshot_version=str(payload["snapshot_version"]),
        contract_version=str(payload.get("contract_version", core.CONTRACT_VERSION)),
    )


def with_completeness(snap: EvidenceSnapshot, completeness: EvidenceCompleteness) -> EvidenceSnapshot:
    """Return a copy with a different completeness verdict (used by tests). Immutable-copy, never mutation."""
    return replace(snap, evidence_completeness=completeness)
