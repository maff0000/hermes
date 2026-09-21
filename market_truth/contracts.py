"""
market_truth.contracts — canonical, provider-neutral market event contracts.

Implements docs/architecture/hmt0-market-truth-v2/canonical-market-events.md: three event
families (`MarketTradeEvent`, `TopOfBookEvent`, `MarketQuoteEvent`), plus the supporting enums
and the exact fixed-point numeric type every price field must use.

Contract/schema versioning
---------------------------
`MARKET_EVENT_CONTRACT_SCHEMA_VERSION` is this module's own record-shape identity (field names /
types / meaning). It is recorded on every event's provenance envelope (see `provenance.py`) and on
the evidence manifest (see `evidence.py`) — a schema change is a new version, never a silent
reinterpretation of the old one. It does NOT participate in event identity (see `identity.py`):
identity is about *what the event is*, schema version is provenance about *how it is shaped*.

Every dataclass below is frozen (immutable) and fails closed in `__post_init__` — invalid
construction raises `ContractValidationError` rather than silently accepting bad data. See
docs/contracts/hmt1-market-event-contract-v1.md for the human-readable version of this contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional

from market_truth.provenance import ProvenanceEnvelope, SourceClassification

MARKET_EVENT_CONTRACT_SCHEMA_VERSION = "hmt1-market-event-contract-v1"


class ContractValidationError(ValueError):
    """Raised when a canonical contract is constructed with invalid/inconsistent data.

    Fail-closed: this is always raised, never silently corrected or approximated.
    """


class EventFamily(Enum):
    MARKET_TRADE = "MARKET_TRADE"
    TOP_OF_BOOK = "TOP_OF_BOOK"
    MARKET_QUOTE = "MARKET_QUOTE"


class EventQualityState(Enum):
    """Quality/completeness state of a canonical event.

    `BAD_RECEIVE_TIME` corresponds to the documented `F_BAD_TS_RECV` condition for the two legacy
    GC provenance eras (time-order-sequence-model.md §3): the provider-receive timestamp is a
    synthetic equation to event time, not a genuine independent capture measurement, and this flag
    must propagate into every derived fact that consumes the event (never silently dropped).
    """

    OK = "OK"
    BAD_RECEIVE_TIME = "BAD_RECEIVE_TIME"
    DEGRADED_SOURCE = "DEGRADED_SOURCE"
    UNCLASSIFIED = "UNCLASSIFIED"


class AggressorSide(Enum):
    BUY = "BUY"
    SELL = "SELL"
    UNKNOWN = "UNKNOWN"


class AggressorBasis(Enum):
    """How an `AggressorSide` was derived. Never left implicit — see canonical-market-events.md:
    aggressor side must carry its own basis/provenance/confidence, and must never be fabricated
    where the source cannot genuinely support it."""

    BBO_RELATIVE_CLASSIFICATION = "BBO_RELATIVE_CLASSIFICATION"
    NOT_AVAILABLE = "NOT_AVAILABLE"


@dataclass(frozen=True)
class ExactPrice:
    """A price represented as an exact fixed-point value: `mantissa * 10**(-scale)`.

    Canonical price identity must NEVER be binary floating point (WO §2). Two textual
    representations of the same logical price (e.g. "2387.40" and "2387.400") must normalise to
    an identical value, and therefore hash identically wherever price participates in identity
    material — see `normalized()` below and `test_exact_numeric.py`.
    """

    mantissa: int
    scale: int

    def __post_init__(self) -> None:
        if not isinstance(self.mantissa, int) or isinstance(self.mantissa, bool):
            raise ContractValidationError("ExactPrice.mantissa must be a plain int")
        if not isinstance(self.scale, int) or isinstance(self.scale, bool):
            raise ContractValidationError("ExactPrice.scale must be a plain int")
        if self.scale < 0:
            raise ContractValidationError("ExactPrice.scale must be >= 0")

    def normalized(self) -> "ExactPrice":
        """Canonical form: trailing-zero mantissa digits stripped down to the coarsest scale that
        represents the same exact value. `238740/scale2` and `2387400/scale3` both normalise to
        `238740/scale2`, giving identical equality/hash/serialization regardless of which textual
        precision the source happened to use."""
        mantissa, scale = self.mantissa, self.scale
        while scale > 0 and mantissa % 10 == 0:
            mantissa //= 10
            scale -= 1
        return ExactPrice(mantissa=mantissa, scale=scale)

    def identity_text(self) -> str:
        """Deterministic textual form used in identity/row serialization — always the normalised
        mantissa/scale pair, never a locale- or float-dependent rendering."""
        n = self.normalized()
        return f"{n.mantissa}:{n.scale}"

    def to_decimal(self) -> Decimal:
        return Decimal(self.mantissa).scaleb(-self.scale)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ExactPrice):
            return NotImplemented
        a, b = self.normalized(), other.normalized()
        return a.mantissa == b.mantissa and a.scale == b.scale

    def __hash__(self) -> int:
        n = self.normalized()
        return hash((n.mantissa, n.scale))

    @classmethod
    def from_decimal_string(cls, text: str) -> "ExactPrice":
        """Parse a decimal-literal string (e.g. "2387.40") into an exact mantissa/scale pair.
        Never routes through binary float. Fails closed on anything not a clean decimal literal."""
        try:
            d = Decimal(text)
        except InvalidOperation as exc:
            raise ContractValidationError(f"not a valid decimal literal: {text!r}") from exc
        sign, digits, exponent = d.as_tuple()
        if exponent > 0:
            # e.g. Decimal("1E+2") — not a plain decimal literal this parser accepts.
            raise ContractValidationError(f"non-fractional exponent not accepted: {text!r}")
        mantissa = int("".join(map(str, digits)))
        if sign:
            mantissa = -mantissa
        return cls(mantissa=mantissa, scale=-exponent)


@dataclass(frozen=True)
class AggressorClassification:
    """Aggressor-side classification for a `MarketTradeEvent`, with its own basis/confidence.

    Fails closed: a genuine side (`BUY`/`SELL`) requires a genuine basis and is never paired with
    `NOT_AVAILABLE`; `UNKNOWN` must be paired with `NOT_AVAILABLE` and no confidence. This makes it
    structurally impossible to fabricate an aggressor side the source does not support.
    """

    side: AggressorSide
    basis: AggressorBasis
    confidence: Optional[float] = None

    def __post_init__(self) -> None:
        if self.side is AggressorSide.UNKNOWN:
            if self.basis is not AggressorBasis.NOT_AVAILABLE:
                raise ContractValidationError("UNKNOWN aggressor side must carry basis=NOT_AVAILABLE")
            if self.confidence is not None:
                raise ContractValidationError("UNKNOWN aggressor side must not carry a confidence value")
        else:
            if self.basis is AggressorBasis.NOT_AVAILABLE:
                raise ContractValidationError(
                    f"genuine aggressor side {self.side} must not carry basis=NOT_AVAILABLE "
                    "(never fabricate a side the source cannot support)"
                )
            if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
                raise ContractValidationError("aggressor confidence must be within [0.0, 1.0]")


UNAVAILABLE_AGGRESSOR = AggressorClassification(side=AggressorSide.UNKNOWN, basis=AggressorBasis.NOT_AVAILABLE)


@dataclass(frozen=True)
class MarketTradeEvent:
    """A genuine centralized-market transaction (canonical-market-events.md §1).

    `contract_id` is the durable, provider-neutral instrument identity string (for GC, the value
    of `GcContractIdentity.canonical_id()` — never a bare root symbol). `identity_hash` is computed
    exclusively by `identity.py` and passed in already-computed; this module never computes its own
    identity so there is exactly one governed algorithm in the whole package.
    """

    contract_id: str
    price: ExactPrice
    quantity: int
    aggressor: AggressorClassification
    provenance: ProvenanceEnvelope
    quality_state: EventQualityState
    identity_hash: str
    event_ordinal: int = 0
    event_family: EventFamily = field(default=EventFamily.MARKET_TRADE, init=False)

    def __post_init__(self) -> None:
        if not self.contract_id:
            raise ContractValidationError("MarketTradeEvent.contract_id must be non-empty")
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool) or self.quantity <= 0:
            raise ContractValidationError("MarketTradeEvent.quantity must be a positive int (discrete contracts)")
        if self.provenance.source_classification is not SourceClassification.CENTRALISED_EXCHANGE_FUTURES_GENUINE_ORDER_FLOW:
            raise ContractValidationError(
                "MarketTradeEvent requires CENTRALISED_EXCHANGE_FUTURES_GENUINE_ORDER_FLOW source classification"
            )
        if not self.identity_hash:
            raise ContractValidationError("MarketTradeEvent.identity_hash must be non-empty")
        if self.event_ordinal < 0:
            raise ContractValidationError("event_ordinal must be >= 0")


@dataclass(frozen=True)
class BookLevel:
    """One side (bid or ask) of a top-of-book state. Size/order-count are `None` when the source
    genuinely does not provide them — never defaulted to a plausible-looking number."""

    price: ExactPrice
    quantity: Optional[int] = None
    order_count: Optional[int] = None

    def __post_init__(self) -> None:
        if self.quantity is not None and (not isinstance(self.quantity, int) or self.quantity < 0):
            raise ContractValidationError("BookLevel.quantity must be a non-negative int or None")
        if self.order_count is not None and (not isinstance(self.order_count, int) or self.order_count < 0):
            raise ContractValidationError("BookLevel.order_count must be a non-negative int or None")


@dataclass(frozen=True)
class TopOfBookEvent:
    """A genuine top-of-book *state transition* (canonical-market-events.md §1/§2) — never a
    snapshot glued to a trade. The canonicaliser (canonicaliser.py) is responsible for only ever
    constructing one of these when the book state has genuinely changed; this contract only
    validates the state itself, not the transition discipline (that is a canonicaliser-level, not a
    contract-level, invariant — see test_canonicaliser.py::test_unchanged_bbo_emits_nothing)."""

    contract_id: str
    bid: BookLevel
    ask: BookLevel
    provenance: ProvenanceEnvelope
    quality_state: EventQualityState
    identity_hash: str
    event_ordinal: int = 0
    event_family: EventFamily = field(default=EventFamily.TOP_OF_BOOK, init=False)

    def __post_init__(self) -> None:
        if not self.contract_id:
            raise ContractValidationError("TopOfBookEvent.contract_id must be non-empty")
        if self.provenance.source_classification is not SourceClassification.CENTRALISED_EXCHANGE_FUTURES_GENUINE_ORDER_FLOW:
            raise ContractValidationError(
                "TopOfBookEvent requires CENTRALISED_EXCHANGE_FUTURES_GENUINE_ORDER_FLOW source classification"
            )
        if self.ask.price.to_decimal() < self.bid.price.to_decimal():
            raise ContractValidationError(
                f"crossed book is invalid: ask {self.ask.price.to_decimal()} < bid {self.bid.price.to_decimal()}"
            )
        if not self.identity_hash:
            raise ContractValidationError("TopOfBookEvent.identity_hash must be non-empty")
        if self.event_ordinal < 0:
            raise ContractValidationError("event_ordinal must be >= 0")


@dataclass(frozen=True)
class MarketQuoteEvent:
    """A provider-neutral broker/OTC quote observation (canonical-market-events.md §1).

    Deliberately simpler than the other two event families: it carries no aggression/trade
    semantics and must never acquire them merely by sharing the common provenance envelope. The
    `__post_init__` guard below is the structural enforcement of that separation.
    """

    instrument_id: str
    bid: Optional[ExactPrice]
    ask: Optional[ExactPrice]
    provenance: ProvenanceEnvelope
    quality_state: EventQualityState
    identity_hash: str
    event_ordinal: int = 0
    event_family: EventFamily = field(default=EventFamily.MARKET_QUOTE, init=False)

    _ALLOWED_SOURCES = (
        SourceClassification.RETAIL_CFD_STREAMING_QUOTE,
        SourceClassification.BROKER_OTC_EXECUTABLE_QUOTE_OBSERVATION,
    )

    def __post_init__(self) -> None:
        if not self.instrument_id:
            raise ContractValidationError("MarketQuoteEvent.instrument_id must be non-empty")
        if self.bid is None and self.ask is None:
            raise ContractValidationError("MarketQuoteEvent must carry at least one of bid/ask")
        if self.provenance.source_classification not in self._ALLOWED_SOURCES:
            raise ContractValidationError(
                "MarketQuoteEvent must not carry centralized-exchange trade/aggression semantics; "
                f"got source_classification={self.provenance.source_classification}"
            )
        if not self.identity_hash:
            raise ContractValidationError("MarketQuoteEvent.identity_hash must be non-empty")
        if self.event_ordinal < 0:
            raise ContractValidationError("event_ordinal must be >= 0")
