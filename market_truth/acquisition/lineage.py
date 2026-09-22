"""
market_truth.acquisition.lineage — source→canonical lineage catalogue (HMT-2B, made REAL by the
HMT-2 canonicalisation checkpoint).

HISTORY — this module's earlier (HMT-2B) form was explicitly scaffolding/interface code only: no
real acquired GC MBP-1 data and no real canonical partitions existed yet, so every test exercised
it with clearly-labelled synthetic placeholder records. That is no longer true: the HMT-2
canonicalisation checkpoint (see `market_truth.acquisition.canonical_worker`) builds real,
durable canonical partitions from real retained MBP-1 sessions, and THIS module is the durable
lineage record for each one. `LineageRow`/`LineageCatalogue` themselves are unchanged in shape
(every existing synthetic test still passes unmodified) — what's new is:

  1. A set of additional, OPTIONAL fields (all default `None`, appended after `synthetic` so no
     existing positional/keyword construction breaks) carrying the rest of the WO's required
     lineage chain: corpus manifest reference, canonical schema/canonicaliser/identity-algorithm
     versions, per-family canonical event counts, per-partition semantic AND physical hashes, the
     evidence manifest's own deterministic-fields hash, and a quality-record reference. A row with
     all of these `None` is exactly the old HMT-2B synthetic shape — nothing about the old rows'
     meaning changes.
  2. `assert_real_row_is_complete()` — a STRICTER check (never run automatically in
     `__post_init__`, so it never touches the old synthetic tests) that a REAL (`synthetic=False`)
     row must pass before its session may be marked `CANONICAL_COMPLETE` by the canonical ledger.
  3. Durable, per-session, atomically-written JSON storage — `lineage/<session-id>.json`, ONE
     file per session (never a single fragile monolithic catalogue file) — `write_lineage_record_
     atomic()` / `read_lineage_record()` / `lineage_record_relative_path()`.
  4. `build_lineage_index()` — a read/aggregation function that deterministically re-derives a
     corpus-level `LineageCatalogue` FROM the per-session files on disk. This index is NEVER
     itself the source of truth — it is always regenerable from the per-session records, exactly
     as the WO requires.

Deterministic chain (WO Part 2, verbatim order):

    corpus session -> provider request -> native artefact -> provider definition
        -> actual GC contract (market_truth.futures.GcContractIdentity — reused, not
           reinvented) -> canonical events -> research partitions -> evidence manifest
        -> quality record

Invariants enforced structurally (not just by convention):
    - every canonical partition is traceable back to retained native bytes (no orphan
      partitions — a row may not carry research-partition paths without a canonical event set
      hash on the SAME row);
    - no source bytes are recorded without acquisition/evidence metadata (enforced by
      `market_truth.acquisition.source_store.NativeArtefactRecord.__post_init__`, and re-checked
      here at the lineage-row level too, since a lineage row is the thing that actually links
      session -> artefact -> everything downstream);
    - (NEW) no session may be considered `CANONICAL_COMPLETE` without a REAL lineage row that
      itself carries a canonical event-set hash, a canonicaliser/schema/identity-algorithm
      version, at least one research partition with BOTH a semantic and a physical hash, an
      evidence-manifest reference with its own deterministic hash, and a quality-record
      reference — `assert_real_row_is_complete()` is the one place this is checked.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

from market_truth.futures import GcContractIdentity
from market_truth.identity import encode_field

LINEAGE_CATALOGUE_VERSION = "hmt2b-source-canonical-lineage-v1"
LINEAGE_RECORD_FILE_VERSION = "hmt2-lineage-record-file-v1"


class LineageError(ValueError):
    """Fails closed on any structurally invalid or orphaned lineage row."""


def _safe_session_segment(session_id: str) -> str:
    """Filesystem-escape one session id for use as a lineage record file stem — mirrors
    `source_store._safe_segment`'s philosophy (deterministic, human-legible, never silently
    truncates). Duplicated here (not imported) to keep this module's own dependency surface
    limited to `futures`/`identity`, exactly as it already was before this checkpoint."""
    if not session_id:
        raise LineageError("session_id must be non-empty")
    return "".join(c if (c.isalnum() or c in "-_.:") else "_" for c in session_id)


@dataclass(frozen=True)
class LineageRow:
    """One deterministic link in the source->canonical chain for one GC session.

    `synthetic=True` on every row the original HMT-2B checkpoint could produce (no real acquired
    data existed yet). The HMT-2 canonicalisation checkpoint is the first, and only, code
    permitted to construct a `synthetic=False` row, and only from a real, verified canonical
    pipeline run (`market_truth.acquisition.canonical_worker`).

    The block of fields below `synthetic` is entirely OPTIONAL (default `None`) and additive —
    every field that existed before this checkpoint is unchanged, in the same order, with the
    same required/optional-ness it always had.
    """

    corpus_session_id: str
    provider_request_identity: str
    native_artefact_relative_path: str
    native_artefact_sha256: str
    provider_definition_ref: str
    gc_contract: GcContractIdentity
    canonical_event_set_hash: Optional[str]
    research_partition_relative_paths: Tuple[str, ...]
    evidence_manifest_ref: Optional[str]
    synthetic: bool = True

    # ---- HMT-2 canonicalisation checkpoint additions (all optional, all additive) ----
    corpus_manifest_ref: Optional[str] = None
    canonical_schema_version: Optional[str] = None
    canonicaliser_version: Optional[str] = None
    event_identity_algorithm_version: Optional[str] = None
    canonical_event_counts_by_family: Optional[Mapping[str, int]] = None
    partition_semantic_hashes: Optional[Mapping[str, str]] = None  # relative_path -> partition_content_sha256
    partition_artifact_hashes: Optional[Mapping[str, str]] = None  # relative_path -> artifact_sha256 (physical)
    evidence_manifest_deterministic_hash: Optional[str] = None
    quality_record_ref: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.corpus_session_id:
            raise LineageError("corpus_session_id must be non-empty")
        if not self.provider_request_identity:
            raise LineageError(f"session {self.corpus_session_id}: provider_request_identity must be non-empty")
        if not self.native_artefact_relative_path or not self.native_artefact_sha256:
            raise LineageError(
                f"session {self.corpus_session_id}: source bytes recorded without acquisition/"
                f"evidence metadata (native_artefact_relative_path/sha256 both required)"
            )
        if not isinstance(self.gc_contract, GcContractIdentity):
            raise LineageError(f"session {self.corpus_session_id}: gc_contract must be a GcContractIdentity")
        if self.research_partition_relative_paths and self.canonical_event_set_hash is None:
            raise LineageError(
                f"session {self.corpus_session_id}: research partitions recorded with no "
                f"canonical_event_set_hash — orphan partition, refused"
            )
        if self.partition_semantic_hashes is not None:
            if set(self.partition_semantic_hashes) != set(self.research_partition_relative_paths):
                raise LineageError(
                    f"session {self.corpus_session_id}: partition_semantic_hashes keys must "
                    f"exactly match research_partition_relative_paths"
                )
        if self.partition_artifact_hashes is not None:
            if set(self.partition_artifact_hashes) != set(self.research_partition_relative_paths):
                raise LineageError(
                    f"session {self.corpus_session_id}: partition_artifact_hashes keys must "
                    f"exactly match research_partition_relative_paths"
                )

    def row_identity_sha256(self) -> str:
        h = hashlib.sha256()
        for value in (
            self.corpus_session_id,
            self.provider_request_identity,
            self.native_artefact_relative_path,
            self.native_artefact_sha256,
            self.provider_definition_ref,
            self.gc_contract.canonical_id(),
            self.canonical_event_set_hash,
            self.evidence_manifest_ref,
            str(self.synthetic),
            self.corpus_manifest_ref,
            self.canonical_schema_version,
            self.canonicaliser_version,
            self.event_identity_algorithm_version,
            self.evidence_manifest_deterministic_hash,
            self.quality_record_ref,
        ):
            h.update(encode_field(value))
        for p in self.research_partition_relative_paths:
            h.update(encode_field(p))
        for mapping in (
            self.canonical_event_counts_by_family,
            self.partition_semantic_hashes,
            self.partition_artifact_hashes,
        ):
            if mapping:
                for k in sorted(mapping):
                    h.update(encode_field(str(k)))
                    h.update(encode_field(str(mapping[k])))
        return h.hexdigest()

    def to_json_dict(self) -> dict:
        """Deterministic, sorted-key JSON representation — the one canonical serialization used
        by `write_lineage_record_atomic()`."""
        contract = self.gc_contract
        return {
            "lineage_record_file_version": LINEAGE_RECORD_FILE_VERSION,
            "corpus_session_id": self.corpus_session_id,
            "provider_request_identity": self.provider_request_identity,
            "native_artefact_relative_path": self.native_artefact_relative_path,
            "native_artefact_sha256": self.native_artefact_sha256,
            "provider_definition_ref": self.provider_definition_ref,
            "gc_contract": {
                "venue": contract.venue,
                "product_root": contract.product_root,
                "delivery_year": contract.delivery_year,
                "delivery_month": contract.delivery_month,
                "canonical_id": contract.canonical_id(),
            },
            "canonical_event_set_hash": self.canonical_event_set_hash,
            "research_partition_relative_paths": list(self.research_partition_relative_paths),
            "evidence_manifest_ref": self.evidence_manifest_ref,
            "synthetic": self.synthetic,
            "corpus_manifest_ref": self.corpus_manifest_ref,
            "canonical_schema_version": self.canonical_schema_version,
            "canonicaliser_version": self.canonicaliser_version,
            "event_identity_algorithm_version": self.event_identity_algorithm_version,
            "canonical_event_counts_by_family": (
                dict(sorted(self.canonical_event_counts_by_family.items()))
                if self.canonical_event_counts_by_family else None
            ),
            "partition_semantic_hashes": (
                dict(sorted(self.partition_semantic_hashes.items())) if self.partition_semantic_hashes else None
            ),
            "partition_artifact_hashes": (
                dict(sorted(self.partition_artifact_hashes.items())) if self.partition_artifact_hashes else None
            ),
            "evidence_manifest_deterministic_hash": self.evidence_manifest_deterministic_hash,
            "quality_record_ref": self.quality_record_ref,
            "row_identity_sha256": self.row_identity_sha256(),
        }

    @classmethod
    def from_json_dict(cls, doc: Mapping) -> "LineageRow":
        contract_doc = doc["gc_contract"]
        contract = GcContractIdentity(
            delivery_year=contract_doc["delivery_year"],
            delivery_month=contract_doc["delivery_month"],
            venue=contract_doc.get("venue", "COMEX"),
            product_root=contract_doc.get("product_root", "GC"),
        )
        return cls(
            corpus_session_id=doc["corpus_session_id"],
            provider_request_identity=doc["provider_request_identity"],
            native_artefact_relative_path=doc["native_artefact_relative_path"],
            native_artefact_sha256=doc["native_artefact_sha256"],
            provider_definition_ref=doc["provider_definition_ref"],
            gc_contract=contract,
            canonical_event_set_hash=doc.get("canonical_event_set_hash"),
            research_partition_relative_paths=tuple(doc.get("research_partition_relative_paths") or ()),
            evidence_manifest_ref=doc.get("evidence_manifest_ref"),
            synthetic=doc.get("synthetic", True),
            corpus_manifest_ref=doc.get("corpus_manifest_ref"),
            canonical_schema_version=doc.get("canonical_schema_version"),
            canonicaliser_version=doc.get("canonicaliser_version"),
            event_identity_algorithm_version=doc.get("event_identity_algorithm_version"),
            canonical_event_counts_by_family=doc.get("canonical_event_counts_by_family"),
            partition_semantic_hashes=doc.get("partition_semantic_hashes"),
            partition_artifact_hashes=doc.get("partition_artifact_hashes"),
            evidence_manifest_deterministic_hash=doc.get("evidence_manifest_deterministic_hash"),
            quality_record_ref=doc.get("quality_record_ref"),
        )


def assert_real_row_is_complete(row: LineageRow) -> None:
    """The stricter completeness gate a REAL (`synthetic=False`) row must pass before its
    session may be marked `CANONICAL_COMPLETE` (WO Part 3, binding invariant: "no session may be
    marked CANONICAL_COMPLETE without lineage evidence existing and verified"). Never invoked
    automatically by `__post_init__` — that constructor-time check stays exactly as permissive as
    it always was, so every pre-existing synthetic test is untouched. This function is the one,
    explicit, separately-callable gate `canonical_worker.py` runs before promoting a session."""
    if row.synthetic:
        raise LineageError(f"session {row.corpus_session_id!r}: assert_real_row_is_complete() called on a synthetic row")
    required_scalars = {
        "canonical_event_set_hash": row.canonical_event_set_hash,
        "canonical_schema_version": row.canonical_schema_version,
        "canonicaliser_version": row.canonicaliser_version,
        "event_identity_algorithm_version": row.event_identity_algorithm_version,
        "evidence_manifest_ref": row.evidence_manifest_ref,
        "evidence_manifest_deterministic_hash": row.evidence_manifest_deterministic_hash,
        "quality_record_ref": row.quality_record_ref,
    }
    missing = sorted(name for name, value in required_scalars.items() if not value)
    if missing:
        raise LineageError(f"session {row.corpus_session_id!r}: real lineage row missing required field(s): {missing}")
    if not row.research_partition_relative_paths:
        raise LineageError(f"session {row.corpus_session_id!r}: real lineage row has no research partitions")
    if not row.partition_semantic_hashes or not row.partition_artifact_hashes:
        raise LineageError(f"session {row.corpus_session_id!r}: real lineage row missing partition hash map(s)")


def lineage_record_relative_path(session_id: str) -> str:
    """`lineage/<session-id>.json` — relative to the durable canonical research-store root
    (`market_truth.acquisition.canonical_worker.resolve_canonical_research_root()`'s corpus
    subdirectory)."""
    return f"lineage/{_safe_session_segment(session_id)}.json"


def write_lineage_record_atomic(root_dir, row: LineageRow) -> Path:
    """Write ONE per-session lineage record — atomic (temp path, then `os.replace`), so a crash
    mid-write can never leave a partially-written or corrupt lineage file in place (WO Part 3/4:
    "atomically written ... never write in place")."""
    path = Path(root_dir) / lineage_record_relative_path(row.corpus_session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    doc = row.to_json_dict()
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp_path, path)  # atomic rename on POSIX
    return path


def read_lineage_record(root_dir, session_id: str) -> LineageRow:
    """Read back ONE per-session lineage record, re-verifying its own self-check hash
    (`row_identity_sha256`, recorded at write time) — a file that fails this self-check is
    treated as corrupted/tampered, never silently trusted."""
    path = Path(root_dir) / lineage_record_relative_path(session_id)
    if not path.exists():
        raise LineageError(f"no lineage record file for session {session_id!r} at {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    row = LineageRow.from_json_dict(doc)
    recorded_self_hash = doc.get("row_identity_sha256")
    if row.row_identity_sha256() != recorded_self_hash:
        raise LineageError(
            f"session {session_id!r}: lineage record at {path} failed its own self-check "
            f"(row_identity_sha256 mismatch) — file may be corrupted; never silently trusted"
        )
    return row


def lineage_record_exists(root_dir, session_id: str) -> bool:
    return (Path(root_dir) / lineage_record_relative_path(session_id)).exists()


class LineageCatalogue:
    """An append-only, deterministic collection of `LineageRow`s, keyed by session id.

    Re-enforces WO Part 2's two invariants at the catalogue level (each row already enforces
    the orphan-partition and acquisition-metadata invariants individually in
    `LineageRow.__post_init__`; `assert_no_orphan_partitions()` below re-verifies across the
    whole catalogue as constructed, e.g. after a bulk load from disk).
    """

    def __init__(self) -> None:
        self._rows: Dict[str, LineageRow] = {}

    def add_row(self, row: LineageRow) -> None:
        if row.corpus_session_id in self._rows:
            raise LineageError(
                f"session {row.corpus_session_id!r} already has a lineage row — append-only, "
                f"never overwritten in place"
            )
        self._rows[row.corpus_session_id] = row

    def get_row(self, session_id: str) -> LineageRow:
        try:
            return self._rows[session_id]
        except KeyError as exc:
            raise LineageError(f"no lineage row for session {session_id!r}") from exc

    def all_rows(self) -> Tuple[LineageRow, ...]:
        return tuple(self._rows[k] for k in sorted(self._rows))

    def catalogue_content_sha256(self) -> str:
        """Deterministic hash over every row, sorted by session id, so catalogue identity never
        depends on insertion order."""
        h = hashlib.sha256()
        h.update(encode_field(LINEAGE_CATALOGUE_VERSION))
        for row in self.all_rows():
            h.update(encode_field(row.row_identity_sha256()))
        return h.hexdigest()

    def assert_no_orphan_partitions(self) -> None:
        for row in self.all_rows():
            if row.research_partition_relative_paths and row.canonical_event_set_hash is None:
                raise LineageError(f"session {row.corpus_session_id!r}: orphan research partition detected")


def build_lineage_index(root_dir) -> dict:
    """Deterministically re-derive a corpus-level lineage index FROM the per-session
    `lineage/<session-id>.json` files on disk under `root_dir` — a pure read/aggregation
    function. This index is NEVER itself the source of truth (WO Part 3): re-running it against
    the same on-disk records always reproduces the same `catalogue_content_sha256`, and deleting
    it and re-running it loses nothing, because every fact it reports is re-read straight from
    the per-session files."""
    lineage_dir = Path(root_dir) / "lineage"
    catalogue = LineageCatalogue()
    session_ids: list = []
    if lineage_dir.exists():
        for path in sorted(lineage_dir.glob("*.json")):
            if path.name.endswith(".tmp"):
                continue
            session_id = path.stem
            row = read_lineage_record(root_dir, session_id)
            if row.corpus_session_id != session_id:
                raise LineageError(
                    f"lineage record file {path} is keyed by filename {session_id!r} but its own "
                    f"content declares corpus_session_id={row.corpus_session_id!r} — refusing to "
                    f"trust a mismatched file"
                )
            catalogue.add_row(row)
            session_ids.append(session_id)
    catalogue.assert_no_orphan_partitions()
    return {
        "lineage_index_version": LINEAGE_RECORD_FILE_VERSION,
        "session_count": len(session_ids),
        "session_ids": sorted(session_ids),
        "catalogue_content_sha256": catalogue.catalogue_content_sha256(),
    }
