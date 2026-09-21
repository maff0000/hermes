"""
market_truth.partition — deterministic canonical row serialization + research-partition
writer/reader.

Implements docs/architecture/hmt0-market-truth-v2/data-lifecycle-and-storage.md §1.2 (the
append-oriented, Parquet/Zstd-partitioned research store) and this WO's §10-12: a deterministic
logical layout, exactly one canonical row serialization (never JSON default key-ordering/float
formatting for anything identity-bearing), and two distinct partition identities:

- `partition_content_sha256` — SEMANTIC/authoritative. Computed from `serialize_event_row()` over
  every row, in a fixed deterministic row order, independent of the Parquet writer. This is the
  identity that MUST reproduce exactly on every replay.
- `artifact_sha256` — PHYSICAL. A hash of the actual emitted Parquet file bytes. Whether this is
  also reproducible run-to-run depends on the pinned writer's own byte-for-byte determinism; see
  `test_partition_roundtrip.py` and the final HMT-1 report for the measured result. Per WO §12,
  semantic determinism is never weakened to compensate for any physical non-determinism found.

Local/disposable test filesystem storage only (WO §10) — no production object-store deployment, no
HMT-1 historical events in operational MariaDB.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

from market_truth.contracts import (
    AggressorBasis,
    AggressorClassification,
    AggressorSide,
    BookLevel,
    EventQualityState,
    ExactPrice,
    MarketQuoteEvent,
    MarketTradeEvent,
    TopOfBookEvent,
)
from market_truth.identity import encode_field
from market_truth.provenance import (
    HistoricalProvenanceEra,
    ProvenanceEnvelope,
    ProviderReceiptQuality,
    SequenceAvailability,
    SourceClassification,
    TimestampPrecision,
    parse_utc_text,
)

PARTITION_CONTRACT_VERSION = "hmt1-research-partition-contract-v1"
PARQUET_WRITER_LIBRARY = "pyarrow"
PARQUET_COMPRESSION = "zstd"

CanonicalEvent = object  # MarketTradeEvent | TopOfBookEvent | MarketQuoteEvent (typing.Union avoided for py3.9-friendly runtime isinstance checks below)


class PartitionError(ValueError):
    """Raised on any structurally invalid partition write/read operation."""


# ---------------------------------------------------------------------------
# §11 — exactly one canonical row serialization
# ---------------------------------------------------------------------------

# Fixed provenance column order shared by every event family. `hermes_receive_time` is
# deliberately excluded (wall-clock — see provenance.py module docstring); every event produced by
# the fixture provider carries it as None anyway, so nothing observable is lost for HMT-1.
_PROVENANCE_FIELDS: Tuple[str, ...] = (
    "provider_id",
    "dataset_id",
    "source_classification",
    "source_record_schema",
    "raw_provider_symbol",
    "source_artifact_id",
    "source_event_time_text",
    "source_timestamp_precision",
    "provider_receive_time_text",
    "provider_receive_quality",
    "source_sequence",
    "sequence_domain",
    "sequence_availability",
    "acquisition_epoch",
    "canonicaliser_version",
    "schema_version",
    "historical_provenance_era",
)


def _provenance_values(p: ProvenanceEnvelope) -> List[Optional[str]]:
    return [
        p.provider_id,
        p.dataset_id,
        p.source_classification.value,
        p.source_record_schema,
        p.raw_provider_symbol,
        p.source_artifact_id,
        p.source_event_time_text,
        p.source_timestamp_precision.value,
        p.provider_receive_time_text,
        p.provider_receive_quality.value,
        None if p.source_sequence is None else str(p.source_sequence),
        p.sequence_domain,
        p.sequence_availability.value,
        p.acquisition_epoch,
        p.canonicaliser_version,
        p.schema_version,
        p.historical_provenance_era.value,
    ]


TRADE_ROW_FIELDS: Tuple[str, ...] = (
    "identity_hash", "event_ordinal", "contract_id", "price_mantissa", "price_scale",
    "quantity", "aggressor_side", "aggressor_basis", "aggressor_confidence", "quality_state",
) + _PROVENANCE_FIELDS

TOP_OF_BOOK_ROW_FIELDS: Tuple[str, ...] = (
    "identity_hash", "event_ordinal", "contract_id",
    "bid_price_mantissa", "bid_price_scale", "bid_quantity", "bid_order_count",
    "ask_price_mantissa", "ask_price_scale", "ask_quantity", "ask_order_count",
    "quality_state",
) + _PROVENANCE_FIELDS

QUOTE_ROW_FIELDS: Tuple[str, ...] = (
    "identity_hash", "event_ordinal", "instrument_id",
    "bid_price_mantissa", "bid_price_scale", "ask_price_mantissa", "ask_price_scale",
    "quality_state",
) + _PROVENANCE_FIELDS


def _row_values(event) -> List[Optional[str]]:
    """The ordered value list matching this event's *_ROW_FIELDS constant, as strings (never
    binary float) — the single source of truth both `serialize_event_row` and the Arrow row-dict
    builders below draw from, so persisted columns and the semantic hash can never silently drift
    apart."""
    if isinstance(event, MarketTradeEvent):
        p = event.price.normalized()
        agg = event.aggressor
        return [
            event.identity_hash, str(event.event_ordinal), event.contract_id,
            str(p.mantissa), str(p.scale), str(event.quantity),
            agg.side.value, agg.basis.value,
            None if agg.confidence is None else repr(agg.confidence),
            event.quality_state.value,
        ] + _provenance_values(event.provenance)
    if isinstance(event, TopOfBookEvent):
        bp, ap = event.bid.price.normalized(), event.ask.price.normalized()
        return [
            event.identity_hash, str(event.event_ordinal), event.contract_id,
            str(bp.mantissa), str(bp.scale),
            None if event.bid.quantity is None else str(event.bid.quantity),
            None if event.bid.order_count is None else str(event.bid.order_count),
            str(ap.mantissa), str(ap.scale),
            None if event.ask.quantity is None else str(event.ask.quantity),
            None if event.ask.order_count is None else str(event.ask.order_count),
            event.quality_state.value,
        ] + _provenance_values(event.provenance)
    if isinstance(event, MarketQuoteEvent):
        bid = event.bid.normalized() if event.bid is not None else None
        ask = event.ask.normalized() if event.ask is not None else None
        return [
            event.identity_hash, str(event.event_ordinal), event.instrument_id,
            None if bid is None else str(bid.mantissa), None if bid is None else str(bid.scale),
            None if ask is None else str(ask.mantissa), None if ask is None else str(ask.scale),
            event.quality_state.value,
        ] + _provenance_values(event.provenance)
    raise PartitionError(f"unknown canonical event type: {type(event)!r}")


def event_family_name(event) -> str:
    if isinstance(event, MarketTradeEvent):
        return "market_trade"
    if isinstance(event, TopOfBookEvent):
        return "top_of_book"
    if isinstance(event, MarketQuoteEvent):
        return "market_quote"
    raise PartitionError(f"unknown canonical event type: {type(event)!r}")


def serialize_event_row(event) -> bytes:
    """The one deterministic semantic row serialization (WO §11): explicit field order (the
    `*_ROW_FIELDS` constant matching this event's family), length-prefixed encoding (never
    JSON-library default key ordering/formatting). This is the basis for `partition_content_sha256`
    and for the canonicaliser's duplicate/conflict detection (canonicaliser.py)."""
    return b"".join(encode_field(v) for v in _row_values(event))


