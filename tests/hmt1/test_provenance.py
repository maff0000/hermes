"""HMT-1 — provenance.py: the common envelope, kept permanently distinct, never fabricated."""
from datetime import datetime, timezone

import pytest

from market_truth.provenance import (
    HistoricalProvenanceEra,
    ProvenanceEnvelope,
    ProvenanceValidationError,
    ProviderReceiptQuality,
    SEQUENCE_DOMAIN_NOT_AVAILABLE,
    SequenceAvailability,
    SourceClassification,
    TimestampPrecision,
    parse_utc_text,
)


def _base_kwargs(**overrides):
    base = dict(
        provider_id="fixture-provider:hmt1-fixture-provider-v1",
        dataset_id="hmt1-test-dataset",
        source_classification=SourceClassification.CENTRALISED_EXCHANGE_FUTURES_GENUINE_ORDER_FLOW,
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
    return base


def test_valid_envelope_constructs():
    ProvenanceEnvelope(**_base_kwargs())


def test_source_event_time_must_be_utc_aware():
    kwargs = _base_kwargs(source_event_time=datetime(2026, 9, 15, 13, 30))  # naive
    with pytest.raises(ProvenanceValidationError):
        ProvenanceEnvelope(**kwargs)


def test_provider_receive_time_never_fabricated_when_not_available():
    """provider_receive_quality=NOT_AVAILABLE must never carry a receive time — this is the
    structural enforcement of time-order-sequence-model.md §1 item 2 ("never fabricated or
    backfilled ... if the provider does not actually supply it")."""
    kwargs = _base_kwargs(
        provider_receive_quality=ProviderReceiptQuality.NOT_AVAILABLE,
        provider_receive_time=datetime(2026, 9, 15, 13, 30, 1, tzinfo=timezone.utc),
        provider_receive_time_text="2026-09-15T13:30:01Z",
    )
    with pytest.raises(ProvenanceValidationError):
        ProvenanceEnvelope(**kwargs)


def test_genuine_provider_receive_quality_requires_a_receive_time():
    kwargs = _base_kwargs(provider_receive_quality=ProviderReceiptQuality.GENUINE)
    with pytest.raises(ProvenanceValidationError):
        ProvenanceEnvelope(**kwargs)


def test_synthetic_equated_receive_time_is_a_distinct_quality_from_genuine():
    """The two legacy GC eras equate ts_recv to ts_event synthetically (time-order-sequence-
    model.md §3) — this must never be presented as GENUINE."""
    kwargs = _base_kwargs(
        provider_receive_quality=ProviderReceiptQuality.SYNTHETIC_EQUATED_TO_EVENT_TIME,
        provider_receive_time=datetime(2026, 9, 15, 13, 30, tzinfo=timezone.utc),
        provider_receive_time_text="2026-09-15T13:30:00Z",
        historical_provenance_era=HistoricalProvenanceEra.ERA_PRE_2015_11_20_LEGACY,
    )
    env = ProvenanceEnvelope(**kwargs)
    assert env.provider_receive_quality is not ProviderReceiptQuality.GENUINE


def test_sequence_available_requires_a_sequence_number_and_domain():
    kwargs = _base_kwargs(sequence_availability=SequenceAvailability.AVAILABLE)
    with pytest.raises(ProvenanceValidationError):
        ProvenanceEnvelope(**kwargs)


def test_sequence_not_available_must_not_carry_a_sequence_number():
    kwargs = _base_kwargs(
        source_sequence=1001,
        sequence_domain="FIXTURE_PROVIDER_V1_NATIVE_SEQUENCE",
        sequence_availability=SequenceAvailability.NOT_AVAILABLE,
    )
    with pytest.raises(ProvenanceValidationError):
        ProvenanceEnvelope(**kwargs)


def test_raw_provider_symbol_is_provenance_never_canonical_identity():
    """provider-abstraction.md §3: raw_provider_symbol crosses the boundary as provenance only.
    Nothing on ProvenanceEnvelope exposes it as, or lets it substitute for, a canonical identity
    field — canonical identity lives on the event (`contract_id`/`instrument_id`), never here."""
    env = ProvenanceEnvelope(**_base_kwargs(raw_provider_symbol="GCZ26"))
    assert env.raw_provider_symbol == "GCZ26"
    assert not hasattr(env, "contract_id")
    assert not hasattr(env, "canonical_id")


def test_parse_utc_text_round_trips_without_inventing_precision():
    dt = parse_utc_text("2026-09-15T13:30:00.100Z")
    assert dt.tzinfo is not None
    assert dt.microsecond == 100000


def test_parse_utc_text_rejects_naive_text():
    with pytest.raises(ProvenanceValidationError):
        parse_utc_text("not-a-timestamp")
