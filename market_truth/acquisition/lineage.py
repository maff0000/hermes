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
  5. VALID-EMPTY ARCHITECTURE RULING (additive, v2 file format) — `gc_contract` is now `Optional`
     (widened, never narrowed) and four new optional fields are appended after `quality_record_
     ref`: `canonical_result_kind` ("NONEMPTY"/"EMPTY_VALID" — NOT a new ledger lifecycle state,
     just result-kind metadata; both kinds are `CANONICAL_COMPLETE`), `empty_reason`,
     `source_resolved_contract_ids`, `canonical_emitted_contract_ids`. A session that fully,
     honestly processes to zero canonical events is a valid, `EMPTY_VALID` result — its row's
     `gc_contract` is `None` (no representative contract may ever be derived from zero emitted
     events) and it carries zero research partitions; `assert_real_row_is_complete()` enforces
     the correct, opposite set of invariants for each kind (see its own docstring).
  6. HMT-2 GOVERNED CANONICAL STORAGE-LAYOUT-DEFECT REMEDIATION (additive, v2 file format) — a
     confirmed, quantified defect in `canonical_worker.py`'s ORIGINAL promotion path let two
     different sessions' partitions collide on one shared, unnamespaced physical path (see that
     module's `CANONICAL_STORAGE_LAYOUT_VERSION` docstring for the full mechanism). The fix
     namespaces every promoted partition's physical path by `session_id`; this module's own
     role in that fix is exactly one new, appended, OPTIONAL field —
     `canonical_storage_layout_version` — recording which storage-layout generation produced a
     given row's `research_partition_relative_paths`/hash maps. A row with this field `None` is
     a PRE-FIX (v1, unnamespaced) row — indistinguishable, for every pre-existing consumer, from
     the row shape this module always had, and it must NEVER be silently treated as
     v2-verified merely because its other required fields happen to be populated
     (`assert_lineage_row_is_storage_layout_v2()` is the one explicit, separately-callable check
     for that).

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

from market_truth.acquisition.canonical_quality_record import (
    RESULT_KIND_EMPTY_VALID,
    VALID_CANONICAL_RESULT_KINDS,
)
from market_truth.futures import GcContractIdentity
from market_truth.identity import encode_field

LINEAGE_CATALOGUE_VERSION = "hmt2b-source-canonical-lineage-v1"
LINEAGE_RECORD_FILE_VERSION = "hmt2-lineage-record-file-v2"

