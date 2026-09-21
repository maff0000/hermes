"""HMT-1 — source sequence semantics (time-order-sequence-model.md §1 item 5, §2, §4):
sequence numbers survive, tagged with their own domain, and no fabricated cross-source ordering."""
from pathlib import Path

from market_truth.canonicaliser import Canonicaliser
from market_truth.futures import ContractMappingTable
from market_truth.provenance import SEQUENCE_DOMAIN_HERMES_RECEIVE_ORDER_APPROXIMATION, SequenceAvailability
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


def test_source_sequence_survives_into_canonical_provenance():
    events = _load_gc_events()
    sequences = [e.provenance.source_sequence for e in events]
    assert all(s is not None for s in sequences)
    # Strictly increasing across the fixture's ordered records — genuine exchange-ordering
    # progression is preserved exactly, not renumbered by HERMES.
    assert sequences == sorted(sequences)
    assert len(set(sequences)) >= 6  # 7 records, one of which shares its trade's sequence with its own companion TopOfBookEvent


def test_sequence_domain_is_tagged_and_never_the_legacy_hermes_stored_row_scheme():
    events = _load_gc_events()
    for event in events:
        assert event.provenance.sequence_domain == "FIXTURE_PROVIDER_V1_NATIVE_SEQUENCE"
        # The legacy HERMES ticks.seq stored-row scheme (time-order-sequence-model.md §2) must
        # never appear as a sequence domain tag anywhere in this package.
        assert "HERMES_STORED_ROW" not in event.provenance.sequence_domain
        assert event.provenance.sequence_availability is SequenceAvailability.AVAILABLE


def test_broker_quote_events_honestly_have_no_sequence():
    mapping = ContractMappingTable.from_json_file(FIXTURES_DIR / "gc_contract_mapping_v1.json")
    canonicaliser = Canonicaliser(mapping_table=mapping)
    provider = FixtureMarketDataProvider(
        FIXTURES_DIR / "broker_quote_equivalent_v1.jsonl", provider_id="hmt1-fixture-provider", dataset_id="hmt1-test"
    )
    for record in provider.iter_records():
        for event in canonicaliser.canonicalise(record):
            assert event.provenance.source_sequence is None
            assert event.provenance.sequence_availability is SequenceAvailability.NOT_AVAILABLE


def test_no_fabricated_cross_source_total_ordering():
    """time-order-sequence-model.md §4: HERMES never invents an ordering between genuinely
    different sources. This package offers exactly one labelled escape hatch for an
    arrival-order-only approximation (`SEQUENCE_DOMAIN_HERMES_RECEIVE_ORDER_APPROXIMATION`) and
    nothing in the canonicaliser/replay path ever assigns it — the canonicaliser only ever passes
    a record's OWN declared sequence_domain through unchanged."""
    gc_events = _load_gc_events()
    assert all(e.provenance.sequence_domain != SEQUENCE_DOMAIN_HERMES_RECEIVE_ORDER_APPROXIMATION for e in gc_events)

    mapping = ContractMappingTable.from_json_file(FIXTURES_DIR / "gc_contract_mapping_v1.json")
    canonicaliser = Canonicaliser(mapping_table=mapping)
    provider = FixtureMarketDataProvider(
        FIXTURES_DIR / "broker_quote_equivalent_v1.jsonl", provider_id="hmt1-fixture-provider", dataset_id="hmt1-test"
    )
    quote_events = []
    for record in provider.iter_records():
        quote_events.extend(canonicaliser.canonicalise(record))

    # Two genuinely different sources (GC centralized-exchange vs broker/OTC quote) never share,
    # or get merged into, one ordering/sequence domain.
    gc_domains = {e.provenance.sequence_domain for e in gc_events}
    quote_domains = {e.provenance.sequence_domain for e in quote_events}
    assert gc_domains.isdisjoint(quote_domains)
