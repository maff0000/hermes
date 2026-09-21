"""HMT-1 — contracts.py: fail-closed construction + the three event families' shape rules."""
import pytest

from market_truth.contracts import (
    AggressorBasis,
    AggressorClassification,
    AggressorSide,
    BookLevel,
    ContractValidationError,
    EventFamily,
    EventQualityState,
    ExactPrice,
    MarketQuoteEvent,
    MarketTradeEvent,
    TopOfBookEvent,
    UNAVAILABLE_AGGRESSOR,
)
from market_truth.provenance import (
    ProviderReceiptQuality,
    ProvenanceEnvelope,
    SequenceAvailability,
    HistoricalProvenanceEra,
    SourceClassification,
    TimestampPrecision,
    SEQUENCE_DOMAIN_NOT_AVAILABLE,
)
from datetime import datetime, timezone


def _provenance(source_classification, **overrides):
    base = dict(
        provider_id="fixture-provider:hmt1-fixture-provider-v1",
        dataset_id="hmt1-test-dataset",
        source_classification=source_classification,
        source_record_schema="hmt1-fixture-line-schema-v1",
        raw_provider_symbol="GCZ26",
        source_artifact_id="artifact-0001",
        source_event_time=datetime(2026, 9, 15, 13, 30, tzinfo=timezone.utc),
        source_event_time_text="2026-09-15T13:30:00Z",
        source_timestamp_precision=TimestampPrecision.MILLISECOND,
        provider_receive_time=None,
        provider_receive_time_text=None,
        provider_receive_quality=ProviderReceiptQuality.NOT_AVAILABLE,
        hermes_receive_time=None,
        source_sequence=None,
        sequence_domain=SEQUENCE_DOMAIN_NOT_AVAILABLE,
        sequence_availability=SequenceAvailability.NOT_AVAILABLE,
        acquisition_epoch="hmt1-test-epoch",
        canonicaliser_version="hmt1-canonicaliser-v1",
        schema_version="hmt1-market-event-contract-v1",
        historical_provenance_era=HistoricalProvenanceEra.ERA_MDP3_FROM_2017_05_21,
    )
    base.update(overrides)
    return ProvenanceEnvelope(**base)


GC_PROVENANCE = _provenance(SourceClassification.CENTRALISED_EXCHANGE_FUTURES_GENUINE_ORDER_FLOW)


def test_market_trade_event_valid_construction():
    trade = MarketTradeEvent(
        contract_id="COMEX:GC:2026-12",
        price=ExactPrice(mantissa=238750, scale=2),
        quantity=3,
        aggressor=AggressorClassification(side=AggressorSide.BUY, basis=AggressorBasis.BBO_RELATIVE_CLASSIFICATION, confidence=0.9),
        provenance=GC_PROVENANCE,
        quality_state=EventQualityState.OK,
        identity_hash="a" * 64,
    )
    assert trade.event_family is EventFamily.MARKET_TRADE
    assert trade.event_ordinal == 0


def test_market_trade_event_rejects_non_positive_quantity():
    with pytest.raises(ContractValidationError):
        MarketTradeEvent(
            contract_id="COMEX:GC:2026-12",
            price=ExactPrice(mantissa=238750, scale=2),
            quantity=0,
            aggressor=UNAVAILABLE_AGGRESSOR,
            provenance=GC_PROVENANCE,
            quality_state=EventQualityState.OK,
            identity_hash="a" * 64,
        )


def test_market_trade_event_requires_centralised_exchange_source():
    otc_provenance = _provenance(SourceClassification.BROKER_OTC_EXECUTABLE_QUOTE_OBSERVATION)
    with pytest.raises(ContractValidationError):
        MarketTradeEvent(
            contract_id="COMEX:GC:2026-12",
            price=ExactPrice(mantissa=238750, scale=2),
            quantity=1,
            aggressor=UNAVAILABLE_AGGRESSOR,
            provenance=otc_provenance,
            quality_state=EventQualityState.OK,
            identity_hash="a" * 64,
        )