# The canonical STORAGE-LAYOUT version this checkpoint's namespace-collision remediation
# introduces (see `canonical_worker.py`'s module docstring for the full defect mechanism and
# fix). A `LineageRow.canonical_storage_layout_version` equal to this value means: every one of
# this row's `research_partition_relative_paths` was promoted under the session-scoped
# `canonical-v2/session_id=<...>/...` physical layout, so no other session_id can ever address
# the same physical file. `None` (or any other value) means the row predates this fix (the
# original, unnamespaced `canonical/...` layout) — see `assert_lineage_row_is_storage_layout_v2`.
CANONICAL_STORAGE_LAYOUT_VERSION = "hmt2-canonical-storage-layout-v2"


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
    gc_contract: Optional[GcContractIdentity]
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

    # ---- valid-empty architecture ruling additions (all optional, all additive) ----
    # `gc_contract` above is now Optional too (widened, never narrowed — every row that already
    # provided a real GcContractIdentity is completely unaffected): a real EMPTY_VALID row's
    # `gc_contract` is deliberately `None` (no representative contract may ever be derived from
    # zero emitted events); its real per-contract detail lives in
    # `source_resolved_contract_ids` instead (architecture ruling item 6, disclosed choice).
    canonical_result_kind: Optional[str] = None  # "NONEMPTY" | "EMPTY_VALID"
    empty_reason: Optional[str] = None  # only meaningful when canonical_result_kind == "EMPTY_VALID"
    source_resolved_contract_ids: Optional[Tuple[str, ...]] = None
    canonical_emitted_contract_ids: Optional[Tuple[str, ...]] = None

    # ---- HMT-2 governed canonical storage-layout-defect remediation addition (optional, additive) ----
    # `None` for every row written before this fix existed (the pre-fix, unnamespaced `canonical/`
    # layout) — see `CANONICAL_STORAGE_LAYOUT_VERSION` above and `canonical_worker.py`'s module
    # docstring for the full defect/fix. Set to `CANONICAL_STORAGE_LAYOUT_VERSION` on every row
    # `canonical_worker.py` constructs from this fix onward.
    canonical_storage_layout_version: Optional[str] = None

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
        if self.gc_contract is not None and not isinstance(self.gc_contract, GcContractIdentity):
            raise LineageError(f"session {self.corpus_session_id}: gc_contract must be a GcContractIdentity")
        if self.canonical_result_kind is not None and self.canonical_result_kind not in VALID_CANONICAL_RESULT_KINDS:
            raise LineageError(
                f"session {self.corpus_session_id}: invalid canonical_result_kind "
                f"{self.canonical_result_kind!r} (must be one of {sorted(VALID_CANONICAL_RESULT_KINDS)})"
            )
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
        """BACKWARD-COMPATIBILITY, load-bearing: every field that existed before the valid-empty
        architecture ruling is hashed in EXACTLY the same order, over EXACTLY the same values, as
        before that ruling -- a lineage record file written by the pre-ruling code must continue
        to self-verify (`read_lineage_record()`'s own `row_identity_sha256` recheck) forever,
        without being silently invalidated by a later code change. The four new fields
        (`canonical_result_kind`/`empty_reason`/`source_resolved_contract_ids`/`canonical_
        emitted_contract_ids`) are folded in ONLY when `canonical_result_kind` is set -- true for
        every row `canonical_worker.py` constructs from this checkpoint onward, false for every
        row written before it existed (which always leaves it `None`). This is what lets a
        pre-existing, already-`CANONICAL_COMPLETE` session's lineage file keep passing its own
        self-check after this upgrade, with zero reprocessing."""
        h = hashlib.sha256()
        for value in (
            self.corpus_session_id,
            self.provider_request_identity,
            self.native_artefact_relative_path,
            self.native_artefact_sha256,
            self.provider_definition_ref,
            None if self.gc_contract is None else self.gc_contract.canonical_id(),
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
        if self.canonical_result_kind is not None:
            h.update(encode_field(self.canonical_result_kind))
            h.update(encode_field(self.empty_reason))
            for id_tuple in (self.source_resolved_contract_ids, self.canonical_emitted_contract_ids):
                if id_tuple:
                    for cid in sorted(id_tuple):
                        h.update(encode_field(cid))
        # Storage-layout-defect remediation addition — folded in ONLY when set, same
        # backward-compatibility discipline as the valid-empty ruling's own block immediately
        # above: every row written before this fix existed leaves this `None` and its self-check
        # hash is completely unaffected by this code existing.
        if self.canonical_storage_layout_version is not None:
            h.update(encode_field(self.canonical_storage_layout_version))
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
            "gc_contract": None if contract is None else {
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
            # `is not None` (never bare truthiness) — an EMPTY_VALID row's mapping fields are
            # genuinely provided, empty dicts (`{}`), which must round-trip as `{}`, NEVER
            # collapse to `None` (a bare-truthiness check would silently do exactly that, since
            # `{}` is falsy in Python — this distinction did not matter before the valid-empty
            # architecture ruling, because every pre-existing real row's mappings were always
            # non-empty).
            "canonical_event_counts_by_family": (
                dict(sorted(self.canonical_event_counts_by_family.items()))
                if self.canonical_event_counts_by_family is not None else None
            ),
            "partition_semantic_hashes": (
                dict(sorted(self.partition_semantic_hashes.items()))
                if self.partition_semantic_hashes is not None else None
            ),
            "partition_artifact_hashes": (
                dict(sorted(self.partition_artifact_hashes.items()))
                if self.partition_artifact_hashes is not None else None
            ),
            "evidence_manifest_deterministic_hash": self.evidence_manifest_deterministic_hash,
            "quality_record_ref": self.quality_record_ref,
            "canonical_result_kind": self.canonical_result_kind,
            "empty_reason": self.empty_reason,
            "source_resolved_contract_ids": (
                list(self.source_resolved_contract_ids) if self.source_resolved_contract_ids is not None else None
            ),
            "canonical_emitted_contract_ids": (
                list(self.canonical_emitted_contract_ids) if self.canonical_emitted_contract_ids is not None else None
            ),
            "canonical_storage_layout_version": self.canonical_storage_layout_version,
            "row_identity_sha256": self.row_identity_sha256(),
        }

    @classmethod
    def from_json_dict(cls, doc: Mapping) -> "LineageRow":
        contract_doc = doc.get("gc_contract")
        contract = None
        if contract_doc is not None:
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
            canonical_result_kind=doc.get("canonical_result_kind"),
            empty_reason=doc.get("empty_reason"),
            source_resolved_contract_ids=(
                tuple(doc["source_resolved_contract_ids"]) if doc.get("source_resolved_contract_ids") is not None else None
            ),
            canonical_emitted_contract_ids=(
                tuple(doc["canonical_emitted_contract_ids"]) if doc.get("canonical_emitted_contract_ids") is not None else None
            ),
            canonical_storage_layout_version=doc.get("canonical_storage_layout_version"),
        )


