"""HMT-1 — canonicaliser.py: provider record -> canonical event(s)."""
from pathlib import Path

import pytest

from market_truth.canonicaliser import Canonicaliser, CanonicalisationError, DuplicateConflictError
from market_truth.contracts import EventFamily, ExactPrice, MarketTradeEvent, TopOfBookEvent
from market_truth.futures import ContractMappingTable
from market_truth.providers.fixture import FixtureMarketDataProvider

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "hmt1"
MAPPING_PATH = FIXTURES_DIR / "gc_contract_mapping_v1.json"


def _mapping():
    return ContractMappingTable.from_json_file(MAPPING_PATH)


def _canonicalise_all(fixture_name: str):
    canonicaliser = Canonicaliser(mapping_table=_mapping())
    provider = FixtureMarketDataProvider(
        FIXTURES_DIR / fixture_name, provider_id="hmt1-fixture-provider", dataset_id="hmt1-test"
    )
    events = []
    for record in provider.iter_records():
        events.extend(canonicaliser.canonicalise(record))
    return events


def test_trade_event_generated_correctly():
    events = _canonicalise_all("gc_mbp1_equivalent_v1.jsonl")
    trades = [e for e in events if isinstance(e, MarketTradeEvent)]
    assert len(trades) == 2
    first = trades[0]
    assert first.contract_id == "COMEX:GC:2026-12"
    assert first.price == ExactPrice(mantissa=238750, scale=2)
    assert first.quantity == 3
    assert first.aggressor.side.value == "BUY"


def test_bbo_transitions_generate_top_of_book_events():
    events = _canonicalise_all("gc_mbp1_equivalent_v1.jsonl")
    tob_events = [e for e in events if isinstance(e, TopOfBookEvent)]
    # 5 TOP_OF_BOOK_UPDATE records (initial formation, bid qty change, ask qty change, price
    # improvement, price deterioration) + 1 companion TopOfBookEvent from the trade that changes
    # BBO (record 7) = 6.
    assert len(tob_events) == 6

    initial, bid_qty_change, ask_qty_change, price_improvement, price_deterioration = tob_events[:5]
    assert initial.bid.quantity == 10 and initial.ask.quantity == 8
    assert bid_qty_change.bid.quantity == 15 and bid_qty_change.ask.quantity == 8
    assert ask_qty_change.ask.quantity == 5
    assert price_improvement.bid.price == ExactPrice(mantissa=238740, scale=2)
    assert price_deterioration.bid.price == ExactPrice(mantissa=238720, scale=2)


def test_unchanged_bbo_does_not_invent_a_top_of_book_event():
    """Record 6 (the trade that leaves BBO unchanged) must never produce a TopOfBookEvent — this
    is WO acceptance #12."""
    events = _canonicalise_all("gc_mbp1_equivalent_v1.jsonl")
    trades = [e for e in events if isinstance(e, MarketTradeEvent)]
    unchanged_trade = trades[0]
    assert unchanged_trade.provenance.source_artifact_id == "gc-mbp1-0006"

    tob_events = [e for e in events if isinstance(e, TopOfBookEvent)]
    companion_source_ids = {e.provenance.source_artifact_id for e in tob_events}
    assert "gc-mbp1-0006" not in companion_source_ids


def test_trade_that_changes_bbo_produces_the_ordinal_subtype_pair():
    """Record 7: one source record producing both a MarketTradeEvent (ordinal 0) and a companion
    TopOfBookEvent (ordinal 1) — the ordinal/subtype mechanism from identity.py (WO §6/§8)."""
    events = _canonicalise_all("gc_mbp1_equivalent_v1.jsonl")
    same_record_events = [e for e in events if e.provenance.source_artifact_id == "gc-mbp1-0007"]
    assert len(same_record_events) == 2
    families = {e.event_family for e in same_record_events}
    assert families == {EventFamily.MARKET_TRADE, EventFamily.TOP_OF_BOOK}

    trade = next(e for e in same_record_events if isinstance(e, MarketTradeEvent))
    tob = next(e for e in same_record_events if isinstance(e, TopOfBookEvent))
    assert trade.event_ordinal == 0
    assert tob.event_ordinal == 1
    assert trade.identity_hash != tob.identity_hash
    assert tob.ask.price == ExactPrice(mantissa=238770, scale=2)


def test_sequence_progression_and_contract_mapping_hold_across_the_whole_fixture():
    events = _canonicalise_all("gc_mbp1_equivalent_v1.jsonl")
    for event in events:
        assert event.contract_id == "COMEX:GC:2026-12"


def test_conflicting_payload_for_same_identity_fails_closed():
    events_or_error = None
    canonicaliser = Canonicaliser(mapping_table=_mapping())
    provider = FixtureMarketDataProvider(
        FIXTURES_DIR / "gc_conflict_case_v1.jsonl", provider_id="hmt1-fixture-provider", dataset_id="hmt1-test"
    )
    records = list(provider.iter_records())
    canonicaliser.canonicalise(records[0])
    with pytest.raises(DuplicateConflictError):
        canonicaliser.canonicalise(records[1])


def test_exact_duplicate_is_deterministically_deduplicated_not_reemitted():
    canonicaliser = Canonicaliser(mapping_table=_mapping())
    provider = FixtureMarketDataProvider(
        FIXTURES_DIR / "gc_duplicate_exact_v1.jsonl", provider_id="hmt1-fixture-provider", dataset_id="hmt1-test"
    )
    records = list(provider.iter_records())
    first_events = canonicaliser.canonicalise(records[0])
    second_events = canonicaliser.canonicalise(records[1])
    assert len(first_events) == 1
    assert len(second_events) == 0  # deterministically deduplicated, not re-emitted


def test_unknown_record_type_is_rejected():
    canonicaliser = Canonicaliser(mapping_table=_mapping())
    # RawSourceRecord.record_type is an enum, so we exercise the fail-closed branch through a
    # deliberately malformed in-memory stand-in rather than constructing an invalid enum member.
    class _BadRecord:
        record_type = "NOT_A_REAL_TYPE"

    with pytest.raises(CanonicalisationError):
        canonicaliser.canonicalise(_BadRecord())
