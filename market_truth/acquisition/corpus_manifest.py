"""HMT-2A — Selection manifest schema, canonical serialization, hashing, writer/reader.

The manifest is the frozen, committed, machine-readable artifact this checkpoint produces
(WO brief §7/§19). It records one row per selected GC session plus a metadata block. The
manifest's own hash is computed the same conceptual way HMT-1 hashes its canonical rows/
partitions (deterministic canonical JSON -> SHA-256) — this module does NOT import HMT-1's
canonicaliser/partition code, since this manifest predates any canonicalisation and HMT-1's
canonical machinery is out of scope to touch or reuse by import in this checkpoint.

Pure stdlib. No network. No provider dependency.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from typing import Any, Optional

MANIFEST_SCHEMA_VERSION = "hmt2-corpus-selection-manifest-v1"


@dataclass(frozen=True)
class ManifestRow:
    """One row of the frozen selection manifest — one selected GC session.

    Fields mirror WO brief §19 exactly. Fields not applicable to a given row's stratum are
    left as None (e.g. ``event_class`` for a RANDOM_DEVELOPMENT row) rather than an empty
    string, so a reader can distinguish "not applicable" from "empty string was recorded".
    """

    corpus_version: str
    session_id: str
    gc_trade_date: str  # ISO date
    request_start_utc: str  # ISO datetime, UTC
    request_end_utc: str  # ISO datetime, UTC
    primary_stratum: str
    protected_holdout: bool
    selection_algorithm_version: str
    calendar_version: str
    inclusion_reason: str
    event_class: Optional[str] = None
    event_source_ref: Optional[str] = None
    matched_parent_session: Optional[str] = None
    volatility_compression_metric: Optional[Any] = None  # value, or "PENDING_REFERENCE_SERIES_DATA"
    random_seed_domain: Optional[str] = None
    random_seed_version: Optional[str] = None
    exclusion_or_replacement_lineage: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ManifestMetadata:
    corpus_version: str
    manifest_schema_version: str
    selection_algorithm_version: str
    calendar_version: str
    session_universe_start: str
    session_universe_end: str
    seed_development_random_hex: str
    seed_protected_holdout_hex: str
    stratum_precedence_order: tuple
    pending_strata: tuple  # strata this checkpoint could not genuinely populate
    generated_by: str
    base_sha: str
    notes: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["stratum_precedence_order"] = list(self.stratum_precedence_order)
        d["pending_strata"] = list(self.pending_strata)
        return d


def canonical_json(obj: Any) -> str:
    """Deterministic canonical JSON: sorted keys, fixed separators, no floating whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def manifest_hash(metadata: ManifestMetadata, rows: list[ManifestRow]) -> str:
    """SHA-256 hex digest of the manifest's canonical serialization (metadata + rows, in the
    exact row order given — callers must pass rows in the final, deterministic sort order they
    intend to persist, since row order is part of what is hashed)."""
    payload = {
        "metadata": metadata.to_dict(),
        "rows": [row.to_dict() for row in rows],
    }
    canonical = canonical_json(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_manifest_document(metadata: ManifestMetadata, rows: list[ManifestRow]) -> dict:
    """The full manifest document, including its own hash, ready to serialize to disk."""
    return {
        "metadata": metadata.to_dict(),
        "manifest_sha256": manifest_hash(metadata, rows),
        "rows": [row.to_dict() for row in rows],
    }


def write_manifest(path: str, metadata: ManifestMetadata, rows: list[ManifestRow]) -> str:
    """Write the manifest document to ``path`` as pretty-printed JSON; return its hash."""
    document = build_manifest_document(metadata, rows)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2, sort_keys=True)
        f.write("\n")
    return document["manifest_sha256"]


def read_manifest(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class ManifestIntegrityError(Exception):
    """Raised when a read manifest's recorded hash does not match its recomputed hash."""


def verify_manifest_integrity(document: dict) -> None:
    """Recompute the hash over a loaded manifest document's metadata+rows and compare it to
    the ``manifest_sha256`` field recorded in the document. Fails closed."""
    metadata_dict = document["metadata"]
    rows_dicts = document["rows"]
    payload = {"metadata": metadata_dict, "rows": rows_dicts}
    recomputed = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    recorded = document["manifest_sha256"]
    if recomputed != recorded:
        raise ManifestIntegrityError(
            f"manifest hash mismatch: recorded={recorded} recomputed={recomputed}"
        )
