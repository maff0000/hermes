"""HMT-2 (real-money checkpoint) — selection manifest v2 schema, canonical serialization,
hashing, writer/reader. Additive: does NOT modify `corpus_manifest.py`'s v1 schema/hashing
(`ManifestRow`, `ManifestMetadata`, `write_manifest`, etc. are all untouched and continue to
reproduce v1's frozen manifest byte-for-byte). Reuses `corpus_manifest.canonical_json()`
unchanged for the actual canonical-serialization/hashing primitive — the hashing DISCIPLINE is
identical to v1's; only the row/metadata SHAPE is new (extra fields the v1 schema never needed:
`session_log_range`, `reference_quality`, `within_year_rank`/`within_year_percentile`,
`continuous_underlying_instrument_id`, `event_snapshot_version`, `reference_snapshot_version`,
`secondary_context`).

Pure stdlib. No network. No provider dependency.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Optional

from market_truth.acquisition.corpus_manifest import canonical_json

MANIFEST_V2_SCHEMA_VERSION = "hmt2-corpus-selection-manifest-v2"


@dataclass(frozen=True)
class ManifestRowV2:
    """One row of the v2 selection manifest — one selected GC session. Superset of v1's
    `ManifestRow` fields (same names, same meaning, for every field v1 also had) plus the new
    fields this checkpoint's real reference-series/definitions processing made possible."""

    corpus_version: str
    session_id: str
    gc_trade_date: str
    request_start_utc: str
    request_end_utc: str
    primary_stratum: str
    protected_holdout: bool
    selection_algorithm_version: str
    calendar_version: str
    inclusion_reason: str
    event_class: Optional[str] = None
    event_source_ref: Optional[str] = None
    matched_parent_session: Optional[str] = None
    random_seed_domain: Optional[str] = None
    random_seed_version: Optional[str] = None
    exclusion_or_replacement_lineage: Optional[str] = None

    # New in v2:
    session_log_range: Optional[float] = None
    reference_quality: Optional[str] = None
    within_year_rank: Optional[int] = None
    within_year_eligible_count: Optional[int] = None
    within_year_percentile: Optional[float] = None
    continuous_underlying_instrument_ids: Optional[tuple] = None
    event_snapshot_version: Optional[str] = None
    reference_snapshot_version: Optional[str] = None

    # HMT-2 CORRECTION (additive): descriptive-only governance metadata — e.g. a
    # PROTECTED_HOLDOUT session that happens to coincide with a scheduled macro-event date. This
    # NEVER creates a duplicate session entry and NEVER implies trading-signal meaning; a
    # session's `primary_stratum` remains the sole determinant of its stratum membership.
    secondary_context: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if d.get("continuous_underlying_instrument_ids") is not None:
            d["continuous_underlying_instrument_ids"] = list(d["continuous_underlying_instrument_ids"])
        return d


@dataclass(frozen=True)
class ManifestMetadataV2:
    corpus_version: str
    manifest_schema_version: str
    selection_algorithm_version: str
    calendar_version: str
    session_universe_start: str
    session_universe_end: str
    seed_development_random_hex: str
    seed_protected_holdout_hex: str
    stratum_precedence_order: tuple
    superseded_manifest_relative_path: str
    superseded_manifest_sha256: str
    event_snapshot_version: str
    reference_snapshot_version: str
    generated_by: str
    base_sha: str
    notes: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["stratum_precedence_order"] = list(self.stratum_precedence_order)
        return d


def manifest_hash_v2(metadata: ManifestMetadataV2, rows: list[ManifestRowV2]) -> str:
    payload = {"metadata": metadata.to_dict(), "rows": [row.to_dict() for row in rows]}
    canonical = canonical_json(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_manifest_document_v2(metadata: ManifestMetadataV2, rows: list[ManifestRowV2]) -> dict:
    return {
        "metadata": metadata.to_dict(),
        "manifest_sha256": manifest_hash_v2(metadata, rows),
        "rows": [row.to_dict() for row in rows],
    }


def write_manifest_v2(path: str, metadata: ManifestMetadataV2, rows: list[ManifestRowV2]) -> str:
    document = build_manifest_document_v2(metadata, rows)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2, sort_keys=True)
        f.write("\n")
    return document["manifest_sha256"]


def read_manifest_v2(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class ManifestIntegrityErrorV2(Exception):
    pass


def verify_manifest_integrity_v2(document: dict) -> None:
    payload = {"metadata": document["metadata"], "rows": document["rows"]}
    recomputed = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    recorded = document["manifest_sha256"]
    if recomputed != recorded:
        raise ManifestIntegrityErrorV2(f"manifest hash mismatch: recorded={recorded} recomputed={recomputed}")
