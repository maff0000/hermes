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
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Set

QUALITY_RECORD_FILE_VERSION = "hmt2-canonical-quality-record-v1"

STATUS_COMPLETE = "COMPLETE"
STATUS_GAP_OR_ANOMALY_SUSPECTED = "GAP_OR_ANOMALY_SUSPECTED"


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

    @property
    def observed_contract_count(self) -> int:
        return len(self.observed_contract_ids)

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
            "unmapped_symbol_count": self.unmapped_symbol_count,
            "crossed_book_skipped_count": self.crossed_book_skipped_count,
            "incomplete_book_skipped_count": self.incomplete_book_skipped_count,
            "bad_receive_time_count": self.bad_receive_time_count,
            "duplicate_count": self.duplicate_count,
            "conflict_count": self.conflict_count,
            "source_sequence_anomaly_count": self.source_sequence_anomaly_count,
            "observed_contract_count": self.observed_contract_count,
            "observed_contract_ids": sorted(self.observed_contract_ids),
            "source_gap_completeness_status": self.source_gap_completeness_status,
        }

    def apply_adapter_counters(self, adapter_counters) -> None:
        """Pull the native-record-level counts straight from the pilot-proven adapter's own
        `Mbp1AdapterQualityCounters` (reused, not reinvented) — see
        `market_truth.providers.databento_mbp1.Mbp1AdapterQualityCounters`."""
        self.native_record_count = adapter_counters.total_native_records
        self.unmapped_symbol_count = adapter_counters.unmapped_symbol_records
        self.crossed_book_skipped_count = adapter_counters.crossed_book_skipped_records
        self.incomplete_book_skipped_count = adapter_counters.incomplete_book_skipped_records
        self.bad_receive_time_count = adapter_counters.bad_receive_time_records


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
