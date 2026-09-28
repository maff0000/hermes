"""
market_truth.acquisition.source_store — native source-store artefact model (HMT-2B).

Structure/interface code ONLY in this checkpoint (WO Part 2): there is no real acquired GC MBP-1
data yet — Part 1's `DatabentoHistoricalProvider` deliberately does not implement any bulk
download. This module defines the immutable-evidence record shape and the on-disk layout a
future, separately-authorised acquisition step will populate, and enforces — structurally, not
just by convention — that a hashed native artefact can never be rewritten in place.

Every test exercising this module (`tests/hmt2/test_source_store.py`) uses clearly-labelled
synthetic placeholder records (mirroring HMT-1's `providers/fixture.py` `__synthetic__`
labelling discipline). None of it represents real GC data.

Layout (research-only, local/disposable filesystem — mirrors `partition.py`'s local/disposable-
storage discipline; see docs/contracts/hmt1-research-partition-contract-v1.md):

    research-source/hmt2-gc-mbp1-v1/
      manifest/
      sessions/<session-id>/
        request/
        definitions/
        source/
        evidence/

This is the WO brief's own preferred conceptual layout (Part 2); no clearer existing repo
convention for *research-source* (as opposed to *research-partition*) storage was found during
this checkpoint, so it is used as given.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from market_truth.identity import encode_field

SOURCE_STORE_LAYOUT_VERSION = "hmt2b-native-source-store-v1"
CORPUS_STORE_ROOT_NAME = "hmt2-gc-mbp1-v1"

_ARTEFACT_CATEGORIES = ("request", "definitions", "source", "evidence")


class SourceStoreError(ValueError):
    """Fails closed on any structurally invalid native-artefact operation."""


class ImmutableArtefactError(SourceStoreError):
    """Raised when code attempts to rewrite an already-hashed native artefact in place. Native
    source artefacts are immutable evidence (WO Part 2) — this store never supports in-place
    rewriting, even with byte-identical content, so an accidental re-run is caught rather than
    silently re-confirmed."""


def _safe_segment(value: str) -> str:
    """Filesystem-escape one path segment — mirrors `partition._safe_segment`'s philosophy
    (deterministic, human-legible, never silently truncates)."""
    if not value:
        raise SourceStoreError("path segment must be non-empty")
    return "".join(c if (c.isalnum() or c in "-_.:") else "_" for c in value)


def artefact_relative_path(session_id: str, category: str, filename: str, *, corpus_store_root: str = CORPUS_STORE_ROOT_NAME) -> str:
    """Build the deterministic relative path for one native artefact under the layout above."""
    if category not in _ARTEFACT_CATEGORIES:
        raise SourceStoreError(f"unknown artefact category {category!r} — must be one of {_ARTEFACT_CATEGORIES}")
    return "/".join(
        [_safe_segment(corpus_store_root), "sessions", _safe_segment(session_id), category, _safe_segment(filename)]
    )


def manifest_relative_path(filename: str, *, corpus_store_root: str = CORPUS_STORE_ROOT_NAME) -> str:
    return "/".join([_safe_segment(corpus_store_root), "manifest", _safe_segment(filename)])


def compute_request_identity(
    *, dataset: str, schema: str, symbols: "Tuple[str, ...] | list[str]", stype_in: str, start: str, end: str
) -> str:
    """HMT-2 real-money checkpoint — deterministic request-identity hash for the "already have
    this exact retained artefact? never re-request it" duplicate-billing guard (WO Part 3).
    Pure, order-independent in `symbols` (sorted before hashing, so a caller passing the same
    symbol set in a different order still resolves to the same identity) — every other field
    is hashed as given (dataset/schema/stype_in/start/end are each single scalar strings, so
    there is no ordering ambiguity to normalise for them).

    This is an identity over the REQUEST SHAPE only (what would be asked of the vendor), never
    over any acquired bytes — `NativeArtefactRecord.sha256` remains the only hash of the actual
    retained content. Two requests with the same identity are, by construction, asking for the
    exact same real data; finding one already completed (see
    `find_completed_request_identities()`) means the second must reuse the first's retained
    bytes rather than re-request and double-bill for them.
    """
    h = hashlib.sha256()
    for value in (dataset, schema, stype_in, start, end):
        h.update(encode_field(value))
    for symbol in sorted(symbols):
        h.update(encode_field(symbol))
    return h.hexdigest()


@dataclass(frozen=True)
class NativeArtefactRecord:
    """One immutable native-source artefact record — field list exactly per WO Part 2.

    `synthetic=True` on every record this checkpoint can produce (no real acquired data exists
    yet); a future, separately-authorised acquisition step is the only thing that may ever set
    it False for a real artefact.
    """

    corpus_version: str
    session_id: str
    provider_id: str
    dataset_id: str
    schema: str
    request_identity: str
    requested_start_utc: str
    requested_end_utc: str
    provider_raw_symbols: Tuple[str, ...]
    canonical_contract_mapping_ref: str
    object_relative_path: str
    byte_size: int
    sha256: str
    source_condition: str
    acquisition_utc: str
    synthetic: bool = True

    def __post_init__(self) -> None:
        if not self.session_id:
            raise SourceStoreError("session_id must be non-empty")
        if not self.object_relative_path or not self.sha256:
            raise SourceStoreError(
                f"session {self.session_id}: a native artefact record requires both "
                f"object_relative_path and sha256 — no source bytes without acquisition/"
                f"evidence metadata (WO Part 2 invariant)"
            )
        if self.byte_size < 0:
            raise SourceStoreError(f"session {self.session_id}: byte_size must be >= 0")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["provider_raw_symbols"] = list(self.provider_raw_symbols)
        return d

    def content_sha256(self) -> str:
        """Deterministic hash over the record's OWN fields (not the artefact bytes — that is
        `self.sha256`, already recorded). Used as this record's lineage-catalogue row identity
        input."""
        h = hashlib.sha256()
        for value in (
            self.corpus_version,
            self.session_id,
            self.provider_id,
            self.dataset_id,
            self.schema,
            self.request_identity,
            self.requested_start_utc,
            self.requested_end_utc,
            self.canonical_contract_mapping_ref,
            self.object_relative_path,
            str(self.byte_size),
            self.sha256,
            self.source_condition,
            self.acquisition_utc,
            str(self.synthetic),
        ):
            h.update(encode_field(value))
        for symbol in self.provider_raw_symbols:
            h.update(encode_field(symbol))
        return h.hexdigest()


class NativeSourceStore:
    """Filesystem-backed native-artefact writer/reader, rooted at `root_dir`.

    Enforces write-once-then-immutable per artefact relative path — see `ImmutableArtefactError`.
    Local/disposable test filesystem storage only (mirrors `partition.py`'s own storage
    discipline) — no production object-store deployment in this checkpoint.
    """

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        self._written_hashes: Dict[str, str] = {}

    def _full_path(self, relative_path: str) -> Path:
        return self.root_dir / relative_path

    def write_artefact(self, relative_path: str, data: bytes) -> str:
        """Write `data` at `relative_path` under `root_dir` exactly once. A second write to the
        same relative_path — even with byte-identical content — fails closed."""
        full_path = self._full_path(relative_path)
        if relative_path in self._written_hashes or full_path.exists():
            raise ImmutableArtefactError(
                f"native artefact at {relative_path!r} already exists — immutable evidence, "
                f"never rewritten in place"
            )
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        self._written_hashes[relative_path] = digest
        return digest

    def read_artefact(self, relative_path: str) -> bytes:
        full_path = self._full_path(relative_path)
        if not full_path.exists():
            raise SourceStoreError(f"no native artefact at {relative_path!r}")
        return full_path.read_bytes()

    def verify_artefact_integrity(self, relative_path: str, expected_sha256: str) -> None:
        """Read the artefact back and confirm its bytes still hash to `expected_sha256`. Fails
        closed on any mismatch — never silently accepts drifted/corrupted evidence."""
        data = self.read_artefact(relative_path)
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_sha256:
            raise SourceStoreError(
                f"native artefact at {relative_path!r} hash mismatch: "
                f"expected={expected_sha256} actual={actual}"
            )

    def artefact_exists(self, relative_path: str) -> bool:
        return relative_path in self._written_hashes or self._full_path(relative_path).exists()


@dataclass(frozen=True)
class NativeMbp1Record:
    """HMT-2 real-money checkpoint (MBP-1 pilot, Part 4) — one genuine, decoded MBP-1 record
    from an already-retained native `.dbn.zst` artefact, translated to this module's own plain,
    vendor-independent shape by `providers.databento_historical.iter_retained_mbp1_records()`
    (the ONE place a vendor `databento.MBP1Msg`/`BidAskPair` object is ever touched — it never
    escapes that function). Every field here is a plain `str`/`int`; nothing here is a vendor
    type, so `market_truth.providers.databento_mbp1` (the HMT-1-canonicalisation-side adapter
    that consumes this) never needs to import `databento` at all.

    `raw_symbol` is `None` when this request's own embedded symbology mapping (`DBNStore.
    mappings`) could not uniquely resolve `instrument_id` — the CALLER (the HMT-1-side adapter)
    must fail closed on this, never guess (WO Part 4). `record_index` is this record's 0-based
    position in the file's own native record order — used only for a deterministic
    `source_artifact_id` tiebreak, never identity-bearing on its own and never a "when it was
    processed" wall-clock-shaped field.
    """

    session_id: str
    raw_symbol: Optional[str]
    instrument_id: int
    ts_event_ns: int
    ts_recv_ns: int
    action: str  # "TRADE" | "ADD" | "CANCEL" | "MODIFY" | "CLEAR" | "FILL" | "NONE"
    side: str  # "BID" | "ASK" | "NONE"
    flags: int
    sequence: int
    price: int
    size: int
    bid_px_00: int
    ask_px_00: int
    bid_sz_00: int
    ask_sz_00: int
    bid_ct_00: int
    ask_ct_00: int
    record_index: int