def test_aggressor_classification_fails_closed_on_fabricated_side():
    with pytest.raises(ContractValidationError):
        AggressorClassification(side=AggressorSide.BUY, basis=AggressorBasis.NOT_AVAILABLE)


def test_aggressor_classification_fails_closed_on_unknown_with_confidence():
    with pytest.raises(ContractValidationError):
        AggressorClassification(side=AggressorSide.UNKNOWN, basis=AggressorBasis.NOT_AVAILABLE, confidence=0.5)


def test_top_of_book_event_rejects_crossed_book():
    with pytest.raises(ContractValidationError):
        TopOfBookEvent(
            contract_id="COMEX:GC:2026-12",
            bid=BookLevel(price=ExactPrice(mantissa=238800, scale=2)),
            ask=BookLevel(price=ExactPrice(mantissa=238700, scale=2)),  # ask < bid: crossed, invalid
            provenance=GC_PROVENANCE,
            quality_state=EventQualityState.OK,
            identity_hash="b" * 64,
        )


def test_top_of_book_event_allows_absent_quantity_and_order_count():
    tob = TopOfBookEvent(
        contract_id="COMEX:GC:2026-12",
        bid=BookLevel(price=ExactPrice(mantissa=238700, scale=2)),
        ask=BookLevel(price=ExactPrice(mantissa=238800, scale=2)),
        provenance=GC_PROVENANCE,
        quality_state=EventQualityState.OK,
        identity_hash="c" * 64,
    )
    assert tob.bid.quantity is None
    assert tob.bid.order_count is None


def test_top_of_book_event_requires_centralised_exchange_source():
    otc_provenance = _provenance(SourceClassification.RETAIL_CFD_STREAMING_QUOTE)
    with pytest.raises(ContractValidationError):
        TopOfBookEvent(
            contract_id="COMEX:GC:2026-12",
            bid=BookLevel(price=ExactPrice(mantissa=238700, scale=2)),
            ask=BookLevel(price=ExactPrice(mantissa=238800, scale=2)),
            provenance=otc_provenance,
            quality_state=EventQualityState.OK,
            identity_hash="c" * 64,
        )


def test_market_quote_event_requires_broker_or_retail_source():
    with pytest.raises(ContractValidationError):
        MarketQuoteEvent(
            instrument_id="BROKER_OTC:XAUUSD_FIXTURE",
            bid=ExactPrice(mantissa=265000, scale=2),
            ask=ExactPrice(mantissa=265050, scale=2),
            provenance=GC_PROVENANCE,  # CENTRALISED_EXCHANGE_FUTURES — never allowed on a quote event
            quality_state=EventQualityState.OK,
            identity_hash="d" * 64,
        )


def test_market_quote_event_never_acquires_trade_semantics():
    """A MarketQuoteEvent has no aggressor/quantity field at all — it cannot acquire centralized-
    exchange trade/aggression semantics merely by sharing the common provenance envelope."""
    quote = MarketQuoteEvent(
        instrument_id="BROKER_OTC:XAUUSD_FIXTURE",
        bid=ExactPrice(mantissa=265000, scale=2),
        ask=ExactPrice(mantissa=265050, scale=2),
        provenance=_provenance(SourceClassification.BROKER_OTC_EXECUTABLE_QUOTE_OBSERVATION),
        quality_state=EventQualityState.OK,
        identity_hash="e" * 64,
    )
    assert not hasattr(quote, "aggressor")
    assert not hasattr(quote, "quantity")
    assert quote.event_family is EventFamily.MARKET_QUOTE


def test_market_quote_event_requires_at_least_one_side():
    with pytest.raises(ContractValidationError):
        MarketQuoteEvent(
            instrument_id="BROKER_OTC:XAUUSD_FIXTURE",
            bid=None,
            ask=None,
            provenance=_provenance(SourceClassification.BROKER_OTC_EXECUTABLE_QUOTE_OBSERVATION),
            quality_state=EventQualityState.OK,
            identity_hash="f" * 64,
        )


def test_book_level_rejects_negative_quantity():
    with pytest.raises(ContractValidationError):
        BookLevel(price=ExactPrice(mantissa=238700, scale=2), quantity=-1)