def event_sort_key(event):
    """Deterministic row/event ordering key, shared by the partition writer and the replay
    comparison harness (replay.py) — never dependent on input iteration order."""
    seq = event.provenance.source_sequence
    return (
        event.provenance.source_event_time,
        seq if seq is not None else -1,
        event.event_ordinal,
        event.identity_hash,
    )


# ---------------------------------------------------------------------------
# §10 — deterministic logical partition layout
# ---------------------------------------------------------------------------

def _safe_segment(text: str) -> str:
    return "".join(c if (c.isalnum() or c in "-._") else "_" for c in text)


def partition_relative_dir(event) -> str:
    date_str = event.provenance.source_event_time.date().isoformat()
    family = event_family_name(event)
    if isinstance(event, (MarketTradeEvent, TopOfBookEvent)):
        # contract_id looks like "COMEX:GC:2026-12"
        venue, product, _delivery = event.contract_id.split(":", 2)
        return (
            f"schema=v1/venue={_safe_segment(venue)}/product={_safe_segment(product)}/"
            f"contract={_safe_segment(event.contract_id)}/date={date_str}/event_type={family}"
        )
    if isinstance(event, MarketQuoteEvent):
        classification = event.provenance.source_classification.value
        return (
            f"schema=v1/venue={_safe_segment(classification)}/"
            f"instrument={_safe_segment(event.instrument_id)}/date={date_str}/event_type={family}"
        )
    raise PartitionError(f"unknown canonical event type: {type(event)!r}")


