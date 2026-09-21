"""
market_truth.acquisition.lineage — source→canonical lineage catalogue (HMT-2B).

Scaffolding/interface code ONLY in this checkpoint (WO Part 2): there is no real acquired GC
MBP-1 data and no real canonical partitions built from it yet, so every test exercising this
module (`tests/hmt2/test_lineage.py`) uses clearly-labelled synthetic placeholder records
(mirroring HMT-1's `providers/fixture.py` `__synthetic__` labelling discipline). None of it
represents, or is claimed to represent, real GC data.

Deterministic chain (WO Part 2, verbatim order):

    corpus session -> provider request -> native artefact -> provider definition
        -> actual GC contract (market_truth.futures.GcContractIdentity — reused, not
           reinvented) -> canonical events -> research partitions -> evidence manifest

Invariants enforced structurally (not just by convention):
    - every canonical partition is traceable back to retained native bytes (no orphan
      partitions — a row may not carry research-partition paths without a canonical event set
      hash on the SAME row);
    - no source bytes are recorded without acquisition/evidence metadata (enforced by
      `market_truth.acquisition.source_store.NativeArtefactRecord.__post_init__`, and re-checked
      here at the lineage-row level too, since a lineage row is the thing that actually links
      session -> artefact -> everything downstream).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from market_truth.futures import GcContractIdentity
from market_truth.identity import encode_field

LINEAGE_CATALOGUE_VERSION = "hmt2b-source-canonical-lineage-v1"


class LineageError(ValueError):
    """Fails closed on any structurally invalid or orphaned lineage row."""


@dataclass(frozen=True)
class LineageRow:
    """One deterministic link in the source->canonical chain for one GC session.

    `synthetic=True` on every row this checkpoint can produce (no real acquired data exists
    yet); a future, separately-authorised acquisition step is the only thing that may ever set
    it False for a real row.
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
        ):
            h.update(encode_field(value))
        for p in self.research_partition_relative_paths:
            h.update(encode_field(p))
        return h.hexdigest()


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
