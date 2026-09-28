"""
market_truth.providers.databento_mbp1 — HMT-2 real-money checkpoint (MBP-1 pilot, Part 4): the
genuine Databento MBP-1 -> HMT-1 canonical-event provider-neutral boundary adapter.

Reads ALREADY-RETAINED, local, immutable native `.dbn.zst` bytes (never a network call, never a
credential read, never `market_truth.acquisition.providers.databento_historical`'s network-
capable `DatabentoHistoricalProvider`/`load_databento_api_key` — only that module's read-only,
local `iter_retained_mbp1_records()` decode helper) and translates each genuine MBP-1 record
into HMT-1's existing provider-neutral `RawSourceRecord` shape (`market_truth.provider`), for
`market_truth.canonicaliser.Canonicaliser` to consume completely unchanged.

Vendor boundary: this module never imports `databento` (or `databento_dbn`) at all — it consumes
only `market_truth.acquisition.source_store.NativeMbp1Record`, a plain, already-translated
dataclass. The one place a vendor `MBP1Msg`/`BidAskPair` object exists is inside
`databento_historical.iter_retained_mbp1_records()`, and it never escapes that function.

Record-type mapping (real, observed MBP-1 semantics — confirmed against the installed SDK's own
`Action`/`Side` docstrings and real retained pilot data this checkpoint, not assumed):
  - `action == "TRADE"`: a genuine trade. The SAME record also carries the resulting top-of-book
    snapshot (`bid_px_00`/`ask_px_00`/...) — MBP-1's own "book depth of 1" design publishes the
    current top-of-book alongside every event, trade or not. This is passed as `payload
    ["book_after"]`, letting the EXISTING, unmodified `Canonicaliser._canonicalise_trade` decide
    (via its own already-governed top-of-book-transition tracking) whether this also emits a
    companion `TopOfBookEvent` at `event_ordinal=1` — the exact "trade that also moves the book"
    mechanism the WO requires reusing, not reinventing.
  - Any other action (`ADD`/`CANCEL`/`MODIFY`/`CLEAR`/`FILL`/`NONE`): a potential top-of-book
    state update, handed to the existing `_canonicalise_top_of_book` (which itself refuses to
    emit anything for a genuinely unchanged BBO — never invented here either).

Aggressor-side judgment call (disclosed — see final report): MBP-1's own `side` field on a TRADE
record is the vendor's real, directly-reported aggressor side (`Side.ASK` = "a sell aggressor in
a trade", `Side.BID` = "a buy aggressor in a trade" — installed SDK's own docstring). HMT-1's
existing `AggressorBasis` enum offers only `BBO_RELATIVE_CLASSIFICATION` or `NOT_AVAILABLE` — no
"provider-native-flag" option exists (and this module must never alter that governed enum to add
one). Since a genuine side is never permitted to pair with `NOT_AVAILABLE`
(`contracts.AggressorClassification.__post_init__`), and `BBO_RELATIVE_CLASSIFICATION` is the
only existing category that is not a fabrication, this module records a genuine MBP-1
`side=BID/ASK` as `BBO_RELATIVE_CLASSIFICATION` — the closest honest fit in the existing governed
vocabulary, not a claim about Databento's own internal computation method. `side="NONE"` (real,
observed on ~6% of this pilot's trades) is recorded as `UNAVAILABLE_AGGRESSOR` — never guessed.

Incomplete-book discipline (no synthetic repair — WO Part 6): a genuinely undefined book side
(`price == UNDEF_PRICE`, e.g. after a `CLEAR`/book-reset action) is real, observed pilot data.
`BookLevel.price` is non-optional (contracts.py), so this module never fabricates a placeholder
price for the missing side — it skips emitting a `TopOfBookEvent` for that specific record
(counted in `Mbp1AdapterQualityCounters.incomplete_book_skipped_records`) and, on a TRADE record
with a genuinely incomplete book, still emits the genuine trade but with `payload["book_after"]
= None` (never fabricating the missing companion top-of-book state).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator, Optional

from market_truth.acquisition.providers.databento_historical import iter_retained_mbp1_records
from market_truth.contracts import EventQualityState
from market_truth.provenance import (
    HistoricalProvenanceEra,
    ProviderReceiptQuality,
    SequenceAvailability,
    SourceClassification,
    TimestampPrecision,
)
from market_truth.provider import MarketDataProvider, ProviderCapability, RawRecordType, RawSourceRecord

DATABENTO_MBP1_PROVIDER_VERSION = "hmt2-databento-mbp1-pilot-provider-v1"
SEQUENCE_DOMAIN_DATABENTO_GLBX_MDP3_VENUE_SEQUENCE = "DATABENTO_GLBX_MDP3_VENUE_SEQUENCE"

# Real DBN wire-protocol sentinel/flag values (confirmed against the installed databento_dbn
# SDK's own exposed constants this checkpoint: UNDEF_PRICE=2**63-1, UNDEF_ORDER_SIZE=2**32-1,
# F_BAD_TS_RECV=8, F_MAYBE_BAD_BOOK=4). Plain int literals — this module never imports the
# vendor SDK itself (see module docstring); DBN wire-protocol constant VALUES are stable
# protocol knowledge, not a vendor object, and copying them here keeps this module's vendor
# boundary genuinely at zero imports.
_UNDEF_PRICE = 9223372036854775807
_UNDEF_ORDER_SIZE = 4294967295
_FLAG_BAD_TS_RECV = 8
_FLAG_MAYBE_BAD_BOOK = 4

_CAPABILITIES = frozenset(
    {
        ProviderCapability.TRADE_EVENTS,
        ProviderCapability.TOP_OF_BOOK_TRANSITIONS,
        ProviderCapability.SOURCE_SEQUENCE,
        ProviderCapability.PROVIDER_RECEIVE_TIMESTAMP,
        ProviderCapability.QUALITY_CONDITION_METADATA,
    }
)


class UnmappedSymbolError(ValueError):
    """Raised when a native MBP-1 record's `instrument_id` cannot be resolved to a raw provider
    symbol via this request's own embedded symbology mapping. Fail-closed (WO Part 4) — never
    guessed, never silently dropped."""


@dataclass
class Mbp1AdapterQualityCounters:
    """Real, per-session counters for the WO Part 6 pilot quality report. Mutated in place by
    `DatabentoMbp1PilotProvider.iter_records()` as it runs — reflects genuine observed pilot
    data, never a synthetic/estimated figure.

    `source_observed_raw_symbols` (valid-empty architecture ruling addition, additive) — every
    raw provider symbol seen on the native stream, added for EVERY native record that reaches
    this adapter's own governed per-request symbology resolution (i.e. `native.raw_symbol is not
    None` — a record that fails even that resolution raises `UnmappedSymbolError` immediately,
    below, and is never added here). This deliberately includes records this adapter goes on to
    filter BEFORE ever constructing a `RawSourceRecord` (an incomplete or crossed book on a
    non-trade action) — those symbols were still genuinely, successfully resolved by this
    adapter; `canonical_worker.py` is what independently checks each one against the governed GC
    contract mapping table, fixing the exact bug the architecture ruling addresses (a session
    could previously look like it "resolved zero contracts" purely because every one of its
    records happened to be filtered before reaching the canonicaliser, even when every single
    one of them genuinely, successfully resolved)."""

    total_native_records: int = 0
    trade_records: int = 0
    book_update_records: int = 0
    unmapped_symbol_records: int = 0
    incomplete_book_skipped_records: int = 0
    crossed_book_skipped_records: int = 0
    bad_receive_time_records: int = 0
    maybe_bad_book_records: int = 0
    action_counts: dict = field(default_factory=dict)
    source_observed_raw_symbols: set = field(default_factory=set)

    def to_dict(self) -> dict:
        return {
            "total_native_records": self.total_native_records,
            "trade_records": self.trade_records,
            "book_update_records": self.book_update_records,
            "unmapped_symbol_records": self.unmapped_symbol_records,
            "incomplete_book_skipped_records": self.incomplete_book_skipped_records,
            "crossed_book_skipped_records": self.crossed_book_skipped_records,
            "bad_receive_time_records": self.bad_receive_time_records,
            "maybe_bad_book_records": self.maybe_bad_book_records,
            "action_counts": dict(sorted(self.action_counts.items())),
            "source_observed_raw_symbols": sorted(self.source_observed_raw_symbols),
        }


def _nanos_to_utc_text(ns: int) -> str:
    """Verbatim nanosecond-precision ISO-8601 UTC text — never derived from a (microsecond-
    limited) parsed `datetime`, so no precision is ever invented OR lost in the identity/row
    serialization value (provenance.py's own documented discipline)."""
    seconds, nanos = divmod(int(ns), 1_000_000_000)
    dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{nanos:09d}+00:00"


def _nanos_to_utc_datetime(ns: int) -> datetime:
    """Parsed UTC datetime for ordering/partitioning ONLY (microsecond-truncated — Python's
    `datetime` cannot hold nanosecond precision; the verbatim text above is the identity-bearing
    value, never this). Built without a float division, so there is no floating-point rounding
    anywhere in this computation."""
    seconds, nanos = divmod(int(ns), 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=nanos // 1000)


def _book_level_payload(price: int, quantity: int, order_count: int) -> Optional[dict]:
    if price == _UNDEF_PRICE:
        return None
    return {
        "price_mantissa": price,
        "price_scale": 9,
        "quantity": None if quantity == _UNDEF_ORDER_SIZE else quantity,
        "order_count": None if order_count == _UNDEF_ORDER_SIZE else order_count,
    }


def _book_is_crossed(bid: dict, ask: dict) -> bool:
    """True iff `ask` < `bid` (both always carry `price_scale=9` here, so a direct mantissa
    comparison is exactly a price comparison — no decimal conversion needed). A genuinely
    crossed top-of-book is real, observed pilot data (momentary source inconsistency, not this
    adapter's error) — `contracts.TopOfBookEvent.__post_init__` correctly refuses to construct
    a crossed book at all, so this adapter must detect and skip it BEFORE handing it to the
    canonicaliser, exactly like a genuinely incomplete (undefined-price) book: never repaired,
    never silently dropped without being counted (`crossed_book_skipped_records`, WO Part 6)."""
    return ask["price_mantissa"] < bid["price_mantissa"]


class DatabentoMbp1PilotProvider(MarketDataProvider):
    """Reads one already-retained native MBP-1 `.dbn.zst` artefact for one pilot session and
    yields `RawSourceRecord`s in the file's own native (venue sequence) order — mirrors
    `providers.fixture.FixtureMarketDataProvider`'s "provider's own deterministic native order"
    discipline exactly, just for a genuine retained artefact instead of a committed fixture.
    """

    def __init__(
        self,
        *,
        retained_path: str,
        session_id: str,
        acquisition_epoch: str,
        provider_id: str = "databento",
        dataset_id: str = "GLBX.MDP3",
    ) -> None:
        super().__init__(provider_id=provider_id, dataset_id=dataset_id, capabilities=_CAPABILITIES)
        self.retained_path = str(retained_path)
        self.session_id = session_id
        self.acquisition_epoch = acquisition_epoch
        self.quality_counters = Mbp1AdapterQualityCounters()
        with open(self.retained_path, "rb") as f:
            self._raw_bytes = f.read()

    def content_sha256(self) -> str:
        import hashlib

        return hashlib.sha256(self._raw_bytes).hexdigest()

    def iter_records(self) -> Iterator[RawSourceRecord]:
        for native in iter_retained_mbp1_records(self.retained_path, session_id=self.session_id):
            self.quality_counters.total_native_records += 1
            self.quality_counters.action_counts[native.action] = (
                self.quality_counters.action_counts.get(native.action, 0) + 1
            )
            if native.raw_symbol is None:
                self.quality_counters.unmapped_symbol_records += 1
                raise UnmappedSymbolError(
                    f"session {self.session_id}: native MBP-1 record #{native.record_index} "
                    f"(instrument_id={native.instrument_id}) has no unique raw-symbol mapping in "
                    f"this request's own embedded symbology — fail-closed, never guessed"
                )
            self.quality_counters.source_observed_raw_symbols.add(native.raw_symbol)
            record = self._translate(native)
            if record is not None:
                yield record

    def _translate(self, native) -> Optional[RawSourceRecord]:
        source_artifact_id = (
            f"{self.provider_id}:{self.dataset_id}:{native.instrument_id}:{native.sequence}:{native.record_index}"
        )
        source_event_time_text = _nanos_to_utc_text(native.ts_event_ns)
        source_event_time = _nanos_to_utc_datetime(native.ts_event_ns)

        bad_receive_time = bool(native.flags & _FLAG_BAD_TS_RECV)
        maybe_bad_book = bool(native.flags & _FLAG_MAYBE_BAD_BOOK)

        if bad_receive_time:
            self.quality_counters.bad_receive_time_records += 1
            provider_receive_time = None
            provider_receive_time_text = None
            provider_receive_quality = ProviderReceiptQuality.NOT_AVAILABLE
            quality_state = EventQualityState.BAD_RECEIVE_TIME
        else:
            provider_receive_time_text = _nanos_to_utc_text(native.ts_recv_ns)
            provider_receive_time = _nanos_to_utc_datetime(native.ts_recv_ns)
            provider_receive_quality = ProviderReceiptQuality.GENUINE
            quality_state = EventQualityState.DEGRADED_SOURCE if maybe_bad_book else EventQualityState.OK

        if maybe_bad_book:
            self.quality_counters.maybe_bad_book_records += 1

        common_kwargs = dict(
            provider_id=self.provider_id,
            dataset_id=self.dataset_id,
            source_classification=SourceClassification.CENTRALISED_EXCHANGE_FUTURES_GENUINE_ORDER_FLOW,
            source_record_schema=DATABENTO_MBP1_PROVIDER_VERSION,
            source_artifact_id=source_artifact_id,
            raw_provider_symbol=native.raw_symbol,
            source_event_time=source_event_time,
            source_event_time_text=source_event_time_text,
            source_timestamp_precision=TimestampPrecision.NANOSECOND,
            provider_receive_time=provider_receive_time,
            provider_receive_time_text=provider_receive_time_text,
            provider_receive_quality=provider_receive_quality,
            source_sequence=native.sequence,
            sequence_domain=SEQUENCE_DOMAIN_DATABENTO_GLBX_MDP3_VENUE_SEQUENCE,
            sequence_availability=SequenceAvailability.AVAILABLE,
            acquisition_epoch=self.acquisition_epoch,
            historical_provenance_era=HistoricalProvenanceEra.ERA_MDP3_FROM_2017_05_21,
            quality_state=quality_state,
        )

        bid = _book_level_payload(native.bid_px_00, native.bid_sz_00, native.bid_ct_00)
        ask = _book_level_payload(native.ask_px_00, native.ask_sz_00, native.ask_ct_00)
        book_usable = bid is not None and ask is not None and not _book_is_crossed(bid, ask)

        if native.action == "TRADE":
            self.quality_counters.trade_records += 1
            book_after = {"bid": bid, "ask": ask} if book_usable else None
            if not book_usable:
                if bid is None or ask is None:
                    self.quality_counters.incomplete_book_skipped_records += 1
                else:
                    self.quality_counters.crossed_book_skipped_records += 1
            payload = {
                "price_mantissa": native.price,
                "price_scale": 9,
                "quantity": native.size,
                "book_after": book_after,
            }
            if native.side in ("BID", "ASK"):
                payload["aggressor_side"] = "BUY" if native.side == "BID" else "SELL"
                payload["aggressor_basis"] = "BBO_RELATIVE_CLASSIFICATION"
            return RawSourceRecord(record_type=RawRecordType.TRADE, payload=payload, **common_kwargs)

        # Non-trade actions -> potential top-of-book update. The existing canonicaliser itself
        # refuses to emit anything for a genuinely unchanged BBO (never invented here either).
        if not book_usable:
            if bid is None or ask is None:
                self.quality_counters.incomplete_book_skipped_records += 1
            else:
                self.quality_counters.crossed_book_skipped_records += 1
            return None
        self.quality_counters.book_update_records += 1
        payload = {"bid": bid, "ask": ask}
        return RawSourceRecord(record_type=RawRecordType.TOP_OF_BOOK_UPDATE, payload=payload, **common_kwargs)
