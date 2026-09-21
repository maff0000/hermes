"""HMT-1 — native timestamp precision: survives round trip, never invented, never upgraded."""
from pathlib import Path

from market_truth.canonicaliser import Canonicaliser
from market_truth.futures import ContractMappingTable
from market_truth.partition import PartitionReader, PartitionWriter, serialize_event_row
from market_truth.provenance import TimestampPrecision
from market_truth.providers.fixture import FixtureMarketDataProvider

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "hmt1"


def _load_gc_events():
    mapping = ContractMappingTable.from_json_file(FIXTURES_DIR / "gc_contract_mapping_v1.json")
    canonicaliser = Canonicaliser(mapping_table=mapping)
    provider = FixtureMarketDataProvider(
        FIXTURES_DIR / "gc_mbp1_equivalent_v1.jsonl", provider_id="hmt1-fixture-provider", dataset_id="hmt1-test"
    )
    events = []
    for record in provider.iter_records():
        events.extend(canonicaliser.canonicalise(record))
    return events


def test_native_precision_survives_from_fixture_through_canonical_event():
    events = _load_gc_events()
    assert events, "fixture produced no events"
    for event in events:
        # The fixture declares MILLISECOND precision throughout; the canonical event must carry
        # exactly that — never UNKNOWN, never upgraded to a finer-grained enum value.
        assert event.provenance.source_timestamp_precision is TimestampPrecision.MILLISECOND


def test_precision_survives_partition_round_trip(tmp_path):
    events = _load_gc_events()
    writer = PartitionWriter(tmp_path)
    results = writer.write(events)
    reader = PartitionReader(tmp_path)

    original_by_id = {e.identity_hash: e for e in events}
    for result in results:
        for reloaded in reader.read_events(result.relative_dir, result.event_family):
            original = original_by_id[reloaded.identity_hash]
            assert reloaded.provenance.source_timestamp_precision == original.provenance.source_timestamp_precision
            assert reloaded.provenance.source_event_time_text == original.provenance.source_event_time_text


def test_absent_provider_receive_time_never_invented_on_reload(tmp_path):
    """The broker/OTC fixture declares provider_receive_quality=NOT_AVAILABLE for every record.
    That absence must reload as absence, never as a fabricated timestamp."""
    mapping = ContractMappingTable.from_json_file(FIXTURES_DIR / "gc_contract_mapping_v1.json")
    canonicaliser = Canonicaliser(mapping_table=mapping)
    provider = FixtureMarketDataProvider(
        FIXTURES_DIR / "broker_quote_equivalent_v1.jsonl", provider_id="hmt1-fixture-provider", dataset_id="hmt1-test"
    )
    events = []
    for record in provider.iter_records():
        events.extend(canonicaliser.canonicalise(record))

    writer = PartitionWriter(tmp_path)
    results = writer.write(events)
    reader = PartitionReader(tmp_path)
    for result in results:
        for reloaded in reader.read_events(result.relative_dir, result.event_family):
            assert reloaded.provenance.provider_receive_time is None
            assert reloaded.provenance.provider_receive_time_text is None


def test_row_serialization_never_zero_pads_precision_text():
    """serialize_event_row uses the verbatim source_event_time_text, never a re-derived
    datetime.isoformat() call that could invent trailing sub-second zeros the source never sent."""
    events = _load_gc_events()
    for event in events:
        row = serialize_event_row(event)
        assert event.provenance.source_event_time_text.encode("utf-8") in row