# ---------------------------------------------------------------------------
# Arrow schemas (explicit column order == *_ROW_FIELDS; every value stored as string/int, never
# binary float, so a reload can reconstruct the exact original mantissa/scale/precision-text).
# ---------------------------------------------------------------------------

def _arrow_schema(fields: Sequence[str]) -> pa.Schema:
    int_fields = {
        "event_ordinal", "price_mantissa", "price_scale", "quantity",
        "bid_price_mantissa", "bid_price_scale", "bid_quantity", "bid_order_count",
        "ask_price_mantissa", "ask_price_scale", "ask_quantity", "ask_order_count",
        "source_sequence",
    }
    return pa.schema([pa.field(name, pa.int64() if name in int_fields else pa.string()) for name in fields])


TRADE_ARROW_SCHEMA = _arrow_schema(TRADE_ROW_FIELDS)
TOP_OF_BOOK_ARROW_SCHEMA = _arrow_schema(TOP_OF_BOOK_ROW_FIELDS)
QUOTE_ARROW_SCHEMA = _arrow_schema(QUOTE_ROW_FIELDS)

_SCHEMA_BY_FAMILY: Dict[str, pa.Schema] = {
    "market_trade": TRADE_ARROW_SCHEMA,
    "top_of_book": TOP_OF_BOOK_ARROW_SCHEMA,
    "market_quote": QUOTE_ARROW_SCHEMA,
}
_ROW_FIELDS_BY_FAMILY: Dict[str, Tuple[str, ...]] = {
    "market_trade": TRADE_ROW_FIELDS,
    "top_of_book": TOP_OF_BOOK_ROW_FIELDS,
    "market_quote": QUOTE_ROW_FIELDS,
}

_INT_FIELD_NAMES = {
    "event_ordinal", "price_mantissa", "price_scale", "quantity",
    "bid_price_mantissa", "bid_price_scale", "bid_quantity", "bid_order_count",
    "ask_price_mantissa", "ask_price_scale", "ask_quantity", "ask_order_count",
    "source_sequence",
}


def _row_dict(event) -> Dict[str, Optional[object]]:
    fields = _ROW_FIELDS_BY_FAMILY[event_family_name(event)]
    values = _row_values(event)
    out: Dict[str, Optional[object]] = {}
    for name, value in zip(fields, values):
        if value is None:
            out[name] = None
        elif name in _INT_FIELD_NAMES:
            out[name] = int(value)
        else:
            out[name] = value
    return out


@dataclass(frozen=True)
class PartitionWriteResult:
    relative_dir: str
    file_name: str
    event_family: str
    row_count: int
    partition_content_sha256: str
    artifact_sha256: str
    writer_library: str
    writer_version: str
    writer_config_id: str

    @property
    def relative_path(self) -> str:
        return f"{self.relative_dir}/{self.file_name}"


class PartitionWriter:
    """Writes canonical events into local, disposable Parquet+Zstd research partitions, one file
    per (partition key, event family) group, following the deterministic layout above."""

    FILE_NAME = "part-00000.parquet"
    WRITER_CONFIG_ID = "zstd-default-level-v1"

    def __init__(self, root: Path):
        self.root = Path(root)

    def write(self, events: Sequence[object]) -> List[PartitionWriteResult]:
        groups: Dict[Tuple[str, str], List[object]] = {}
        for event in events:
            key = (partition_relative_dir(event), event_family_name(event))
            groups.setdefault(key, []).append(event)

        results: List[PartitionWriteResult] = []
        for (relative_dir, family), group_events in sorted(groups.items(), key=lambda kv: kv[0]):
            ordered = sorted(group_events, key=event_sort_key)
            content_hash = hashlib.sha256()
            for event in ordered:
                row_bytes = serialize_event_row(event)
                content_hash.update(len(row_bytes).to_bytes(4, "big"))
                content_hash.update(row_bytes)

            schema = _SCHEMA_BY_FAMILY[family]
            rows = [_row_dict(event) for event in ordered]
            table = pa.Table.from_pylist(rows, schema=schema)

            out_dir = self.root / relative_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / self.FILE_NAME
            pq.write_table(table, out_path, compression=PARQUET_COMPRESSION)

            artifact_hash = hashlib.sha256(out_path.read_bytes()).hexdigest()
            results.append(
                PartitionWriteResult(
                    relative_dir=relative_dir,
                    file_name=self.FILE_NAME,
                    event_family=family,
                    row_count=len(ordered),
                    partition_content_sha256=content_hash.hexdigest(),
                    artifact_sha256=artifact_hash,
                    writer_library=PARQUET_WRITER_LIBRARY,
                    writer_version=pa.__version__,
                    writer_config_id=self.WRITER_CONFIG_ID,
                )
            )
        return results


