"""
market_truth.acquisition.canonical_worker — the durable, session-scoped, streaming, crash-safe
HMT-2 canonicalisation pipeline (canonicalisation checkpoint, WO Part 4).

Pipeline (one governed session, verbatim WO order):

    verify native SHA-256 -> decode retained DBN (offline, no network)
        -> map actual GC contracts (market_truth.futures — UNCHANGED, fail-closed)
        -> HMT-1 provider-neutral source records (market_truth.providers.databento_mbp1 —
           UNCHANGED, the exact adapter already proven on the pilot)
        -> HMT-1 canonicaliser (market_truth.canonicaliser — UNCHANGED)
        -> MarketTradeEvent/TopOfBookEvent
        -> HMT-1 partitions (market_truth.partition — UNCHANGED)
        -> evidence manifest (market_truth.evidence — UNCHANGED)
        -> reload verification
        -> lineage record (market_truth.acquisition.lineage)
        -> quality record (market_truth.acquisition.canonical_quality_record)
        -> only THEN may a caller mark the session CANONICAL_COMPLETE.

Nothing in this module ever imports, references, or calls anything network-capable or vendor-SDK-
capable directly — `databento` is never imported here (see
`tests/hmt2/test_canonical_worker_no_network_guard.py`, an AST-based structural guard mirroring
`tests/hmt2/test_no_network_guard.py`'s own pattern). The ONE genuine source of native MBP-1
bytes this module reaches is `market_truth.providers.databento_mbp1.DatabentoMbp1PilotProvider`,
itself a read-only, offline, already-retained-bytes-only adapter (no network, no credential) —
exactly the adapter the MBP-1 pilot already proved.

`canonicalise_records()` is the pure(ish), provider-agnostic core (works with ANY
`MarketDataProvider.iter_records()` stream — this is what lets it be unit-tested against HMT-1's
own zero-vendor-dependency `providers.fixture.FixtureMarketDataProvider`, without needing any real
retained MBP-1 bytes at all). `canonicalise_mbp1_session()` is the thin, REAL wiring on top of it
that constructs `DatabentoMbp1PilotProvider` for one genuine retained session.

Durable canonical research store (WO Part 2) — external configuration, never a hardcoded path,
mirroring `market_truth.acquisition.providers.databento_historical`'s own established
default-path / environment-variable-override convention for its governed credential file
location exactly: `resolve_canonical_research_root()` resolves, in order, an explicit
caller-supplied path, then `HMT2_CANONICAL_RESEARCH_ROOT`, then the default
`research-canonical-store/` at the repo root.
Layout under `<root>/hmt2-gc-mbp1-v1/` (the SAME corpus-store-root NAME
`market_truth.acquisition.source_store.CORPUS_STORE_ROOT_NAME` already uses for native/, so a
session's native and canonical stores are trivially correlatable by name even though they live
under two different roots):

    canonical-v2/  -- Parquet/Zstd research partitions (market_truth.partition, unchanged
                      format), namespaced by `session_id` (see CANONICAL STORAGE-LAYOUT DEFECT
                      REMEDIATION below): canonical-v2/session_id=<...>/<HMT-1 partition dir>/...
    evidence/    -- one evidence-manifest JSON per session
    lineage/     -- one lineage record JSON per session (market_truth.acquisition.lineage)
    quality/     -- one quality record JSON per session (canonical_quality_record.py)
    .staging/    -- transient, per-attempt staging area; never a durable output location

There is deliberately NO `native/` subdirectory created here — the WO's own conceptual layout
explicitly permits reusing the EXISTING, already-governed native-source-store location
(`market_truth.acquisition.source_store`, rooted at `research-source/`) instead of copying bytes
merely to satisfy the diagram; this module's lineage records reference that existing location by
its own relative path, never a duplicate copy (disclosed judgment call — see final report).

CANONICAL STORAGE-LAYOUT DEFECT REMEDIATION (governed correction, additive) — CONFIRMED DEFECT
MECHANISM (quantified against the real 122-session corpus: 57 colliding physical paths, 19
victim sessions, 58 stale-hash mismatches; full forensic detail retained outside this repo):
`market_truth.partition.partition_relative_dir()` computes a canonical Parquet file's LOGICAL
path from ONLY `(contract_id, event.source_event_time.date(), event_family)` — never
`session_id`. The ORIGINAL `_promote_partition_results()` promoted every session's staged output
into ONE SHARED physical tree via blind `os.replace()`, keyed by that same session-blind logical
path. Two different sessions whose events land in the same `(contract, date, family)` bucket
therefore silently collided: whichever session was promoted LAST won, and the earlier session's
own lineage row was left pointing at a physical file it no longer owns (a stale/incorrect
reference — the earlier session's OWN recorded event content and hashes are unaffected, only the
shared physical file on disk drifted out from under it).

THE FIX (this checkpoint, additive, HMT-1 completely untouched) — every promoted partition's
PHYSICAL path is now namespaced by `session_id`: `canonical-v2/session_id=<safe-escaped-id>/
<HMT-1's own unchanged partition_relative_dir() output>/part-00000.parquet`. Two distinct
session_ids can structurally never address the same physical file again, because the very first
path segment is the session_id's own escaped namespace. The LOGICAL partition identity HMT-1
computes (`partition_relative_dir()`, `partition_content_sha256`, row serialization/hashing) is
completely unchanged — only the outer, session-scoped physical-storage prefix this module adds
on top, purely at the promotion/lineage-recording boundary. `CANONICAL_STORAGE_LAYOUT_VERSION`
(imported from `lineage.py`) is recorded on every lineage row this fix produces, so a future
reader can always tell a v2 (session-scoped, collision-proof) row from a v1 (pre-fix,
collision-vulnerable) one — see `lineage.assert_lineage_row_is_storage_layout_v2()`.
`_promote_partition_results()` also now fails closed (raises, never silently overwrites) if a
destination file already exists, UNLESS its content is byte-identical to what is about to be
promoted (this session's own deterministic crash-and-retry — same retained native bytes +
governed code always reproduce the same output bytes) OR this exact session already carries a
verified-complete v2 lineage row (its own idempotent verify-and-reuse path,
`verify_existing_completion()`, unchanged). Any OTHER pre-existing, content-mismatched
destination is refused, never guessed at.

The old, pre-fix, unnamespaced `canonical/` tree (built before this fix existed) is NOT deleted
by this module — it is archived, out-of-band, to a clearly-labelled sibling directory (disclosed
in the fix's own dispatch report) for forensic retention; this module never reads or writes that
old tree.

VALID-EMPTY ARCHITECTURE RULING (v2, additive) — a session that fully, honestly processes to
ZERO canonical events is a valid successful result (`CANONICAL_COMPLETE`, `canonical_result_kind
= "EMPTY_VALID"`), not a failure, but this is provably distinguished from a session that produced
zero events because something silently broke: `canonicalise_records()` now independently tracks
`source_resolved_contract_ids` (every raw provider symbol that resolved to a real, governed GC
contract, checked for EVERY record reaching the loop, and for every native record the adapter
observed even if it was filtered before ever becoming a `RawSourceRecord` — via
`Mbp1AdapterQualityCounters.source_observed_raw_symbols`) alongside `canonical_emitted_contract_
ids` (the OLD, events-only notion, kept under its original name/meaning for `observed_contract_
ids` in the quality record). A session is `EMPTY_VALID` only under the two explicit sub-cases
(`EMPTY_REASON_SOURCE_RETURNED_ZERO_RECORDS` / `EMPTY_REASON_NO_CANONICAL_EMISSIONS_AFTER_VALID_
PROCESSING`) — every other zero-event path (an unresolved symbol, a genuine mapping/integrity
problem) still fails closed exactly as before. No new ledger lifecycle state is introduced; both
`NONEMPTY` and `EMPTY_VALID` are `CANONICAL_COMPLETE`. See `lineage.py`'s module docstring and
`assert_real_row_is_complete()` for how the lineage row itself represents this.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Sequence, Set, Tuple
from uuid import uuid4

import pyarrow as pa

from market_truth.acquisition.canonical_quality_record import (
    EMPTY_REASON_NO_CANONICAL_EMISSIONS_AFTER_VALID_PROCESSING,
    EMPTY_REASON_SOURCE_RETURNED_ZERO_RECORDS,
    RESULT_KIND_EMPTY_VALID,
    RESULT_KIND_NONEMPTY,
    SessionQualityCounters,
    write_quality_record_atomic,
)
from market_truth.acquisition.lineage import (
    CANONICAL_STORAGE_LAYOUT_VERSION,
    LineageError,
    LineageRow,
    assert_lineage_row_is_storage_layout_v2,
    assert_real_row_is_complete,
    lineage_record_exists,
    read_lineage_record,
    write_lineage_record_atomic,
)
from market_truth.canonicaliser import CANONICALISER_VERSION, Canonicaliser, DuplicateConflictError
from market_truth.contracts import MARKET_EVENT_CONTRACT_SCHEMA_VERSION, MarketTradeEvent, TopOfBookEvent
from market_truth.evidence import EVIDENCE_MANIFEST_VERSION, EvidenceManifest
from market_truth.futures import ContractMappingError, ContractMappingTable, GcContractIdentity
from market_truth.identity import EVENT_IDENTITY_ALGORITHM_VERSION
from market_truth.partition import (
    PARQUET_WRITER_LIBRARY,
    PARTITION_CONTRACT_VERSION,
    PartitionReader,
    PartitionWriter,
    event_family_name,
    event_sort_key,
    serialize_event_row,
)
from market_truth.providers.databento_mbp1 import (
    DATABENTO_MBP1_PROVIDER_VERSION,
    DatabentoMbp1PilotProvider,
)
from market_truth.replay import compute_event_set_hash

CANONICAL_WORKER_VERSION = "hmt2-canonical-worker-v2"

# External configuration for the durable canonical research store (WO Part 2) — mirrors
# providers/databento_historical.py's DEFAULT_*/*_ENV_VAR pair exactly. Never hardcoded into any
# call site: every caller in this checkpoint resolves through resolve_canonical_research_root().
DEFAULT_CANONICAL_RESEARCH_ROOT_DIRNAME = "research-canonical-store"
CANONICAL_RESEARCH_ROOT_ENV_VAR = "HMT2_CANONICAL_RESEARCH_ROOT"
CANONICAL_CORPUS_STORE_ROOT_NAME = "hmt2-gc-mbp1-v1"  # matches source_store.CORPUS_STORE_ROOT_NAME

# The v2, session-scoped physical canonical-partition tree name (storage-layout defect
# remediation — see module docstring). Deliberately a NEW name, never a reuse of the old,
# pre-fix "canonical" directory name, so this fix can never accidentally read or write into the
# old, collision-vulnerable tree (which is archived elsewhere, out-of-band, never touched by
# this module).
CANONICAL_STORAGE_ROOT_DIRNAME = "canonical-v2"


class CanonicalWorkerError(RuntimeError):
    """Fails closed on any structurally invalid or unverifiable canonicalisation step."""


class NativeSourceIntegrityError(CanonicalWorkerError):
    """Raised when a retained native artefact's bytes no longer hash to their recorded/expected
    sha256 — refuses to canonicalise possibly-corrupted or drifted source bytes (WO Part 4)."""


class VerificationFailedError(CanonicalWorkerError):
    """Raised when a session CLAIMED complete (a lineage record already exists) fails
    re-verification. Per WO Part 4 this must never be silently re-run: the caller marks the
    session CANONICAL_FAILED/corrupt and stops for investigation."""


def resolve_canonical_research_root(explicit: Optional[str] = None, *, repo_root: Optional[Path] = None) -> Path:
    """Resolve the durable canonical research-store root: explicit argument, else
    `HMT2_CANONICAL_RESEARCH_ROOT`, else `research-canonical-store/` at the repo root. Never a
    hardcoded path baked into any call site (WO Part 2)."""
    if explicit:
        return Path(explicit)
    env_value = os.environ.get(CANONICAL_RESEARCH_ROOT_ENV_VAR)
    if env_value:
        return Path(env_value)
    base = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    return base / DEFAULT_CANONICAL_RESEARCH_ROOT_DIRNAME


def corpus_canonical_store_root(
    explicit: Optional[str] = None, *, repo_root: Optional[Path] = None,
    corpus_store_root_name: str = CANONICAL_CORPUS_STORE_ROOT_NAME,
) -> Path:
    """The corpus-specific subdirectory (`<root>/hmt2-gc-mbp1-v1`) every session's canonical/
    evidence/lineage/quality output lives under."""
    return resolve_canonical_research_root(explicit, repo_root=repo_root) / corpus_store_root_name


def _safe_session_segment(session_id: str) -> str:
    if not session_id:
        raise CanonicalWorkerError("session_id must be non-empty")
    return "".join(c if (c.isalnum() or c in "-_.:") else "_" for c in session_id)


def _session_scoped_relative_dir(session_id: str, hmt1_relative_dir: str) -> str:
    """Storage-layout defect remediation (module docstring): the PHYSICAL directory a partition
    is promoted into, under `canonical-v2/`, namespaced by `session_id` — reuses THIS module's
    own existing `_safe_session_segment()` escaping (already used for this exact session_id
    elsewhere in this module, e.g. staging/evidence/quality file naming), deliberately never a
    second, divergent escaping convention. `hmt1_relative_dir` is `market_truth.partition.
    partition_relative_dir()`'s own, completely unchanged, session-blind LOGICAL output —
    untouched, just given a session-scoped physical prefix on top."""
    return f"session_id={_safe_session_segment(session_id)}/{hmt1_relative_dir}"


def _parse_canonical_contract_id(canonical_id: str) -> GcContractIdentity:
    venue, product_root, year_month = canonical_id.split(":", 2)
    year_str, month_str = year_month.split("-")
    return GcContractIdentity(
        delivery_year=int(year_str), delivery_month=int(month_str), venue=venue, product_root=product_root,
    )


def _family_from_relative_dir(relative_dir: str) -> str:
    marker = "event_type="
    idx = relative_dir.rfind(marker)
    if idx == -1:
        raise CanonicalWorkerError(f"cannot determine event family from partition dir {relative_dir!r}")
    return relative_dir[idx + len(marker):]


class _CountingCanonicaliser(Canonicaliser):
    """OBSERVES (never re-implements) the base `Canonicaliser`'s own, completely unchanged
    `_register()` dedup/conflict semantics, so this checkpoint can persist WO Part 5's
    duplicate/conflict quality counters — only observable at this exact boundary.
    `market_truth/canonicaliser.py` itself remains byte-for-byte unchanged (Part 7); this is new
    orchestration code subclassing it, never a modification of it."""

    def __init__(self, *args, quality_counters: SessionQualityCounters, **kwargs):
        super().__init__(*args, **kwargs)
        self._quality_counters = quality_counters

    def _register(self, event):
        was_seen_before = event.identity_hash in self._seen_rows
        try:
            result = super()._register(event)
        except DuplicateConflictError:
            self._quality_counters.conflict_count += 1
            raise
        if result is None and was_seen_before:
            self._quality_counters.duplicate_count += 1
        return result


@dataclass(frozen=True)
class SessionCanonicalisationResult:
    session_id: str
    native_artefact_sha256: str
    source_record_count: int
    canonical_event_counts_by_family: Mapping[str, int]
    canonical_event_set_hash: str
    partition_relative_paths: Tuple[str, ...]
    partition_semantic_hashes: Mapping[str, str]
    partition_artifact_hashes: Mapping[str, str]
    evidence_manifest_relative_path: str
    evidence_manifest_deterministic_hash: str
    lineage_record_relative_path: str
    quality_record_relative_path: str
    quality_summary: Mapping[str, object]

    # ---- valid-empty architecture ruling additions (all optional, all additive) ----
    canonical_result_kind: str = RESULT_KIND_NONEMPTY  # "NONEMPTY" | "EMPTY_VALID"
    empty_reason: Optional[str] = None
    source_resolved_contract_ids: Tuple[str, ...] = ()
    canonical_emitted_contract_ids: Tuple[str, ...] = ()


def _session_already_has_verified_v2_lineage(canonical_store_root: Path, session_id: str) -> bool:
    """`True` only if `session_id` already carries a lineage row that is BOTH real-complete
    (`assert_real_row_is_complete`) AND explicitly v2-storage-layout-verified
    (`assert_lineage_row_is_storage_layout_v2`) — the one, narrow, legitimate reason a promotion
    destination may already exist (this session's own idempotent verify-and-reuse path). A v1
    row (missing `canonical_storage_layout_version`, or holding an old value) never satisfies
    this, even if every other field happens to check out — see `lineage.py`'s
    `assert_lineage_row_is_storage_layout_v2()` docstring for why that distinction matters."""
    if not lineage_record_exists(canonical_store_root, session_id):
        return False
    try:
        existing_row = read_lineage_record(canonical_store_root, session_id)
        assert_real_row_is_complete(existing_row)
        assert_lineage_row_is_storage_layout_v2(existing_row)
    except LineageError:
        return False
    return True


def _files_byte_identical(path_a: Path, path_b: Path) -> bool:
    """Cheap, streamed (never a whole-file-in-memory assumption) content-identity check, used
    ONLY by the promotion fail-closed guard below to distinguish a session's own SAFE,
    deterministic re-write (e.g. a retry after a crash that happened between promotion and the
    lineage write — same inputs, same governed code, same output bytes, byte-for-byte) from a
    genuinely unexpected/foreign file occupying the same destination path."""
    if path_a.stat().st_size != path_b.stat().st_size:
        return False
    chunk_size = 1024 * 1024
    with open(path_a, "rb") as fa, open(path_b, "rb") as fb:
        while True:
            chunk_a = fa.read(chunk_size)
            chunk_b = fb.read(chunk_size)
            if chunk_a != chunk_b:
                return False
            if not chunk_a:
                return True


def _promote_partition_results(
    staging_root: Path,
    canonical_root: Path,
    promotions: Sequence[Tuple[Path, Path]],
    *,
    canonical_store_root: Path,
    session_id: str,
) -> None:
    """Atomically promote each staged partition file into the durable, SESSION-SCOPED
    `canonical-v2/` tree — one `os.replace()` per file (same filesystem, atomic rename).

    Storage-layout defect remediation (module docstring): promotion no longer blindly overwrites.
    Every destination in `promotions` already lives under this session's own
    `session_id=<...>/` namespace (computed by the caller via `_session_scoped_relative_dir()`),
    so a DIFFERENT session's promotion can structurally never reach one of these paths at all.
    This function still fails CLOSED (raises `CanonicalWorkerError`, never silently overwrites)
    if a destination unexpectedly already exists, UNLESS one of two narrow, legitimate
    conditions holds:

      1. the staged file about to be promoted is BYTE-IDENTICAL to what is already at the
         destination (`_files_byte_identical()`) — this is exactly what a crash-and-retry of
         THIS SAME session looks like (a prior attempt promoted this file, then crashed before
         writing its lineage record; the retry re-derives the same deterministic bytes from the
         same retained native bytes + governed code, so re-promoting them is a harmless no-op);
      2. this exact session already carries a verified-complete v2 lineage row
         (`_session_already_has_verified_v2_lineage()`) — its own idempotent verify-and-reuse
         path (`verify_existing_completion()`).

    Any OTHER pre-existing destination — content that does NOT match, with no verified-complete
    v2 lineage row for this exact session — is refused: that is the one case a namespaced-by-
    session-id physical layout can still leave ambiguous, so it is never silently resolved by
    guessing; a human investigates instead."""
    session_already_verified_v2 = _session_already_has_verified_v2_lineage(canonical_store_root, session_id)
    for src, dst in promotions:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            if not (session_already_verified_v2 or _files_byte_identical(src, dst)):
                raise CanonicalWorkerError(
                    f"session {session_id!r}: unexpected pre-existing canonical artifact at "
                    f"{dst!s} — content differs from what this session is about to promote, and "
                    f"no verified-complete v2 lineage record exists for this exact session — "
                    f"refusing to silently overwrite (fail-closed promotion guard, storage-layout "
                    f"defect remediation)"
                )
        os.replace(src, dst)


def canonicalise_records(
    *,
    session_id: str,
    records: Iterable,
    mapping_table: ContractMappingTable,
    canonical_store_root,
    native_artefact_relative_path: str,
    native_artefact_sha256: str,
    provider_request_identity: str,
    provider_definition_ref: str,
    provider_adapter_version: str,
    fixture_schema_version: str,
    corpus_manifest_ref: str,
    quality_counters: SessionQualityCounters,
    adapter_quality_counters=None,
) -> SessionCanonicalisationResult:
    """The pure(ish), provider-agnostic core pipeline (WO Part 4) — session-scoped: a FRESH
    `Canonicaliser` (via `_CountingCanonicaliser`) is constructed on every call, so two sessions
    never share top-of-book/dedupe state (mirrors `market_truth.replay.run_pipeline`'s own
    documented "a fresh Canonicaliser is constructed on every call" discipline exactly).

    `records` is any iterable of `market_truth.provider.RawSourceRecord` in the provider's own
    native order — this is what lets this function be exercised in tests against
    `market_truth.providers.fixture.FixtureMarketDataProvider` (zero vendor dependency, zero real
    retained bytes required), while `canonicalise_mbp1_session()` below wires in the real
    `DatabentoMbp1PilotProvider` for a genuine retained session.

    Streaming/bounded processing: iterates `records` once, accumulating canonical events into one
    in-memory list — the pilot's own two sessions (up to 1,266,770 native records) already proved
    this is a manageable working set for one session's worth of data; this checkpoint's corpus
    sessions are not expected to be dramatically larger (WO Part 4 — a bigger rewrite is
    deliberately NOT undertaken here unless a concrete session is shown to break it).

    Crash safety: partitions are written to a per-attempt STAGING directory first, then promoted
    into the canonical/ tree by atomic per-file rename; evidence/quality/lineage files are each
    written via temp-path-then-`os.replace()`. The lineage record — the one thing a caller's
    ledger must see before marking a session `CANONICAL_COMPLETE` — is written LAST, after a
    reload-verification pass confirms the just-promoted partitions reconstruct byte-identical
    canonical events. If this function raises at ANY point, no lineage record exists for this
    session, so a caller can never mistake a partial/interrupted run for a complete one.
    """
    canonical_store_root = Path(canonical_store_root)
    canonicaliser = _CountingCanonicaliser(mapping_table=mapping_table, quality_counters=quality_counters)

    events = []
    last_sequence_by_symbol: Dict[str, int] = {}
    canonical_emitted_contract_ids: Set[str] = set()
    source_observed_symbols: Set[str] = set()
    records_seen = 0

    # Source-side contract resolution, tracked independently of canonical-event emission (the
    # architecture ruling's core correction). `resolved_cache` memoises one `mapping_table.
    # resolve()` call per DISTINCT raw provider symbol seen — cheap (a session sees very few
    # distinct symbols), and identical to the resolution the canonicaliser performs internally,
    # never a second, divergent notion of "resolved".
    resolved_cache: Dict[str, Optional[str]] = {}

    def _resolve_symbol(symbol: str) -> None:
        if symbol in resolved_cache:
            return
        try:
            resolved_cache[symbol] = mapping_table.resolve(symbol).canonical_id()
        except ContractMappingError:
            resolved_cache[symbol] = None

    for record in records:
        records_seen += 1
        symbol = record.raw_provider_symbol
        source_observed_symbols.add(symbol)
        # Resolve THIS record's symbol regardless of whether canonicalise() below goes on to
        # emit any event for it — fixes the exact bug the architecture ruling addresses: a
        # record may resolve to a real, governed GC contract and still legitimately emit zero
        # events (an exact duplicate already registered, or a genuinely unchanged BBO), which
        # previously made it indistinguishable from a record that never resolved at all.
        _resolve_symbol(symbol)
        # v3 quality-instrumentation correction: `source_sequence` is Databento's own CHANNEL-
        # level (not per-symbol) venue sequence counter — identical, verbatim documentation
        # across MBO/MBP-1/MBP-10/Trade ("the message sequence number assigned at the venue"),
        # CME MDP 3.0's own multiplexed packet counter shared by every instrument on the
        # channel. Filtered down to one symbol it looks non-monotonic under completely normal
        # cross-symbol interleaving, so this is kept ONLY as a raw diagnostic
        # (`sequence_non_monotonic_per_symbol_count`) — it must never again be treated as a
        # gap/anomaly signal (`source_gap_completeness_status` now derives from Databento's own
        # real, decoded `MAYBE_BAD_BOOK` channel-gap flag instead — see
        # `canonical_quality_record.py`'s module docstring).
        seq = record.source_sequence
        if seq is not None:
            prev = last_sequence_by_symbol.get(symbol)
            if prev is not None and seq <= prev:
                quality_counters.sequence_non_monotonic_per_symbol_count += 1
            last_sequence_by_symbol[symbol] = seq

        for event in canonicaliser.canonicalise(record):
            events.append(event)
            contract_id = getattr(event, "contract_id", None)
            if contract_id is not None:
                canonical_emitted_contract_ids.add(contract_id)

    if adapter_quality_counters is not None:
        quality_counters.apply_adapter_counters(adapter_quality_counters)
        # Native records that never became a RawSourceRecord at all (filtered upstream in the
        # adapter — e.g. an incomplete/crossed book on a non-trade action) never reached the
        # loop above, so they were never resolved either. They still had a real raw symbol
        # genuinely observed by the adapter (`source_observed_raw_symbols`) — resolve those too,
        # so this session is never falsely reported as "resolved zero contracts" purely because
        # every one of its records happened to be filtered before ever reaching this loop.
        for symbol in getattr(adapter_quality_counters, "source_observed_raw_symbols", ()):
            source_observed_symbols.add(symbol)
            _resolve_symbol(symbol)
    else:
        quality_counters.native_record_count = records_seen

    source_resolved_contract_ids = {cid for cid in resolved_cache.values() if cid is not None}
    unresolved_source_symbols = {sym for sym, cid in resolved_cache.items() if cid is None}

    quality_counters.observed_contract_ids = canonical_emitted_contract_ids
    quality_counters.source_observed_symbols = source_observed_symbols
    quality_counters.source_resolved_contract_ids = source_resolved_contract_ids
    quality_counters.market_trade_event_count = sum(1 for e in events if isinstance(e, MarketTradeEvent))
    quality_counters.top_of_book_event_count = sum(1 for e in events if isinstance(e, TopOfBookEvent))

    if unresolved_source_symbols:
        # A symbol genuinely observed in the native stream that does NOT resolve to a governed
        # GC contract is always a real mapping/integrity problem (architecture ruling item 3,
        # "same for any unmapped symbol") — never valid-empty, never silently swallowed, even
        # when it comes from a record that would otherwise have been filtered before ever
        # reaching the canonicaliser (the one new case this correction can now detect that the
        # old code structurally never even looked at).
        raise CanonicalWorkerError(
            f"session {session_id!r}: source symbol(s) {sorted(unresolved_source_symbols)!r} were "
            f"observed in the native stream but do not resolve to a governed GC contract identity "
            f"— a genuine mapping/integrity problem, refusing to classify this session as complete"
        )

    canonical_event_count = len(events)
    native_record_count = quality_counters.native_record_count

    # ---- NONEMPTY vs. EMPTY_VALID classification (architecture ruling items 1 and 3) ----
    # Result-kind metadata, never a new ledger lifecycle state: both kinds are, and remain,
    # CANONICAL_COMPLETE. A session that fully, honestly processes to zero canonical events is a
    # valid successful result -- but ONLY under the two explicit, checked sub-cases below; every
    # other zero-event path (a genuine mapping/integrity problem) fails closed above already, or
    # right here, and is never silently reclassified as empty-but-fine.
    if canonical_event_count == 0:
        if native_record_count == 0:
            empty_reason = EMPTY_REASON_SOURCE_RETURNED_ZERO_RECORDS
        elif source_resolved_contract_ids:
            empty_reason = EMPTY_REASON_NO_CANONICAL_EMISSIONS_AFTER_VALID_PROCESSING
        else:
            # native_record_count > 0 but zero records resolved to a governed contract, and zero
            # canonical events were emitted -- a genuine mapping/integrity problem, never valid-
            # empty (architecture ruling item 3, verbatim).
            raise CanonicalWorkerError(
                f"session {session_id!r}: {native_record_count} native record(s) processed, zero "
                f"resolved to a governed GC contract, and zero canonical events were emitted — "
                f"NOT a valid-empty result, refusing to build a lineage row with no observed "
                f"contract and no honest empty-reason"
            )
        canonical_result_kind = RESULT_KIND_EMPTY_VALID
    else:
        empty_reason = None
        canonical_result_kind = RESULT_KIND_NONEMPTY

    quality_counters.canonical_result_kind = canonical_result_kind
    quality_counters.empty_reason = empty_reason

    # Deterministic empty-result identity (architecture ruling item 4): `compute_event_set_hash`
    # already supports (and, per its own regression test, deterministically/reproducibly
    # supports) an empty event list -- this is HMT-1's EXISTING, unmodified identity algorithm,
    # never a hand-crafted magic hash for the empty case.
    event_set_hash = compute_event_set_hash(events)
    event_counts_by_family = dict(Counter(event_family_name(e) for e in events))

    # ---- staging + atomic promotion (crash safety) ----
    staging_parent = canonical_store_root / ".staging"
    session_segment = _safe_session_segment(session_id)
    for stale in staging_parent.glob(f"{session_segment}-*"):
        shutil.rmtree(stale, ignore_errors=True)  # leftover from a prior crashed attempt; disposable

    staging_root = staging_parent / f"{session_segment}-{os.getpid()}-{uuid4().hex[:8]}"
    writer = PartitionWriter(staging_root)
    partition_results = writer.write(events)

    # ---- storage-layout defect remediation: session-scoped physical destination paths ----
    # `dest_relative_path` is what every downstream consumer (evidence manifest, lineage row,
    # reload verification, load_session_canonical_events) sees and stores — it is the PHYSICAL,
    # session-namespaced path, relative to `canonical_root` below. HMT-1's own
    # `partition_relative_dir()`/`event_family_name()` outputs (`r.relative_dir`, `r.file_name`)
    # are completely unchanged; only this module adds the `session_id=<...>/` prefix, purely at
    # the promotion/recording boundary.
    canonical_root = canonical_store_root / CANONICAL_STORAGE_ROOT_DIRNAME
    dest_relative_dir_by_result = {
        r: _session_scoped_relative_dir(session_id, r.relative_dir) for r in partition_results
    }
    promotions = [
        (
            staging_root / r.relative_dir / r.file_name,
            canonical_root / dest_relative_dir_by_result[r] / r.file_name,
        )
        for r in partition_results
    ]
    _promote_partition_results(
        staging_root, canonical_root, promotions,
        canonical_store_root=canonical_store_root, session_id=session_id,
    )
    shutil.rmtree(staging_root, ignore_errors=True)

    if partition_results:
        writer_version = partition_results[0].writer_version
        writer_config_id = partition_results[0].writer_config_id
    else:
        writer_version = pa.__version__
        writer_config_id = PartitionWriter.WRITER_CONFIG_ID

    partition_semantic_hashes = {
        f"{dest_relative_dir_by_result[r]}/{r.file_name}": r.partition_content_sha256 for r in partition_results
    }
    partition_artifact_hashes = {
        f"{dest_relative_dir_by_result[r]}/{r.file_name}": r.artifact_sha256 for r in partition_results
    }

    manifest = EvidenceManifest(
        manifest_version=EVIDENCE_MANIFEST_VERSION,
        source_fixture_hashes={session_id: native_artefact_sha256},
        fixture_schema_version=fixture_schema_version,
        provider_adapter_version=provider_adapter_version,
        contract_mapping_version=mapping_table.version,
        contract_mapping_hash=mapping_table.content_sha256(),
        canonical_schema_version=MARKET_EVENT_CONTRACT_SCHEMA_VERSION,
        canonicaliser_version=CANONICALISER_VERSION,
        event_identity_algorithm_version=EVENT_IDENTITY_ALGORITHM_VERSION,
        partition_contract_version=PARTITION_CONTRACT_VERSION,
        writer_library=PARQUET_WRITER_LIBRARY,
        writer_version=writer_version,
        writer_config_id=writer_config_id,
        source_record_count=quality_counters.native_record_count,
        canonical_event_counts_by_family=event_counts_by_family,
        canonical_event_set_hash=event_set_hash,
        partition_content_hashes=partition_semantic_hashes,
        artifact_hashes=partition_artifact_hashes,
        provenance_quality_summary=dict(Counter(e.quality_state.value for e in events)),
    )

    # ---- reload verification — BEFORE any evidence/lineage file is written ----
    # Partition-bounded semantic reload verification (bounded-memory fix — see the real
    # production-incident investigation retained outside this repo: the ORIGINAL form
    # re-materialised the ENTIRE session's canonical events a SECOND time into a session-wide
    # `reloaded_rows` list while the first copy (`events`) was still resident, then built a
    # session-wide sorted `live_rows` copy on top of that, and compared — three full-session-
    # sized structures resident at once. On a real 1,335,855-record session this drove RSS to
    # 8.97+ GiB and caused sustained cgroup MemoryHigh reclaim thrashing.
    #
    # This replacement processes every `PartitionWriteResult` ONE AT A TIME, proving the exact
    # round-trip guarantee the original design intended — that `PartitionReader` can reconstruct
    # the just-promoted partition into the identical canonical events that were written — without
    # ever holding more than one partition's reconstructed events in memory:
    #
    #   1-2. recompute the promoted PHYSICAL file's own sha256 (streamed, never a whole-file-in-
    #        memory read) and require it to equal the `artifact_sha256` already recorded for this
    #        partition by `PartitionWriter.write()`.
    #   3-4. reload THIS partition alone through the governed, unmodified HMT-1 `PartitionReader`,
    #        and require the reloaded row count to equal this partition's own `row_count`.
    #   5-7. re-serialize every reloaded event with the existing, unmodified HMT-1
    #        `serialize_event_row()`, in the existing, unmodified HMT-1 `event_sort_key()` order
    #        (the SAME deterministic persisted order `PartitionWriter.write()` used), and
    #        recompute the SAME semantic-hash algorithm it uses
    #        (`len(row_bytes).to_bytes(4, "big") + row_bytes`, chained through one sha256) — then
    #        require the result to equal the already-recorded `partition_content_sha256`.
    #   8.   this partition's reloaded events are released (`del`) before the next partition is
    #        even read — at most one partition's reconstructed events are ever resident at a
    #        time; never a session-wide reloaded-event collection, never a second full-session
    #        serialized-row list, never a whole-session reload sort.
    #
    # A failure at ANY partition (either check) fails closed exactly as the original whole-session
    # comparison did: raises `CanonicalWorkerError` before any evidence/lineage/quality file is
    # written, so a caller can never mistake a partially-verified session for a complete one.
    reader = PartitionReader(canonical_root)
    for r in partition_results:
        relative_dir = dest_relative_dir_by_result[r]
        partition_file_path = canonical_root / relative_dir / r.file_name

        artifact_hasher = hashlib.sha256()
        with open(partition_file_path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                artifact_hasher.update(chunk)
        recomputed_artifact_sha256 = artifact_hasher.hexdigest()
        if recomputed_artifact_sha256 != r.artifact_sha256:
            raise CanonicalWorkerError(
                f"session {session_id!r}: reload verification FAILED — physical artifact sha256 "
                f"for promoted partition {relative_dir}/{r.file_name} does not match the value "
                f"recorded at write time (recorded={r.artifact_sha256}, "
                f"recomputed={recomputed_artifact_sha256}); refusing to write "
                f"evidence/lineage/quality for an unverified session"
            )

        reloaded_events = reader.read_events(relative_dir, r.event_family)
        if len(reloaded_events) != r.row_count:
            raise CanonicalWorkerError(
                f"session {session_id!r}: reload verification FAILED — partition "
                f"{relative_dir}/{r.file_name} reloaded {len(reloaded_events)} row(s) through "
                f"PartitionReader, expected {r.row_count}; refusing to write "
                f"evidence/lineage/quality for an unverified session"
            )

        semantic_hasher = hashlib.sha256()
        for event in sorted(reloaded_events, key=event_sort_key):
            row_bytes = serialize_event_row(event)
            semantic_hasher.update(len(row_bytes).to_bytes(4, "big"))
            semantic_hasher.update(row_bytes)
        recomputed_semantic_sha256 = semantic_hasher.hexdigest()
        del reloaded_events
        if recomputed_semantic_sha256 != r.partition_content_sha256:
            raise CanonicalWorkerError(
                f"session {session_id!r}: reload verification FAILED — semantic content sha256 "
                f"reconstructed from partition {relative_dir}/{r.file_name} via PartitionReader "
                f"does not match the value recorded at write time "
                f"(recorded={r.partition_content_sha256}, "
                f"recomputed={recomputed_semantic_sha256}); refusing to write "
                f"evidence/lineage/quality for an unverified session"
            )

    evidence_relative_path = f"evidence/{session_segment}.json"
    evidence_path = canonical_store_root / evidence_relative_path
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_evidence_path = evidence_path.with_name(evidence_path.name + ".tmp")
    # `EvidenceManifest` itself (market_truth/evidence.py) is HMT-1, byte-for-byte unchanged —
    # `canonical_storage_layout_version` is added as a sibling key on the on-disk JSON DOCUMENT
    # only, at this module's own serialization boundary, never as a field on the dataclass
    # itself. It is deliberately excluded from `deterministic_fields_sha256()`'s own input
    # (unchanged, HMT-1 logic) — it describes WHERE this session's bytes physically live, not
    # WHAT they deterministically are.
    evidence_doc = manifest.to_json_dict()
    evidence_doc["canonical_storage_layout_version"] = CANONICAL_STORAGE_LAYOUT_VERSION
    with open(tmp_evidence_path, "w", encoding="utf-8") as f:
        json.dump(evidence_doc, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp_evidence_path, evidence_path)

    quality_path = write_quality_record_atomic(canonical_store_root, quality_counters)
    quality_relative_path = str(quality_path.relative_to(canonical_store_root))

    # A representative contract may only ever be derived from an EMITTED event (never
    # invented/guessed for an EMPTY_VALID session) — for EMPTY_VALID, `gc_contract` is `None`;
    # the real per-contract detail lives in `source_resolved_contract_ids` on the row instead
    # (architecture ruling item 6, disclosed choice — see lineage.py module docstring).
    if canonical_result_kind == RESULT_KIND_NONEMPTY:
        representative_contract = _parse_canonical_contract_id(sorted(canonical_emitted_contract_ids)[0])
    else:
        representative_contract = None
    row = LineageRow(
        corpus_session_id=session_id,
        provider_request_identity=provider_request_identity,
        native_artefact_relative_path=native_artefact_relative_path,
        native_artefact_sha256=native_artefact_sha256,
        provider_definition_ref=provider_definition_ref,
        gc_contract=representative_contract,
        canonical_event_set_hash=event_set_hash,
        research_partition_relative_paths=tuple(sorted(partition_semantic_hashes)),
        evidence_manifest_ref=evidence_relative_path,
        synthetic=False,
        corpus_manifest_ref=corpus_manifest_ref,
        canonical_schema_version=MARKET_EVENT_CONTRACT_SCHEMA_VERSION,
        canonicaliser_version=CANONICALISER_VERSION,
        event_identity_algorithm_version=EVENT_IDENTITY_ALGORITHM_VERSION,
        canonical_event_counts_by_family=event_counts_by_family,
        partition_semantic_hashes=partition_semantic_hashes,
        partition_artifact_hashes=partition_artifact_hashes,
        evidence_manifest_deterministic_hash=manifest.deterministic_fields_sha256(),
        quality_record_ref=quality_relative_path,
        canonical_result_kind=canonical_result_kind,
        empty_reason=empty_reason,
        source_resolved_contract_ids=tuple(sorted(source_resolved_contract_ids)),
        canonical_emitted_contract_ids=tuple(sorted(canonical_emitted_contract_ids)),
        canonical_storage_layout_version=CANONICAL_STORAGE_LAYOUT_VERSION,
    )
    assert_real_row_is_complete(row)  # last gate before the one file a caller checks for "complete"
    lineage_path = write_lineage_record_atomic(canonical_store_root, row)
    lineage_relative_path = str(lineage_path.relative_to(canonical_store_root))

    return SessionCanonicalisationResult(
        session_id=session_id,
        native_artefact_sha256=native_artefact_sha256,
        source_record_count=quality_counters.native_record_count,
        canonical_event_counts_by_family=event_counts_by_family,
        canonical_event_set_hash=event_set_hash,
        partition_relative_paths=tuple(sorted(partition_semantic_hashes)),
        partition_semantic_hashes=partition_semantic_hashes,
        partition_artifact_hashes=partition_artifact_hashes,
        evidence_manifest_relative_path=evidence_relative_path,
        evidence_manifest_deterministic_hash=manifest.deterministic_fields_sha256(),
        lineage_record_relative_path=lineage_relative_path,
        quality_record_relative_path=quality_relative_path,
        quality_summary=quality_counters.to_dict(),
        canonical_result_kind=canonical_result_kind,
        empty_reason=empty_reason,
        source_resolved_contract_ids=tuple(sorted(source_resolved_contract_ids)),
        canonical_emitted_contract_ids=tuple(sorted(canonical_emitted_contract_ids)),
    )


def canonicalise_mbp1_session(
    *,
    session_id: str,
    native_artefact_path,
    native_artefact_relative_path: str,
    expected_native_sha256: str,
    mapping_table_path,
    canonical_store_root,
    provider_request_identity: str,
    provider_definition_ref: str,
    corpus_manifest_ref: str,
    acquisition_epoch: str,
    provider_id: str = "databento",
    dataset_id: str = "GLBX.MDP3",
) -> SessionCanonicalisationResult:
    """The REAL wiring (WO Part 4/6): verify the retained native artefact's sha256, decode it
    fully offline via `DatabentoMbp1PilotProvider` (no network, no credential — see module
    docstring), then hand its records to `canonicalise_records()` above."""
    native_path = Path(native_artefact_path)
    if not native_path.exists():
        raise NativeSourceIntegrityError(f"session {session_id!r}: no native artefact at {native_path}")

    provider = DatabentoMbp1PilotProvider(
        retained_path=str(native_path), session_id=session_id, acquisition_epoch=acquisition_epoch,
        provider_id=provider_id, dataset_id=dataset_id,
    )
    actual_sha256 = provider.content_sha256()
    if actual_sha256 != expected_native_sha256:
        raise NativeSourceIntegrityError(
            f"session {session_id!r}: retained artefact sha256 drift — expected="
            f"{expected_native_sha256} actual={actual_sha256}. Refusing to canonicalise possibly-"
            f"corrupted bytes."
        )

    mapping_table = ContractMappingTable.from_json_file(Path(mapping_table_path))
    quality_counters = SessionQualityCounters(session_id=session_id)

    return canonicalise_records(
        session_id=session_id,
        records=provider.iter_records(),
        mapping_table=mapping_table,
        canonical_store_root=canonical_store_root,
        native_artefact_relative_path=native_artefact_relative_path,
        native_artefact_sha256=actual_sha256,
        provider_request_identity=provider_request_identity,
        provider_definition_ref=provider_definition_ref,
        provider_adapter_version=DATABENTO_MBP1_PROVIDER_VERSION,
        fixture_schema_version=DATABENTO_MBP1_PROVIDER_VERSION,
        corpus_manifest_ref=corpus_manifest_ref,
        quality_counters=quality_counters,
        adapter_quality_counters=provider.quality_counters,
    )


def verify_existing_completion(
    *, canonical_store_root, session_id: str, native_artefact_path, expected_native_sha256: str,
) -> bool:
    """Idempotent-reprocessing check (WO Part 4): `True` if `session_id` already has a
    verified-complete lineage record whose native hash, canonical-partition hashes, and evidence
    manifest all still check out. `False` if no lineage record exists at all (never processed).
    Raises `VerificationFailedError` if a lineage record EXISTS but verification fails — a
    claimed-complete session must never be silently re-run; the caller marks it
    `CANONICAL_FAILED`/corrupt and stops."""
    canonical_store_root = Path(canonical_store_root)
    if not lineage_record_exists(canonical_store_root, session_id):
        return False

    try:
        row = read_lineage_record(canonical_store_root, session_id)
    except LineageError as exc:
        raise VerificationFailedError(f"session {session_id!r}: lineage record failed self-check: {exc}") from exc

    try:
        assert_real_row_is_complete(row)
    except LineageError as exc:
        raise VerificationFailedError(
            f"session {session_id!r}: claimed-complete lineage row failed completeness check: {exc}"
        ) from exc

    native_path = Path(native_artefact_path)
    if not native_path.exists():
        raise VerificationFailedError(
            f"session {session_id!r}: native artefact missing at {native_path} for a claimed-complete session"
        )
    actual_native_sha256 = hashlib.sha256(native_path.read_bytes()).hexdigest()
    if actual_native_sha256 != row.native_artefact_sha256 or actual_native_sha256 != expected_native_sha256:
        raise VerificationFailedError(
            f"session {session_id!r}: native artefact hash drift (recorded={row.native_artefact_sha256}, "
            f"expected={expected_native_sha256}, actual={actual_native_sha256})"
        )

    canonical_root = canonical_store_root / CANONICAL_STORAGE_ROOT_DIRNAME
    for relative_path, expected_hash in row.partition_artifact_hashes.items():
        full_path = canonical_root / relative_path
        if not full_path.exists():
            raise VerificationFailedError(f"session {session_id!r}: missing canonical partition file {relative_path}")
        actual_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise VerificationFailedError(
                f"session {session_id!r}: canonical partition artifact hash drift for {relative_path}"
            )

    evidence_path = canonical_store_root / row.evidence_manifest_ref
    if not evidence_path.exists():
        raise VerificationFailedError(f"session {session_id!r}: missing evidence manifest at {evidence_path}")
    evidence_doc = json.loads(evidence_path.read_text(encoding="utf-8"))
    if evidence_doc.get("deterministic_fields_sha256") != row.evidence_manifest_deterministic_hash:
        raise VerificationFailedError(f"session {session_id!r}: evidence manifest deterministic hash drift")

    quality_path = canonical_store_root / row.quality_record_ref
    if not quality_path.exists():
        raise VerificationFailedError(f"session {session_id!r}: missing quality record at {quality_path}")

    return True


def load_session_canonical_events(canonical_store_root, session_id: str) -> list:
    """Read back ONE session's own durably-recorded canonical events straight from the canonical
    Parquet partitions its (verified) lineage record points to — never from memory."""
    row = read_lineage_record(canonical_store_root, session_id)
    canonical_root = Path(canonical_store_root) / CANONICAL_STORAGE_ROOT_DIRNAME
    reader = PartitionReader(canonical_root)
    events = []
    for relative_path in row.research_partition_relative_paths:
        relative_dir, _file_name = relative_path.rsplit("/", 1)
        family = _family_from_relative_dir(relative_dir)
        events.extend(reader.read_events(relative_dir, family))
    return events


def compute_corpus_level_event_set_hash(canonical_store_root, session_ids: Sequence[str]) -> str:
    """Union every listed session's OWN durably-recorded canonical events (read back from disk)
    and hash them together exactly as `market_truth.replay.compute_event_set_hash` would over one
    combined run — the exact comparison WO Part 6's pilot-reproduction gate needs: the pilot's own
    original disposable script ran BOTH pilot sessions through one shared, multi-session pipeline
    and hashed the union; this checkpoint's durable pipeline runs each session independently and
    must reproduce that same union hash when the two sessions' own canonical events are combined
    after the fact."""
    all_events = []
    for session_id in session_ids:
        all_events.extend(load_session_canonical_events(canonical_store_root, session_id))
    return compute_event_set_hash(all_events)
