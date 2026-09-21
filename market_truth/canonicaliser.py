"""
market_truth.canonicaliser — provider record -> canonical event(s).

Implements the canonicalisation step of the HMT-1 pipeline: translates a provider-neutral
`RawSourceRecord` (provider.py) into one or more canonical events (contracts.py), using the
governed contract mapping (futures.py) and the one governed identity algorithm (identity.py).

Stateful, per-instance responsibilities this module owns (WO §8/§9):
  - Top-of-book TRANSITION discipline: tracks the last known bid/ask per contract and only ever
    emits a `TopOfBookEvent` when the book has genuinely changed (WO acceptance #12 — an unchanged
    BBO must never invent an event).
  - The trade-that-also-moves-the-book ordinal/subtype mechanism (WO §6/§8): a single TRADE record
    whose payload carries the resulting book state produces `event_ordinal=0` (the trade) and, only
    if that book state actually differs from the tracked prior state, a companion
    `event_ordinal=1` `TopOfBookEvent` for the same `source_artifact_id`.
  - Duplicate/conflict semantics (WO §9): identical event identity + identical canonical row bytes
    is a deterministic no-op (idempotent replay of the same observation, not re-emitted); identical
    identity + DIFFERENT row bytes fails closed.

One `Canonicaliser` instance is stateful across a single ordered replay of one governed input set —
matching `replay.py`'s use (a fresh instance per run, so run A and run B never share state).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple

from market_truth.contracts import (
    AggressorBasis,
    AggressorClassification,
    AggressorSide,
    BookLevel,
    ContractValidationError,
    EventFamily,
    ExactPrice,
    MARKET_EVENT_CONTRACT_SCHEMA_VERSION,
    MarketQuoteEvent,
    MarketTradeEvent,
    TopOfBookEvent,
    UNAVAILABLE_AGGRESSOR,
)
from market_truth.futures import ContractMappingError, ContractMappingTable
from market_truth.identity import EventIdentityMaterial, compute_event_identity
from market_truth.partition import serialize_event_row
from market_truth.provenance import ProvenanceEnvelope
from market_truth.provider import RawRecordType, RawSourceRecord

CANONICALISER_VERSION = "hmt1-canonicaliser-v1"


class CanonicalisationError(ValueError):
    """Raised on any structurally invalid raw record this canonicaliser cannot honestly translate."""


class DuplicateConflictError(CanonicalisationError):
    """Raised when the same governed event identity is presented twice with conflicting content
    (WO §9). Fail-closed: never silently picks one side."""


def _book_level_from_payload(d: Optional[Mapping]) -> Optional[BookLevel]:
    if d is None:
        return None
    return BookLevel(
        price=ExactPrice(mantissa=d["price_mantissa"], scale=d["price_scale"]),
        quantity=d.get("quantity"),
        order_count=d.get("order_count"),
    )


def _price_from_payload(d: Optional[Mapping]) -> Optional[ExactPrice]:
    if d is None:
        return None
    return ExactPrice(mantissa=d["price_mantissa"], scale=d["price_scale"])


def _aggressor_from_payload(payload: Mapping) -> AggressorClassification:
    side_text = payload.get("aggressor_side")
    if side_text is None:
        return UNAVAILABLE_AGGRESSOR
    return AggressorClassification(
        side=AggressorSide(side_text),
        basis=AggressorBasis(payload.get("aggressor_basis", AggressorBasis.NOT_AVAILABLE.value)),
        confidence=payload.get("aggressor_confidence"),
    )


def _books_equal(a: Tuple[BookLevel, BookLevel], b: Tuple[BookLevel, BookLevel]) -> bool:
    return (
        a[0].price == b[0].price and a[0].quantity == b[0].quantity and a[0].order_count == b[0].order_count
        and a[1].price == b[1].price and a[1].quantity == b[1].quantity and a[1].order_count == b[1].order_count
    )


@dataclass
class Canonicaliser:
    mapping_table: ContractMappingTable
    canonicaliser_version: str = CANONICALISER_VERSION
    schema_version: str = MARKET_EVENT_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self._book_state: Dict[str, Tuple[BookLevel, BookLevel]] = {}
        self._seen_rows: Dict[str, bytes] = {}

    # -- provenance/identity plumbing -------------------------------------------------

    def _make_provenance(self, record: RawSourceRecord) -> ProvenanceEnvelope:
        return ProvenanceEnvelope(
            provider_id=record.provider_id,
            dataset_id=record.dataset_id,
            source_classification=record.source_classification,
            source_record_schema=record.source_record_schema,
            raw_provider_symbol=record.raw_provider_symbol,
            source_artifact_id=record.source_artifact_id,
            source_event_time=record.source_event_time,
            source_event_time_text=record.source_event_time_text,
            source_timestamp_precision=record.source_timestamp_precision,
            provider_receive_time=record.provider_receive_time,
            provider_receive_time_text=record.provider_receive_time_text,
            provider_receive_quality=record.provider_receive_quality,
            hermes_receive_time=None,  # deterministic offline fixture replay — no genuine ingestion moment
            source_sequence=record.source_sequence,
            sequence_domain=record.sequence_domain,
            sequence_availability=record.sequence_availability,
            acquisition_epoch=record.acquisition_epoch,
            canonicaliser_version=self.canonicaliser_version,
            schema_version=self.schema_version,
            historical_provenance_era=record.historical_provenance_era,
        )

    def _identity_hash(self, record: RawSourceRecord, family: EventFamily, canonical_instrument_id: str, ordinal: int) -> str:
        material = EventIdentityMaterial(
            event_family=family.value,
            provider_id=record.provider_id,
            dataset_id=record.dataset_id,
            source_artifact_id=record.source_artifact_id,
            canonical_instrument_id=canonical_instrument_id,
            source_record_identity=record.source_artifact_id,
            source_sequence=record.source_sequence,
            sequence_domain=record.sequence_domain,
            source_event_time_text=record.source_event_time_text,
            event_ordinal=ordinal,
        )
        return compute_event_identity(material)

    def _register(self, event: object) -> Optional[object]:
        """Duplicate/conflict rule (WO §9). Returns the event if it is new, `None` if it is an
        exact (deterministically deduplicated) repeat, and raises `DuplicateConflictError` if the
        same identity now carries different content."""
        row_bytes = serialize_event_row(event)
        prior = self._seen_rows.get(event.identity_hash)
        if prior is None:
            self._seen_rows[event.identity_hash] = row_bytes
            return event
        if prior == row_bytes:
            return None
        raise DuplicateConflictError(
            f"identity {event.identity_hash} presented twice with conflicting content"
        )

    # -- per record-type canonicalisation -----------------------------------------------

    def canonicalise(self, record: RawSourceRecord) -> Tuple[object, ...]:
        try:
            if record.record_type is RawRecordType.TRADE:
                return self._canonicalise_trade(record)
            if record.record_type is RawRecordType.TOP_OF_BOOK_UPDATE:
                return self._canonicalise_top_of_book(record)
            if record.record_type is RawRecordType.QUOTE:
                return self._canonicalise_quote(record)
        except (ContractValidationError, ContractMappingError) as exc:
            raise CanonicalisationError(str(exc)) from exc
        raise CanonicalisationError(f"unknown record_type {record.record_type!r}")

    def _canonicalise_trade(self, record: RawSourceRecord) -> Tuple[object, ...]:
        payload = record.payload
        contract = self.mapping_table.resolve(record.raw_provider_symbol)
        contract_id = contract.canonical_id()
        provenance = self._make_provenance(record)

        price = ExactPrice(mantissa=payload["price_mantissa"], scale=payload["price_scale"])
        trade = MarketTradeEvent(
            contract_id=contract_id,
            price=price,
            quantity=payload["quantity"],
            aggressor=_aggressor_from_payload(payload),
            provenance=provenance,
            quality_state=record.quality_state,
            identity_hash=self._identity_hash(record, EventFamily.MARKET_TRADE, contract_id, ordinal=0),
            event_ordinal=0,
        )
        events = []
        registered_trade = self._register(trade)
        if registered_trade is not None:
            events.append(registered_trade)

        book_after = payload.get("book_after")
        if book_after is not None:
            new_bid = _book_level_from_payload(book_after["bid"])
            new_ask = _book_level_from_payload(book_after["ask"])
            prior_state = self._book_state.get(contract_id)
            if prior_state is None or not _books_equal(prior_state, (new_bid, new_ask)):
                tob = TopOfBookEvent(
                    contract_id=contract_id,
                    bid=new_bid,
                    ask=new_ask,
                    provenance=provenance,
                    quality_state=record.quality_state,
                    identity_hash=self._identity_hash(record, EventFamily.TOP_OF_BOOK, contract_id, ordinal=1),
                    event_ordinal=1,
                )
                self._book_state[contract_id] = (new_bid, new_ask)
                registered_tob = self._register(tob)
                if registered_tob is not None:
                    events.append(registered_tob)
        return tuple(events)

    def _canonicalise_top_of_book(self, record: RawSourceRecord) -> Tuple[object, ...]:
        payload = record.payload
        contract = self.mapping_table.resolve(record.raw_provider_symbol)
        contract_id = contract.canonical_id()

        new_bid = _book_level_from_payload(payload["bid"])
        new_ask = _book_level_from_payload(payload["ask"])
        prior_state = self._book_state.get(contract_id)
        if prior_state is not None and _books_equal(prior_state, (new_bid, new_ask)):
            # Genuinely unchanged BBO — never invent a TopOfBookEvent (WO acceptance #12).
            return tuple()

        provenance = self._make_provenance(record)
        tob = TopOfBookEvent(
            contract_id=contract_id,
            bid=new_bid,
            ask=new_ask,
            provenance=provenance,
            quality_state=record.quality_state,
            identity_hash=self._identity_hash(record, EventFamily.TOP_OF_BOOK, contract_id, ordinal=0),
            event_ordinal=0,
        )
        self._book_state[contract_id] = (new_bid, new_ask)
        registered = self._register(tob)
        return (registered,) if registered is not None else tuple()

    def _canonicalise_quote(self, record: RawSourceRecord) -> Tuple[object, ...]:
        payload = record.payload
        instrument_id = payload["instrument_id"]
        provenance = self._make_provenance(record)
        quote = MarketQuoteEvent(
            instrument_id=instrument_id,
            bid=_price_from_payload(payload.get("bid")),
            ask=_price_from_payload(payload.get("ask")),
            provenance=provenance,
            quality_state=record.quality_state,
            identity_hash=self._identity_hash(record, EventFamily.MARKET_QUOTE, instrument_id, ordinal=0),
            event_ordinal=0,
        )
        registered = self._register(quote)
        return (registered,) if registered is not None else tuple()