class PartitionReader:
    """Reconstructs canonical events (not just raw rows) from a written research partition, for
    the "partition reload reconstructs identical canonical events" proof (WO acceptance #6)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def read_events(self, relative_dir: str, family: str) -> List[object]:
        path = self.root / relative_dir / PartitionWriter.FILE_NAME
        table = pq.read_table(path)
        rows = table.to_pylist()
        return [self._row_to_event(family, row) for row in rows]

    @staticmethod
    def _provenance_from_row(row: Dict[str, object]) -> ProvenanceEnvelope:
        source_sequence = row["source_sequence"]
        return ProvenanceEnvelope(
            provider_id=row["provider_id"],
            dataset_id=row["dataset_id"],
            source_classification=SourceClassification(row["source_classification"]),
            source_record_schema=row["source_record_schema"],
            raw_provider_symbol=row["raw_provider_symbol"],
            source_artifact_id=row["source_artifact_id"],
            source_event_time=parse_utc_text(row["source_event_time_text"]),
            source_event_time_text=row["source_event_time_text"],
            source_timestamp_precision=TimestampPrecision(row["source_timestamp_precision"]),
            provider_receive_time=(
                parse_utc_text(row["provider_receive_time_text"]) if row["provider_receive_time_text"] else None
            ),
            provider_receive_time_text=row["provider_receive_time_text"],
            provider_receive_quality=ProviderReceiptQuality(row["provider_receive_quality"]),
            hermes_receive_time=None,
            source_sequence=source_sequence,
            sequence_domain=row["sequence_domain"],
            sequence_availability=SequenceAvailability(row["sequence_availability"]),
            acquisition_epoch=row["acquisition_epoch"],
            canonicaliser_version=row["canonicaliser_version"],
            schema_version=row["schema_version"],
            historical_provenance_era=HistoricalProvenanceEra(row["historical_provenance_era"]),
        )

    @classmethod
    def _row_to_event(cls, family: str, row: Dict[str, object]):
        provenance = cls._provenance_from_row(row)
        quality_state = EventQualityState(row["quality_state"])

        if family == "market_trade":
            side = AggressorSide(row["aggressor_side"])
            basis = AggressorBasis(row["aggressor_basis"])
            confidence = None if row["aggressor_confidence"] is None else float(row["aggressor_confidence"])
            return MarketTradeEvent(
                contract_id=row["contract_id"],
                price=ExactPrice(mantissa=row["price_mantissa"], scale=row["price_scale"]),
                quantity=row["quantity"],
                aggressor=AggressorClassification(side=side, basis=basis, confidence=confidence),
                provenance=provenance,
                quality_state=quality_state,
                identity_hash=row["identity_hash"],
                event_ordinal=row["event_ordinal"],
            )
        if family == "top_of_book":
            bid = BookLevel(
                price=ExactPrice(mantissa=row["bid_price_mantissa"], scale=row["bid_price_scale"]),
                quantity=row["bid_quantity"],
                order_count=row["bid_order_count"],
            )
            ask = BookLevel(
                price=ExactPrice(mantissa=row["ask_price_mantissa"], scale=row["ask_price_scale"]),
                quantity=row["ask_quantity"],
                order_count=row["ask_order_count"],
            )
            return TopOfBookEvent(
                contract_id=row["contract_id"],
                bid=bid,
                ask=ask,
                provenance=provenance,
                quality_state=quality_state,
                identity_hash=row["identity_hash"],
                event_ordinal=row["event_ordinal"],
            )
        if family == "market_quote":
            bid = (
                None if row["bid_price_mantissa"] is None
                else ExactPrice(mantissa=row["bid_price_mantissa"], scale=row["bid_price_scale"])
            )
            ask = (
                None if row["ask_price_mantissa"] is None
                else ExactPrice(mantissa=row["ask_price_mantissa"], scale=row["ask_price_scale"])
            )
            return MarketQuoteEvent(
                instrument_id=row["instrument_id"],
                bid=bid,
                ask=ask,
                provenance=provenance,
                quality_state=quality_state,
                identity_hash=row["identity_hash"],
                event_ordinal=row["event_ordinal"],
            )
        raise PartitionError(f"unknown event family {family!r}")
