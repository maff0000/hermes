"""
market_truth.provenance — the common provenance envelope every canonical event carries.

Implements docs/architecture/hmt0-market-truth-v2/time-order-sequence-model.md and
docs/architecture/hmt0-market-truth-v2/source-taxonomy.md: several genuinely distinct clocks and
identities that must never be collapsed into one field, and a source classification that is
permanent, immutable metadata — never inferred at query time, never defaulted.

Deliberate exclusion from event identity
-----------------------------------------
`hermes_receive_time` is a fact about HERMES's own ingestion process (always available in genuine
live ingestion). It is deliberately excluded from `identity.py`'s identity material — a wall-clock
field can never participate in a deterministic identity/hash, or replay run A and run B would never
agree. This module still carries the field (so a genuine live adapter has somewhere honest to put
it); the fixture provider used throughout HMT-1 sets it to `None` because a deterministic offline
replay of governed fixture bytes has no genuine "ingestion moment" to record (see providers/fixture.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class ProvenanceValidationError(ValueError):
    """Raised when a provenance envelope is constructed with invalid/inconsistent data."""


def parse_utc_text(text: str) -> datetime:
    """Parse a verbatim ISO-8601 UTC timestamp string into a timezone-aware `datetime`, accepting
    a trailing `Z`. Shared by every place in this package that needs the parsed form for
    ordering/partitioning while keeping the original text as the identity/row-serialization value
    (never re-deriving a string from the parsed datetime, which could invent precision — see this
    module's docstring)."""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProvenanceValidationError(f"timestamp {text!r} is not a valid ISO-8601 UTC text") from exc
    if dt.tzinfo is None:
        raise ProvenanceValidationError(f"timestamp {text!r} is not timezone-aware")
    return dt


class SourceClassification(Enum):
    """Permanent, immutable source classification (source-taxonomy.md §1/§3)."""

    RETAIL_CFD_STREAMING_QUOTE = "RETAIL_CFD_STREAMING_QUOTE"
    BROKER_OTC_EXECUTABLE_QUOTE_OBSERVATION = "BROKER_OTC_EXECUTABLE_QUOTE_OBSERVATION"
    CENTRALISED_EXCHANGE_FUTURES_GENUINE_ORDER_FLOW = "CENTRALISED_EXCHANGE_FUTURES_GENUINE_ORDER_FLOW"


class TimestampPrecision(Enum):
    """Native source timestamp resolution. Physical storage resolution must never upgrade this —
    a millisecond source timestamp stored in a field capable of nanoseconds is still tagged
    MILLISECOND; no zero-padding masquerades as finer precision (time-order-sequence-model.md §1
    item 4)."""

    SECOND = "SECOND"
    MILLISECOND = "MILLISECOND"
    MICROSECOND = "MICROSECOND"
    NANOSECOND = "NANOSECOND"
    UNKNOWN = "UNKNOWN"


class ProviderReceiptQuality(Enum):
    """Genuineness of `provider_receive_time` (time-order-sequence-model.md §1 item 2 / §3)."""

    GENUINE = "GENUINE"
    SYNTHETIC_EQUATED_TO_EVENT_TIME = "SYNTHETIC_EQUATED_TO_EVENT_TIME"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class SequenceAvailability(Enum):
    AVAILABLE = "AVAILABLE"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class HistoricalProvenanceEra(Enum):
    """The three GC provenance eras ratified in time-order-sequence-model.md §3. `NOT_APPLICABLE`
    is for event families this era model does not apply to (e.g. broker/OTC quotes)."""

    ERA_PRE_2015_11_20_LEGACY = "PRE_2015_11_20_LEGACY"
    ERA_2015_11_20_TO_2017_05_20_LEGACY = "2015_11_20_TO_2017_05_20_LEGACY"
    ERA_MDP3_FROM_2017_05_21 = "MDP3_FROM_2017_05_21"
    ERA_NOT_APPLICABLE = "NOT_APPLICABLE"


# Sequence-domain tag constants (time-order-sequence-model.md §2): a sequence number is meaningless
# without knowing whose counting scheme produced it. HERMES's existing legacy `ticks.seq` stored-row
# counter must never be reused for exchange-ordering purposes — that domain is deliberately not a
# constant offered by this module at all, so nothing here can accidentally be tagged with it.
SEQUENCE_DOMAIN_NOT_AVAILABLE = "NOT_AVAILABLE"
SEQUENCE_DOMAIN_HERMES_RECEIVE_ORDER_APPROXIMATION = "HERMES_RECEIVE_ORDER_APPROXIMATION"


@dataclass(frozen=True)
class ProvenanceEnvelope:
    """The common provenance envelope (time-order-sequence-model.md §1) every canonical event
    carries. Every field below is kept permanently distinct — no field may silently substitute for
    another.
    """

    provider_id: str
    dataset_id: str
    source_classification: SourceClassification
    source_record_schema: str
    raw_provider_symbol: str
    source_artifact_id: str

    # Market/exchange event time (§1 item 1) — market truth. `source_event_time` is a parsed,
    # timezone-aware UTC datetime used for ordering/partitioning; `source_event_time_text` is the
    # VERBATIM text the source provided, used for identity/row serialization so re-parsing can
    # never invent precision the source did not genuinely have.
    source_event_time: datetime
    source_event_time_text: str
    source_timestamp_precision: TimestampPrecision

    # Provider receive/capture time (§1 item 2) — only where genuinely exposed by the provider.
    provider_receive_time: Optional[datetime]
    provider_receive_time_text: Optional[str]
    provider_receive_quality: ProviderReceiptQuality

    # HERMES receive time (§1 item 3) — deliberately excluded from identity; see module docstring.
    hermes_receive_time: Optional[datetime]

    # Sequence source/domain (§1 item 5).
    source_sequence: Optional[int]
    sequence_domain: str
    sequence_availability: SequenceAvailability

    # Connection/acquisition epoch (§1 item 6).
    acquisition_epoch: str

    # Governed versions (never included in event identity — see identity.py).
    canonicaliser_version: str
    schema_version: str

    # Historical provenance era (source-taxonomy.md §1.3 / time-order-sequence-model.md §3).
    historical_provenance_era: HistoricalProvenanceEra

    def __post_init__(self) -> None:
        for name in ("provider_id", "dataset_id", "source_record_schema", "raw_provider_symbol",
                     "source_artifact_id", "source_event_time_text", "acquisition_epoch",
                     "canonicaliser_version", "schema_version"):
            if not getattr(self, name):
                raise ProvenanceValidationError(f"ProvenanceEnvelope.{name} must be non-empty")

        if self.source_event_time.tzinfo is None or self.source_event_time.utcoffset() != timezone.utc.utcoffset(None):
            raise ProvenanceValidationError("source_event_time must be a UTC-aware datetime")
        if self.provider_receive_time is not None and (
            self.provider_receive_time.tzinfo is None
            or self.provider_receive_time.utcoffset() != timezone.utc.utcoffset(None)
        ):
            raise ProvenanceValidationError("provider_receive_time must be UTC-aware when present")
        if self.hermes_receive_time is not None and (
            self.hermes_receive_time.tzinfo is None
            or self.hermes_receive_time.utcoffset() != timezone.utc.utcoffset(None)
        ):
            raise ProvenanceValidationError("hermes_receive_time must be UTC-aware when present")

        # Provider-receive genuineness must agree with whether a receive time/text is even present.
        if self.provider_receive_quality is ProviderReceiptQuality.NOT_AVAILABLE:
            if self.provider_receive_time is not None or self.provider_receive_time_text is not None:
                raise ProvenanceValidationError(
                    "provider_receive_quality=NOT_AVAILABLE must not carry a receive time (never fabricated)"
                )
        else:
            if self.provider_receive_time is None or not self.provider_receive_time_text:
                raise ProvenanceValidationError(
                    f"provider_receive_quality={self.provider_receive_quality} requires a receive time/text"
                )

        # Sequence availability must agree with whether a sequence number/domain is present.
        if self.sequence_availability is SequenceAvailability.NOT_AVAILABLE:
            if self.source_sequence is not None:
                raise ProvenanceValidationError("sequence_availability=NOT_AVAILABLE must not carry a source_sequence")
            if self.sequence_domain != SEQUENCE_DOMAIN_NOT_AVAILABLE:
                raise ProvenanceValidationError("sequence_availability=NOT_AVAILABLE requires sequence_domain=NOT_AVAILABLE")
        else:
            if self.source_sequence is None:
                raise ProvenanceValidationError("sequence_availability=AVAILABLE requires a source_sequence")
            if not self.sequence_domain or self.sequence_domain == SEQUENCE_DOMAIN_NOT_AVAILABLE:
                raise ProvenanceValidationError("sequence_availability=AVAILABLE requires a genuine, named sequence_domain")
