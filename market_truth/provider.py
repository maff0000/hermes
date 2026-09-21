"""
market_truth.provider — capability-based provider abstraction.

Implements docs/architecture/hmt0-market-truth-v2/provider-abstraction.md §1: canonical logic never
reads a provider's native wire format directly, and never depends on a vendor SDK's own object
types. `RawSourceRecord` is the one provider-neutral raw-record shape every adapter must translate
its own wire format into; `MarketDataProvider` names CAPABILITIES (what an adapter can supply), not
vendor classes — nothing here is named after, or imports, any specific vendor's SDK.

HMT-1 implements exactly one concrete provider: `providers.fixture.FixtureMarketDataProvider`. No
live network connection, no credential, and no vendor dependency exists anywhere in this module or
in that implementation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import FrozenSet, Iterator, Mapping, Optional

from market_truth.contracts import EventQualityState
from market_truth.provenance import (
    HistoricalProvenanceEra,
    ProviderReceiptQuality,
    SequenceAvailability,
    SourceClassification,
    TimestampPrecision,
)


class ProviderCapability(Enum):
    """Minimum capabilities named in provider-abstraction.md — never a vendor-specific class."""

    TRADE_EVENTS = "TRADE_EVENTS"
    TOP_OF_BOOK_TRANSITIONS = "TOP_OF_BOOK_TRANSITIONS"
    SOURCE_SEQUENCE = "SOURCE_SEQUENCE"
    PROVIDER_RECEIVE_TIMESTAMP = "PROVIDER_RECEIVE_TIMESTAMP"
    DEFINITIONS_SYMBOLOGY = "DEFINITIONS_SYMBOLOGY"
    HISTORICAL_RANGE_FIXTURE_INPUT = "HISTORICAL_RANGE_FIXTURE_INPUT"
    QUALITY_CONDITION_METADATA = "QUALITY_CONDITION_METADATA"


class RawRecordType(Enum):
    """The provider-neutral raw record kinds this package's canonicaliser understands. A record's
    kind, not its vendor origin, decides how it canonicalises."""

    TRADE = "TRADE"
    TOP_OF_BOOK_UPDATE = "TOP_OF_BOOK_UPDATE"
    QUOTE = "QUOTE"


@dataclass(frozen=True)
class RawSourceRecord:
    """The one provider-neutral raw record shape every adapter must translate its own wire format
    into. Never a vendor SDK object; canonical code (canonicaliser.py) consumes only this shape.

    `source_event_time` is a parsed, UTC-aware datetime (for ordering/partitioning);
    `source_event_time_text` is the source's own verbatim timestamp text (for identity/row
    serialization — see provenance.py module docstring on precision honesty).
    """

    record_type: RawRecordType
    provider_id: str
    dataset_id: str
    source_classification: SourceClassification
    source_record_schema: str
    source_artifact_id: str
    raw_provider_symbol: str

    source_event_time: datetime
    source_event_time_text: str
    source_timestamp_precision: TimestampPrecision

    provider_receive_time: Optional[datetime]
    provider_receive_time_text: Optional[str]
    provider_receive_quality: ProviderReceiptQuality

    source_sequence: Optional[int]
    sequence_domain: str
    sequence_availability: SequenceAvailability

    acquisition_epoch: str
    historical_provenance_era: HistoricalProvenanceEra
    quality_state: EventQualityState

    payload: Mapping[str, object]


class MarketDataProvider(ABC):
    """A capability-based provider. Concrete adapters declare which capabilities they genuinely
    support and never claim one they cannot honestly satisfy."""

    def __init__(self, provider_id: str, dataset_id: str, capabilities: FrozenSet[ProviderCapability]):
        self.provider_id = provider_id
        self.dataset_id = dataset_id
        self.capabilities = capabilities

    def supports(self, capability: ProviderCapability) -> bool:
        return capability in self.capabilities

    @abstractmethod
    def iter_records(self) -> Iterator[RawSourceRecord]:
        """Yield `RawSourceRecord`s in the provider's own deterministic native order."""
        raise NotImplementedError
