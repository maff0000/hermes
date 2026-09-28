"""HMT-2 (real-money checkpoint, MBP-1 pilot) — Part 4 adapter tests.

Every test here feeds `DatabentoMbp1PilotProvider.iter_records()` clearly-synthetic
`NativeMbp1Record` instances (mirroring HMT-1's `__synthetic__` fixture discipline — never real
retained pilot bytes; those are exercised separately, deliberately outside pytest/CI, by the
`hmt2f_mbp1_pilot_replay.py` reproduction script — see `test_mbp1_pilot_replay_script_guard.py`
for the structural, zero-network proof covering that script). `iter_retained_mbp1_records` (the
one function that touches the vendor SDK) is monkeypatched out entirely: this whole test module
never imports `databento`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_truth.acquisition.source_store import NativeMbp1Record  # noqa: E402
from market_truth.canonicaliser import Canonicaliser  # noqa: E402
from market_truth.contracts import AggressorBasis, AggressorSide, EventQualityState  # noqa: E402
from market_truth.futures import ContractMappingTable, GcContractIdentity  # noqa: E402
from market_truth.provenance import ProviderReceiptQuality  # noqa: E402
from market_truth.providers import databento_mbp1 as adapter  # noqa: E402

_UNDEF_PRICE = 9223372036854775807
_UNDEF_ORDER_SIZE = 4294967295

_MAPPING_TABLE = ContractMappingTable(
    version="test-mapping-v1",
    entries={"GCJ9": GcContractIdentity(delivery_year=2019, delivery_month=4)},
)


def _native(
    *,
    raw_symbol="GCJ9",
    instrument_id=14651,
    ts_event_ns=1_553_810_400_000_000_000,
    ts_recv_ns=1_553_810_400_041_434_241,
    action="TRADE",
    side="NONE",
    flags=0,
    sequence=1,
    price=1_289_600_000_000,
    size=1,
    bid_px_00=1_289_600_000_000,
    ask_px_00=1_296_100_000_000,
    bid_sz_00=2,
    ask_sz_00=1,
    bid_ct_00=1,
    ask_ct_00=1,
    record_index=0,
    session_id="GC-2019-03-29",
):
    return NativeMbp1Record(
        session_id=session_id, raw_symbol=raw_symbol, instrument_id=instrument_id,
        ts_event_ns=ts_event_ns, ts_recv_ns=ts_recv_ns, action=action, side=side, flags=flags,
        sequence=sequence, price=price, size=size, bid_px_00=bid_px_00, ask_px_00=ask_px_00,
        bid_sz_00=bid_sz_00, ask_sz_00=ask_sz_00, bid_ct_00=bid_ct_00, ask_ct_00=ask_ct_00,
        record_index=record_index,
    )


def _make_provider(records, monkeypatch, session_id="GC-2019-03-29"):
    def _fake_iter(path, *, session_id):
        return iter(records)

    monkeypatch.setattr(adapter, "iter_retained_mbp1_records", _fake_iter)
    provider = adapter.DatabentoMbp1PilotProvider.__new__(adapter.DatabentoMbp1PilotProvider)
    # Bypass __init__'s real file read (no real/synthetic .dbn.zst file needed for these tests).
    adapter.MarketDataProvider.__init__(
        provider, provider_id="databento", dataset_id="GLBX.MDP3", capabilities=adapter._CAPABILITIES
    )
    provider.retained_path = "synthetic-not-a-real-file"
    provider.session_id = session_id
    provider.acquisition_epoch = "test-epoch"
    provider.quality_counters = adapter.Mbp1AdapterQualityCounters()
    return provider


# ----------------------------------------------------------------------------------------------
# Fail-closed unmapped-symbol behaviour (WO Part 4 — never guessed).
# ----------------------------------------------------------------------------------------------

def test_unmapped_symbol_raises_and_never_silently_drops(monkeypatch):
    provider = _make_provider([_native(raw_symbol=None)], monkeypatch)
    with pytest.raises(adapter.UnmappedSymbolError):
        list(provider.iter_records())
    assert provider.quality_counters.unmapped_symbol_records == 1


# ----------------------------------------------------------------------------------------------
# Trade + genuine BBO transition -> ordinal 0/1 mechanism (reused from canonicaliser.py exactly).
# ----------------------------------------------------------------------------------------------

def test_trade_with_book_after_emits_trade_and_top_of_book_via_existing_ordinal_mechanism(monkeypatch):
    provider = _make_provider([_native(action="TRADE", side="BID")], monkeypatch)
    canonicaliser = Canonicaliser(mapping_table=_MAPPING_TABLE)
    events = []
    for record in provider.iter_records():
        events.extend(canonicaliser.canonicalise(record))

    assert len(events) == 2
    trade, tob = events
    assert type(trade).__name__ == "MarketTradeEvent"
    assert trade.event_ordinal == 0
    assert trade.aggressor.side is AggressorSide.BUY
    assert trade.aggressor.basis is AggressorBasis.BBO_RELATIVE_CLASSIFICATION
    assert type(tob).__name__ == "TopOfBookEvent"
    assert tob.event_ordinal == 1
    assert tob.identity_hash != trade.identity_hash


def test_trade_with_side_none_is_recorded_as_unavailable_aggressor_never_guessed(monkeypatch):
    provider = _make_provider([_native(action="TRADE", side="NONE")], monkeypatch)
    canonicaliser = Canonicaliser(mapping_table=_MAPPING_TABLE)
    events = []
    for record in provider.iter_records():
        events.extend(canonicaliser.canonicalise(record))
    trade = events[0]
    assert trade.aggressor.side is AggressorSide.UNKNOWN
    assert trade.aggressor.basis is AggressorBasis.NOT_AVAILABLE


# ----------------------------------------------------------------------------------------------
# TopOfBookEvent ONLY on a genuine transition — never merely because a trade/record occurred.
# ----------------------------------------------------------------------------------------------

def test_unchanged_bbo_across_two_records_emits_no_second_top_of_book_event(monkeypatch):
    same_book = dict(bid_px_00=1_289_600_000_000, ask_px_00=1_296_100_000_000, bid_sz_00=2, ask_sz_00=1, bid_ct_00=1, ask_ct_00=1)
    records = [
        _native(action="ADD", sequence=1, record_index=0, **same_book),
        _native(action="MODIFY", sequence=2, record_index=1, **same_book),  # genuinely unchanged BBO
    ]
    provider = _make_provider(records, monkeypatch)
    canonicaliser = Canonicaliser(mapping_table=_MAPPING_TABLE)
    events = []
    for record in provider.iter_records():
        events.extend(canonicaliser.canonicalise(record))
    # Only the FIRST record's genuine top-of-book state is a real transition; the second,
    # identical, book state must emit nothing (WO acceptance mirrored from HMT-1's own
    # test_canonicaliser.py::test_unchanged_bbo_emits_nothing — never invented here either).
    assert len(events) == 1
    assert type(events[0]).__name__ == "TopOfBookEvent"


def test_genuinely_changed_bbo_across_two_records_emits_two_transitions(monkeypatch):
    records = [
        _native(action="ADD", sequence=1, record_index=0, bid_px_00=1_289_600_000_000, ask_px_00=1_296_100_000_000, bid_sz_00=2, ask_sz_00=1, bid_ct_00=1, ask_ct_00=1),
        _native(action="MODIFY", sequence=2, record_index=1, bid_px_00=1_289_700_000_000, ask_px_00=1_296_100_000_000, bid_sz_00=3, ask_sz_00=1, bid_ct_00=1, ask_ct_00=1),
    ]
    provider = _make_provider(records, monkeypatch)
    canonicaliser = Canonicaliser(mapping_table=_MAPPING_TABLE)
    events = []
    for record in provider.iter_records():
        events.extend(canonicaliser.canonicalise(record))
    assert len(events) == 2
    assert all(type(e).__name__ == "TopOfBookEvent" for e in events)
    assert events[0].identity_hash != events[1].identity_hash


# ----------------------------------------------------------------------------------------------
# No synthetic repair: crossed book / incomplete (undefined-price) book -> skipped, counted.
# ----------------------------------------------------------------------------------------------

def test_crossed_book_is_skipped_not_repaired_and_counted(monkeypatch):
    # ask (1287.50) < bid (1289.60): a genuinely crossed top-of-book.
    provider = _make_provider(
        [_native(action="ADD", bid_px_00=1_289_600_000_000, ask_px_00=1_287_500_000_000)], monkeypatch
    )
    canonicaliser = Canonicaliser(mapping_table=_MAPPING_TABLE)
    events = []
    for record in provider.iter_records():
        events.extend(canonicaliser.canonicalise(record))
    assert events == []
    assert provider.quality_counters.crossed_book_skipped_records == 1
    assert provider.quality_counters.incomplete_book_skipped_records == 0


def test_undefined_price_side_is_skipped_not_fabricated_and_counted(monkeypatch):
    provider = _make_provider([_native(action="CLEAR", bid_px_00=_UNDEF_PRICE, ask_px_00=_UNDEF_PRICE)], monkeypatch)
    canonicaliser = Canonicaliser(mapping_table=_MAPPING_TABLE)
    events = []
    for record in provider.iter_records():
        events.extend(canonicaliser.canonicalise(record))
    assert events == []
    assert provider.quality_counters.incomplete_book_skipped_records == 1


def test_trade_with_crossed_book_still_emits_the_trade_but_with_no_fabricated_book_after(monkeypatch):
    provider = _make_provider(
        [_native(action="TRADE", side="ASK", bid_px_00=1_289_600_000_000, ask_px_00=1_287_500_000_000)], monkeypatch
    )
    canonicaliser = Canonicaliser(mapping_table=_MAPPING_TABLE)
    events = []
    for record in provider.iter_records():
        events.extend(canonicaliser.canonicalise(record))
    assert len(events) == 1
    assert type(events[0]).__name__ == "MarketTradeEvent"
    assert provider.quality_counters.crossed_book_skipped_records == 1


# ----------------------------------------------------------------------------------------------
# F_BAD_TS_RECV honesty — never present a flagged-bad receive timestamp as genuine.
# ----------------------------------------------------------------------------------------------

def test_bad_ts_recv_flag_marks_quality_bad_receive_time_and_drops_the_receive_timestamp(monkeypatch):
    provider = _make_provider([_native(action="ADD", flags=adapter._FLAG_BAD_TS_RECV)], monkeypatch)
    canonicaliser = Canonicaliser(mapping_table=_MAPPING_TABLE)
    events = []
    for record in provider.iter_records():
        events.extend(canonicaliser.canonicalise(record))
    tob = events[0]
    assert tob.quality_state is EventQualityState.BAD_RECEIVE_TIME
    assert tob.provenance.provider_receive_quality is ProviderReceiptQuality.NOT_AVAILABLE
    assert tob.provenance.provider_receive_time is None
    assert tob.provenance.provider_receive_time_text is None
    assert provider.quality_counters.bad_receive_time_records == 1


def test_genuine_ts_recv_is_recorded_as_genuine_when_not_flagged_bad(monkeypatch):
    provider = _make_provider([_native(action="ADD", flags=0)], monkeypatch)
    canonicaliser = Canonicaliser(mapping_table=_MAPPING_TABLE)
    events = []
    for record in provider.iter_records():
        events.extend(canonicaliser.canonicalise(record))
    tob = events[0]
    assert tob.quality_state is EventQualityState.OK
    assert tob.provenance.provider_receive_quality is ProviderReceiptQuality.GENUINE
    assert tob.provenance.provider_receive_time is not None


# ----------------------------------------------------------------------------------------------
# F_MAYBE_BAD_BOOK honesty — the REAL, Databento-documented channel-level gap flag ("indicates
# an unrecoverable gap was detected in the channel") is genuinely decoded and surfaced, never
# invented. HMT-2 quality-instrumentation correction (v3): this flag — NOT a per-symbol view of
# the channel-level `sequence` counter — is now the sole basis for
# `canonical_quality_record.SessionQualityCounters.source_gap_completeness_status` (via
# `Mbp1AdapterQualityCounters.maybe_bad_book_records`, asserted below).
# ----------------------------------------------------------------------------------------------

def test_maybe_bad_book_flag_marks_quality_degraded_source_and_counts_the_record(monkeypatch):
    provider = _make_provider([_native(action="ADD", flags=adapter._FLAG_MAYBE_BAD_BOOK)], monkeypatch)
    canonicaliser = Canonicaliser(mapping_table=_MAPPING_TABLE)
    events = []
    for record in provider.iter_records():
        events.extend(canonicaliser.canonicalise(record))
    tob = events[0]
    assert tob.quality_state is EventQualityState.DEGRADED_SOURCE
    assert provider.quality_counters.maybe_bad_book_records == 1


def test_maybe_bad_book_not_counted_when_flag_absent(monkeypatch):
    provider = _make_provider([_native(action="ADD", flags=0)], monkeypatch)
    canonicaliser = Canonicaliser(mapping_table=_MAPPING_TABLE)
    for record in provider.iter_records():
        list(canonicaliser.canonicalise(record))
    assert provider.quality_counters.maybe_bad_book_records == 0


def test_maybe_bad_book_and_bad_ts_recv_flags_are_independent(monkeypatch):
    """Both real DBN flags can be genuinely set on the same record (a bad receive timestamp
    does not imply a channel gap, and vice versa) — each is counted independently, never
    conflated."""
    combined_flags = adapter._FLAG_BAD_TS_RECV | adapter._FLAG_MAYBE_BAD_BOOK
    provider = _make_provider([_native(action="ADD", flags=combined_flags)], monkeypatch)
    canonicaliser = Canonicaliser(mapping_table=_MAPPING_TABLE)
    for record in provider.iter_records():
        list(canonicaliser.canonicalise(record))
    assert provider.quality_counters.bad_receive_time_records == 1
    assert provider.quality_counters.maybe_bad_book_records == 1


# ----------------------------------------------------------------------------------------------
# Symbol mapping never guesses: raw_symbol not in the governed mapping table fails closed too
# (a second layer, at the canonicaliser/futures.py boundary — this is the EXISTING, unmodified
# ContractMappingTable.resolve() behaviour, exercised here through the real adapter+canonicaliser
# pipeline to prove the two layers compose correctly).
# ----------------------------------------------------------------------------------------------

def test_a_resolved_raw_symbol_not_in_the_governed_mapping_table_fails_closed_via_canonicaliser(monkeypatch):
    from market_truth.canonicaliser import CanonicalisationError

    provider = _make_provider([_native(raw_symbol="GCZ99_NOT_IN_TABLE", action="TRADE")], monkeypatch)
    canonicaliser = Canonicaliser(mapping_table=_MAPPING_TABLE)
    with pytest.raises(CanonicalisationError):
        for record in provider.iter_records():
            canonicaliser.canonicalise(record)


# ----------------------------------------------------------------------------------------------
# Valid-empty architecture ruling — source_observed_raw_symbols tracks EVERY genuinely resolved
# raw symbol, including ones the adapter goes on to filter BEFORE ever constructing a
# RawSourceRecord (an incomplete or crossed book on a non-trade action) -- the exact fix needed
# so canonical_worker.py can independently detect source-side contract resolution even when a
# session's `records` iterable itself ends up empty.
# ----------------------------------------------------------------------------------------------

def test_source_observed_raw_symbols_includes_a_record_filtered_for_crossed_book(monkeypatch):
    provider = _make_provider(
        [_native(action="ADD", raw_symbol="GCJ9", bid_px_00=1_289_600_000_000, ask_px_00=1_287_500_000_000)],
        monkeypatch,
    )
    records = list(provider.iter_records())
    assert records == []  # filtered before ever becoming a RawSourceRecord
    assert provider.quality_counters.crossed_book_skipped_records == 1
    assert provider.quality_counters.source_observed_raw_symbols == {"GCJ9"}


def test_source_observed_raw_symbols_includes_a_record_filtered_for_incomplete_book(monkeypatch):
    provider = _make_provider(
        [_native(action="CLEAR", raw_symbol="GCJ9", bid_px_00=_UNDEF_PRICE, ask_px_00=_UNDEF_PRICE)], monkeypatch,
    )
    records = list(provider.iter_records())
    assert records == []
    assert provider.quality_counters.incomplete_book_skipped_records == 1
    assert provider.quality_counters.source_observed_raw_symbols == {"GCJ9"}


def test_source_observed_raw_symbols_includes_records_that_do_emit_too(monkeypatch):
    provider = _make_provider([_native(action="TRADE", side="BID", raw_symbol="GCJ9")], monkeypatch)
    list(provider.iter_records())
    assert provider.quality_counters.source_observed_raw_symbols == {"GCJ9"}


def test_source_observed_raw_symbols_excludes_a_record_with_no_raw_symbol_at_all(monkeypatch):
    """An adapter-level UnmappedSymbolError (native.raw_symbol is None) is a wholly different,
    already-fail-closed case -- that record's symbol is never added, because it was never
    genuinely resolved by the adapter's own per-request symbology in the first place."""
    provider = _make_provider([_native(raw_symbol=None)], monkeypatch)
    with pytest.raises(adapter.UnmappedSymbolError):
        list(provider.iter_records())
    assert provider.quality_counters.source_observed_raw_symbols == set()


def test_source_observed_raw_symbols_accumulates_across_multiple_records(monkeypatch):
    provider = _make_provider(
        [
            _native(action="TRADE", raw_symbol="GCJ9", sequence=1, record_index=0),
            _native(action="TRADE", raw_symbol="GCJ9", sequence=2, record_index=1),
        ],
        monkeypatch,
    )
    list(provider.iter_records())
    # Same symbol observed twice -- a SET, deduplicated, never a count.
    assert provider.quality_counters.source_observed_raw_symbols == {"GCJ9"}


def test_quality_counters_to_dict_includes_source_observed_raw_symbols(monkeypatch):
    provider = _make_provider([_native(action="TRADE", raw_symbol="GCJ9")], monkeypatch)
    list(provider.iter_records())
    assert provider.quality_counters.to_dict()["source_observed_raw_symbols"] == ["GCJ9"]