def assert_real_row_is_complete(row: LineageRow) -> None:
    """The stricter completeness gate a REAL (`synthetic=False`) row must pass before its
    session may be marked `CANONICAL_COMPLETE` (WO Part 3, binding invariant: "no session may be
    marked CANONICAL_COMPLETE without lineage evidence existing and verified"). Never invoked
    automatically by `__post_init__` — that constructor-time check stays exactly as permissive as
    it always was, so every pre-existing synthetic test is untouched. This function is the one,
    explicit, separately-callable gate `canonical_worker.py` runs before promoting a session.

    Branches on `canonical_result_kind` (valid-empty architecture ruling, item 5): a legacy row
    predating this field (`canonical_result_kind is None`) or an explicit `"NONEMPTY"` row is
    held to the EXACT ORIGINAL strict requirement — at least one research partition, both hash
    maps populated — completely unchanged. Only a row explicitly marked `"EMPTY_VALID"` is held
    to the alternate, equally strict, zero-partition requirement below; there is no way for a
    row to silently drift between the two, since `canonical_result_kind` is a required, validated
    enum-like field (`LineageRow.__post_init__`)."""
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

    if row.canonical_result_kind == RESULT_KIND_EMPTY_VALID:
        if not row.empty_reason:
            raise LineageError(f"session {row.corpus_session_id!r}: EMPTY_VALID lineage row missing empty_reason")
        if row.gc_contract is not None:
            raise LineageError(
                f"session {row.corpus_session_id!r}: EMPTY_VALID lineage row must not carry a representative "
                f"gc_contract derived from (zero) emitted events"
            )
        if row.research_partition_relative_paths:
            raise LineageError(
                f"session {row.corpus_session_id!r}: EMPTY_VALID lineage row must have exactly zero research "
                f"partitions — found {len(row.research_partition_relative_paths)}"
            )
        if row.partition_semantic_hashes or row.partition_artifact_hashes:
            raise LineageError(
                f"session {row.corpus_session_id!r}: EMPTY_VALID lineage row must have empty partition hash map(s)"
            )
        if row.canonical_event_counts_by_family:
            raise LineageError(
                f"session {row.corpus_session_id!r}: EMPTY_VALID lineage row must have an empty "
                f"canonical_event_counts_by_family"
            )
    else:
        # NONEMPTY, or a legacy row that predates canonical_result_kind entirely — the ORIGINAL,
        # fully unchanged, strict non-empty-partition requirement.
        if not row.research_partition_relative_paths:
            raise LineageError(f"session {row.corpus_session_id!r}: real lineage row has no research partitions")
        if not row.partition_semantic_hashes or not row.partition_artifact_hashes:
            raise LineageError(f"session {row.corpus_session_id!r}: real lineage row missing partition hash map(s)")


def assert_lineage_row_is_storage_layout_v2(row: LineageRow) -> None:
    """Explicit, separately-callable check that `row` was produced under the v2, session-scoped
    canonical storage layout (`canonical-v2/session_id=<...>/...` — see `canonical_worker.py`'s
    `CANONICAL_STORAGE_LAYOUT_VERSION` docstring for the full defect/fix this guards). A row
    whose `canonical_storage_layout_version` is missing (pre-fix v1 row) or holds any value other
    than the current `CANONICAL_STORAGE_LAYOUT_VERSION` must NEVER be silently treated as
    v2-verified just because `assert_real_row_is_complete()` and every other field happen to
    check out — that is exactly the confused-deputy failure mode this function exists to close.
    Never invoked automatically by `__post_init__` or by `assert_real_row_is_complete()`;
    `canonical_worker.py` calls this explicitly wherever v2-storage-layout verification matters
    (the promotion fail-closed guard)."""
    if row.canonical_storage_layout_version != CANONICAL_STORAGE_LAYOUT_VERSION:
        raise LineageError(
            f"session {row.corpus_session_id!r}: lineage row canonical_storage_layout_version="
            f"{row.canonical_storage_layout_version!r} is not the current v2 session-scoped "
            f"layout ({CANONICAL_STORAGE_LAYOUT_VERSION!r}) — refusing to treat as v2-verified"
        )


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
