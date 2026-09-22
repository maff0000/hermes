"""
market_truth.acquisition.canonical_quality_record — per-session HMT-2 canonical-processing
quality record (canonicalisation checkpoint, WO Part 5).

Generalises the exact quality-counting logic already proven in the MBP-1 pilot's own adapter
(`market_truth.providers.databento_mbp1.Mbp1AdapterQualityCounters` — REUSED via `from_adapter_
counters()` below, never reinvented) and adds only the counters that adapter cannot track on its
own, because they are only observable at a DIFFERENT boundary:

  - `duplicate_count` / `conflict_count` — observed at the canonicaliser's own `_register()`
    dedup/conflict boundary (`market_truth.canonicaliser.Canonicaliser`). Counted by
    `market_truth.acquisition.canonical_worker._CountingCanonicaliser`, a thin subclass that
    OBSERVES (never re-implements) the base class's unchanged dedup/conflict logic.
  - `source_sequence_anomaly_count` — a non-monotonic (or duplicate) `source_sequence` value
    observed per raw provider symbol, in the provider's own native record order. Counted by
    `canonical_worker.py`'s own per-record loop (it has direct access to each `RawSourceRecord`
    BEFORE canonicalisation, which this module does not).
  - `observed_contract_count` — the number of distinct canonical GC contract ids this session's
    canonical events actually resolved to (never inferred from the raw symbol set, which may
    contain non-traded contracts).
  - `source_gap_completeness_status` — a conservative, disclosed heuristic (see
    `SessionQualityCounters.source_gap_completeness_status` docstring below): `"COMPLETE"` when
    no sequence anomaly was observed, `"GAP_OR_ANOMALY_SUSPECTED"` otherwise. This module never
    tries to be cleverer than that — a real gap/completeness determination would need the
    provider's own definitions/symbology capability (out of scope for this checkpoint).

This module NEVER synthetically repairs a skipped/bad source record (WO Part 5, absolute) — it
only ever counts what already happened; every count here is additive evidence, never a
correction.

VALID-EMPTY ARCHITECTURE RULING ADDITIONS (additive, v2) — a session that fully, honestly
processes to zero canonical events is a valid successful result, not a failure, but this must be
provably distinguished from a session that produced zero events because something silently
broke. This module adds the fields the ruling requires to make that distinction honest and
checkable from the quality record alone, without inventing any new ledger lifecycle state:

  - `source_observed_symbols` — every raw provider symbol seen in the native stream this
    session, whether or not the record it came from ever became a canonical event (populated
    from BOTH the records `canonical_worker.canonicalise_records()` itself iterates AND, via
    `apply_adapter_counters()`, every native record the adapter observed even when that record
    never became a `RawSourceRecord` at all — e.g. filtered pre-emission for an incomplete or
    crossed book on a non-trade action).
  - `source_resolved_contract_ids` — the FIX for the exact bug this ruling addresses: every
    symbol above that successfully resolved to a real, governed GC contract identity, tracked
    independently of whether canonicalisation of that record went on to emit any canonical
    event. Previously this repository only ever derived "resolved contracts" from EMITTED
    events (`observed_contract_ids` below), so a record that resolved cleanly but was then
    deduplicated, or filtered for a genuinely unchanged BBO, or filtered pre-emission for an
    incomplete/crossed book, always looked identical to "nothing resolved at all".
  - `canonical_result_kind` (`"NONEMPTY"` / `"EMPTY_VALID"`) and `empty_reason` (only meaningful
    for `EMPTY_VALID`) — the result-kind metadata the ruling requires INSTEAD OF a new ledger
    lifecycle state. Both kinds are `CANONICAL_COMPLETE` at the ledger level.

`observed_contract_ids`/`observed_contract_count` keep their EXACT original meaning (distinct
canonical GC contract ids actually appearing on emitted canonical events) — nothing about them
changes; `to_dict()` additionally exposes them under the ruling's own vocabulary as
`canonical_emitted_contract_ids`/`canonical_emitted_contract_count` (same set, same count, new
name — never a second, independent computation).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Set

QUALITY_RECORD_FILE_VERSION = "hmt2-canonical-quality-record-v2"

STATUS_COMPLETE = "COMPLETE"
STATUS_GAP_OR_ANOMALY_SUSPECTED = "GAP_OR_ANOMALY_SUSPECTED"

# Result-kind metadata (architecture ruling) — NOT a new ledger lifecycle state. Both kinds are
# CANONICAL_COMPLETE at the `hmt2i_gc_corpus_canonical_ledger.py` level; this is purely additive
# per-session metadata recorded on the quality record and the lineage row.
RESULT_KIND_NONEMPTY = "NONEMPTY"
RESULT_KIND_EMPTY_VALID = "EMPTY_VALID"
VALID_CANONICAL_RESULT_KINDS = frozenset({RESULT_KIND_NONEMPTY, RESULT_KIND_EMPTY_VALID})

# The two, and only two, genuinely distinct valid-empty sub-cases (architecture ruling item 3).
EMPTY_REASON_SOURCE_RETURNED_ZERO_RECORDS = "SOURCE_RETURNED_ZERO_RECORDS"
EMPTY_REASON_NO_CANONICAL_EMISSIONS_AFTER_VALID_PROCESSING = "NO_CANONICAL_EMISSIONS_AFTER_VALID_PROCESSING"
VALID_EMPTY_REASONS = frozenset(
    {EMPTY_REASON_SOURCE_RETURNED_ZERO_RECORDS, EMPTY_REASON_NO_CANONICAL_EMISSIONS_AFTER_VALID_PROCESSING}
)


class QualityRecordError(ValueError):
    """Fails closed on any structurally invalid quality-record operation."""


def _safe_session_segment(session_id: str) -> str:
    if not session_id:
        raise QualityRecordError("session_id must be non-empty")
    return "".join(c if (c.isalnum() or c in "-_.:") else "_" for c in session_id)


@dataclass
class SessionQualityCounters:
    """Mutable, per-session accumulator. Mutated in place while a session is being canonicalised;
    `to_dict()` (called once, at the end of a successful session) is the durable, immutable
    snapshot written to disk by `write_quality_record_atomic()`."""

    session_id: str
    native_record_count: int = 0
    market_trade_event_count: int = 0
    top_of_book_event_count: int = 0
    unmapped_symbol_count: int = 0
    crossed_book_skipped_count: int = 0
    incomplete_book_skipped_count: int = 0
    bad_receive_time_count: int = 0
    duplicate_count: int = 0
    conflict_count: int = 0
    source_sequence_anomaly_count: int = 0
    observed_contract_ids: Set[str] = field(default_factory=set)

    # ---- valid-empty architecture ruling additions (all additive, default empty/None) ----
    source_observed_symbols: Set[str] = field(default_factory=set)
    source_resolved_contract_ids: Set[str] = field(default_factory=set)
    canonical_result_kind: Optional[str] = None
    empty_reason: Optional[str] = None

    @property
    def observed_contract_count(self) -> int:
        return len(self.observed_contract_ids)

    @property
    def source_resolved_contract_count(self) -> int:
        return len(self.source_resolved_contract_ids)

    @property
    def canonical_event_count(self) -> int:
        return self.market_trade_event_count + self.top_of_book_event_count

    @property
    def source_gap_completeness_status(self) -> str:
        return STATUS_COMPLETE if self.source_sequence_anomaly_count == 0 else STATUS_GAP_OR_ANOMALY_SUSPECTED

    def to_dict(self) -> dict:
        return {
            "quality_record_file_version": QUALITY_RECORD_FILE_VERSION,
            "session_id": self.session_id,
            "native_record_count": self.native_record_count,
            "market_trade_event_count": self.market_trade_event_count,
            "top_of_book_event_count": self.top_of_book_event_count,
            "canonical_event_count": self.canonical_event_count,
            "unmapped_symbol_count": self.unmapped_symbol_count,
            "crossed_book_skipped_count": self.crossed_book_skipped_count,
            "incomplete_book_skipped_count": self.incomplete_book_skipped_count,
            "bad_receive_time_count": self.bad_receive_time_count,
            "duplicate_count": self.duplicate_count,
            "conflict_count": self.conflict_count,
            "source_sequence_anomaly_count": self.source_sequence_anomaly_count,
            "observed_contract_count": self.observed_contract_count,
            "observed_contract_ids": sorted(self.observed_contract_ids),
            # Same set/count as observed_contract_count/observed_contract_ids above, exposed
            # under the architecture ruling's own vocabulary too (never a second computation).
            "canonical_emitted_contract_count": self.observed_contract_count,
            "canonical_emitted_contract_ids": sorted(self.observed_contract_ids),
            "source_observed_symbols": sorted(self.source_observed_symbols),
            "source_resolved_contract_count": self.source_resolved_contract_count,
            "source_resolved_contract_ids": sorted(self.source_resolved_contract_ids),
            "source_gap_completeness_status": self.source_gap_completeness_status,
            "canonical_result_kind": self.canonical_result_kind,
            "empty_reason": self.empty_reason,
        }

    def apply_adapter_counters(self, adapter_counters) -> None:
        """Pull the native-record-level counts straight from the pilot-proven adapter's own
        `Mbp1AdapterQualityCounters` (reused, not reinvented) — see
        `market_truth.providers.databento_mbp1.Mbp1AdapterQualityCounters`. `getattr(...,
        "source_observed_raw_symbols", ())` is defensive: any adapter counters object that
        predates this field (e.g. a test double) is still accepted, simply contributing nothing
        new — additive, never a hard requirement on the adapter's own shape."""
        self.native_record_count = adapter_counters.total_native_records
        self.unmapped_symbol_count = adapter_counters.unmapped_symbol_records
        self.crossed_book_skipped_count = adapter_counters.crossed_book_skipped_records
        self.incomplete_book_skipped_count = adapter_counters.incomplete_book_skipped_records
        self.bad_receive_time_count = adapter_counters.bad_receive_time_records
        self.source_observed_symbols |= set(getattr(adapter_counters, "source_observed_raw_symbols", ()))


def quality_record_relative_path(session_id: str) -> str:
    return f"quality/{_safe_session_segment(session_id)}.json"


def write_quality_record_atomic(root_dir, counters: SessionQualityCounters) -> Path:
    path = Path(root_dir) / quality_record_relative_path(counters.session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    doc = counters.to_dict()
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp_path, path)
    return path


def read_quality_record(root_dir, session_id: str) -> dict:
    path = Path(root_dir) / quality_record_relative_path(session_id)
    if not path.exists():
        raise QualityRecordError(f"no quality record file for session {session_id!r} at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def quality_record_exists(root_dir, session_id: str) -> bool:
    return (Path(root_dir) / quality_record_relative_path(session_id)).exists()
