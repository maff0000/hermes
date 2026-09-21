"""
market_truth.acquisition.providers.databento_historical — Databento historical metadata adapter
(HMT-2B foundation: zero-spend; HMT-2 real-money checkpoint: exactly two authorised, hardcoded,
narrowly-scoped billable acquisitions added on top — see `acquire_reference_series_ohlcv1h()`
and `acquire_gc_definitions()` below. Every other capability in this module remains free/
zero-spend, exactly as HMT-2B/2B.1 left it.)

HMT-2B.1 extension (narrow, documented, backward-compatible)
--------------------------------------------------------------
`get_cost_estimate()`, `get_record_count_estimate()`, and `get_billable_size_estimate()` each
gained an optional `stype_in` keyword argument (default `"raw_symbol"`, the vendor SDK's own
default — every pre-existing call site and test is unaffected). This was genuinely required to
obtain a correct quote for a non-raw-symbol request: HMT-2B.1 Part 3 quotes the continuous
contract `GC.v.0` (`stype_in="continuous"`) and Part 4 quotes the parent-symbology definition
schema for `GC.FUT` (`stype_in="parent"`). Without this, the adapter could only ever quote
raw-symbol requests. Still free, informational, metadata-only calls — never a billable data
transfer; no new capability, credential path, or scope boundary is touched.

Scope (WO Part 1, binding)
--------------------------
Implemented — all free-tier, informational, metadata-only Databento operations:
    - Symbology/definitions resolution via `client.symbology.resolve()` — resolves raw provider
      symbols to instrument metadata for a date range. This is the exact capability the earlier,
      separate Helm HMT-0 metadata study used to split outright/spread symbol buckets (see
      docs/architecture/hmt0-market-truth-v2/gc-data-volume-and-cost-study.md §2: "methodology:
      symbology.resolve per-bucket outright/spread split, cross-validated against Databento's own
      instrument_class=F definition-schema field"). This module re-implements the
      `symbology.resolve` half of that pattern as reusable adapter code — no secret or prior
      script is copied. See `classify_gc_symbol_shape()` below for an explicit, honest statement
      of what is and is NOT re-implemented from the "cross-validated against instrument_class=F"
      half of that sentence.
    - Free cost/record-count/billable-size metadata estimates (`metadata.get_cost`,
      `metadata.get_record_count`, `metadata.get_billable_size`) — exactly the informational
      calls named in the HMT-2 pre-download quotation gate
      (docs/architecture/hmt0-market-truth-v2/hmt2-provisional-acquisition-roadmap.md §10, steps
      3-4). None of these ever transfers billable historical data; each is a single small JSON/
      scalar metadata response.

Deliberately NOT implemented (WO Part 1, absolute) — UPDATED by the HMT-2 real-money checkpoint
--------------------------------------------------------------------------------------------------
    - `download_historical_range()` (the original generic bulk-download stub) remains
      permanently forbidden/always-raising — see that method's own docstring, unchanged.
    - Any live-streaming client of any kind. This module never imports, references, or
      constructs `databento.Live` (or any live/streaming symbol) anywhere.
    - Beyond `download_historical_range()`'s permanent prohibition, this module now has EXACTLY
      two, narrowly-hardcoded, real bulk-download methods — `acquire_reference_series_ohlcv1h()`
      and `acquire_gc_definitions()` (see their own docstrings) — each Central-PO/Chief-
      Architect-authorised for exactly one specific, real-money request shape. Neither is a
      generic "acquire anything" gateway: each hardcodes its own dataset/symbols/stype_in/schema
      as literal constants at its one `timeseries.get_range` call site (statically verified —
      tests/hmt2/test_no_network_guard.py + tests/hmt2/test_hardcoded_acquisition_call_sites.py).
      No other function anywhere in this package may ever call `.get_range` at all, for any
      schema — MBP-1/TBBO/trades/MBO acquisition remains structurally impossible.

Vendor boundary
----------------
Mirrors `market_truth/provider.py`'s capability-abstraction pattern: every public method here
returns one of THIS module's own plain, frozen dataclasses. No `databento`-typed object is ever
returned to a caller — the one place a vendor object exists at all is the private `self._client`
handle, and the raw response each private call receives is translated away from immediately, in
the same method that received it.

Credential handling
--------------------
See `load_databento_api_key()`. The key is loaded from an external file at call time, held only
in a local variable / the constructed vendor client object, and is defensively scrubbed out of
any exception message this module ever raises (`_scrub()`). This module never prints, logs, or
writes the key value anywhere, and never fabricates a placeholder if the real file cannot be
read — it fails closed (`CredentialLoadError`).
"""
from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, FrozenSet, Iterable, Mapping, Optional, Sequence, Tuple

try:
    import databento  # noqa: F401  # the ONLY import site of this vendor SDK in this repository
except ImportError:  # pragma: no cover - exercised only if the optional dependency is absent
    databento = None  # type: ignore[assignment]

from market_truth.acquisition import (  # noqa: E402
    HMT2B1_ELIGIBLE_RANGE_START,
    HMT2B1_RESOLVED_FINAL_CUTOFF_DATE,
)

ADAPTER_VERSION = "hmt2b-databento-historical-adapter-v1"

# ------------------------------------------------------------------------------------------
# HMT-2 (real-money checkpoint) — the exactly-two-authorised-requests governed range.
#
# Both authorised real acquisitions (`acquire_reference_series_ohlcv1h()` and
# `acquire_gc_definitions()` below) use this SAME governed date range — the resolved HMT2B1
# eligible range start through the resolved final cutoff date, INCLUSIVE, expressed to the
# vendor as an EXCLUSIVE `end` one calendar day past the cutoff (empirically confirmed vendor
# behaviour — see research/hmt2/hmt2b1_reference_and_definition_quotes.py's own comment on
# this exact point; reproduced here rather than imported, since that script is a standalone
# reproduction tool, not a library module).
# ------------------------------------------------------------------------------------------
GOVERNED_REAL_ACQUISITION_START = HMT2B1_ELIGIBLE_RANGE_START
GOVERNED_REAL_ACQUISITION_END_EXCLUSIVE = (
    _dt.date.fromisoformat(HMT2B1_RESOLVED_FINAL_CUTOFF_DATE) + _dt.timedelta(days=1)
).isoformat()

# Governed external-secret location for this exact credential (see WO Part 1 credential-handling
# section — placed there by a prior, separate, already-completed WO). Never committed, never
# embedded as a literal value anywhere in this module.
DEFAULT_DATABENTO_API_KEY_PATH = "/srv-dev/secrets/databento_historical_api_key"
DATABENTO_API_KEY_PATH_ENV_VAR = "DATABENTO_HISTORICAL_API_KEY_PATH"


class DatabentoAdapterError(RuntimeError):
    """Raised on any adapter-level failure. Message text is always scrubbed of the live API key
    value before it is ever raised, logged, or displayed — see `_scrub()`."""


class CredentialLoadError(DatabentoAdapterError):
    """Raised when the Databento historical API key cannot be loaded from its governed file
    location. Fails closed: this module never fabricates, guesses, or substitutes a placeholder
    key value under any circumstance — a missing/unreadable/empty file stops this specific lane,
    it does not invent a key."""


class DatabentoScopeError(DatabentoAdapterError):
    """Raised by a deliberately-unimplemented capability (bulk download / live streaming) — see
    module docstring. Distinguishes 'out of this WO's authorised scope' from a genuine runtime
    failure calling a real, authorised capability."""


def _scrub(text: str, secret: Optional[str]) -> str:
    """Remove a known secret value from a string before it is ever raised/logged/displayed.
    Defence-in-depth against a vendor SDK ever including the API key in a repr/error string."""
    if secret:
        text = text.replace(secret, "***REDACTED***")
    return text


def load_databento_api_key(path: Optional[str] = None) -> str:
    """Load the Databento historical API key from its governed external file location.

    NEVER returns a fabricated/placeholder value; fails closed (`CredentialLoadError`) on any
    read problem — missing file, permission denied, or empty content. The caller is responsible
    for holding the returned value only in memory (e.g. passed straight into the
    `DatabentoHistoricalProvider` constructor) and never writing it to disk, a log, Memory
    Fabric, a commit, or any other output.
    """
    key_path = Path(path or os.environ.get(DATABENTO_API_KEY_PATH_ENV_VAR) or DEFAULT_DATABENTO_API_KEY_PATH)
    try:
        raw = key_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CredentialLoadError(
            f"could not read the Databento historical API key from {key_path} "
            f"({type(exc).__name__}) — refusing to proceed; this module never fabricates a key"
        ) from None
    value = raw.strip()
    if not value:
        raise CredentialLoadError(f"Databento historical API key file at {key_path} is empty")
    return value


class GcSymbolShape:
    """Plain string constants for the local symbol-SHAPE classifier below — deliberately NOT a
    vendor `instrument_class` field. See `classify_gc_symbol_shape()` docstring."""

    OUTRIGHT = "OUTRIGHT_SHAPE"
    SPREAD_OR_OTHER = "SPREAD_OR_OTHER_SHAPE"


_GC_MONTH_CODES = frozenset("FGHJKMNQUVXZ")  # standard CME futures month-code letters


def classify_gc_symbol_shape(raw_symbol: str) -> str:
    """A pure, vendor-independent, PROVISIONAL symbol-SHAPE classifier for candidate-list
    narrowing only — `GcSymbolShape.OUTRIGHT` for a bare `GC<month-code><2-digit-year>` shape
    (e.g. `GCZ26`); `GcSymbolShape.SPREAD_OR_OTHER` for everything else (calendar spreads,
    butterfly notations, and any symbol this function cannot positively confirm as a plain
    outright shape).

    Honest disclosure — this is NOT the authoritative outright/spread determination. The prior
    HMT-0 metadata study's own methodology (gc-data-volume-and-cost-study.md §2) cross-validated
    its `symbology.resolve`-based split against Databento's live `instrument_class=F`
    DEFINITION-SCHEMA field. This adapter deliberately does not call the definition schema at
    all (see `download_historical_range()` — the definition schema is fetched via the exact
    `timeseries.get_range` method family this WO's Part 1 forbids for any schema argument), so
    that live cross-validation step is NOT re-implemented here. This function exists only to let
    a caller cheaply narrow a candidate symbol list to LIKELY outrights before spending a real
    `symbology.resolve()` call on it — it must never be presented as, or relied on as, a
    confirmed instrument-class determination. This is an explicit, disclosed judgment call — see
    the WO final report.
    """
    if not raw_symbol or not raw_symbol.upper().startswith("GC"):
        return GcSymbolShape.SPREAD_OR_OTHER
    rest = raw_symbol.upper()[2:]
    if len(rest) == 3 and rest[0] in _GC_MONTH_CODES and rest[1:].isdigit():
        return GcSymbolShape.OUTRIGHT
    return GcSymbolShape.SPREAD_OR_OTHER


def filter_likely_outrights(raw_symbols: Iterable[str]) -> Tuple[str, ...]:
    """Symbols classified `OUTRIGHT_SHAPE` by `classify_gc_symbol_shape()` — provisional
    candidate-list narrowing only, see that function's docstring."""
    return tuple(s for s in raw_symbols if classify_gc_symbol_shape(s) == GcSymbolShape.OUTRIGHT)


def filter_likely_spreads_or_other(raw_symbols: Iterable[str]) -> Tuple[str, ...]:
    return tuple(s for s in raw_symbols if classify_gc_symbol_shape(s) == GcSymbolShape.SPREAD_OR_OTHER)


@dataclass(frozen=True)
class SymbologyResolution:
    """One resolved (or unresolved) raw provider symbol — this module's own plain shape, never a
    vendor object."""

    raw_symbol: str
    stype_in: str
    stype_out: str
    resolved_output_symbols: Tuple[str, ...]
    found: bool


@dataclass(frozen=True)
class SymbologyResolutionResult:
    dataset: str
    stype_in: str
    stype_out: str
    start_date: str
    end_date: str
    resolutions: Tuple[SymbologyResolution, ...]

    def found_symbols(self) -> Tuple[str, ...]:
        return tuple(r.raw_symbol for r in self.resolutions if r.found)

    def not_found_symbols(self) -> Tuple[str, ...]:
        return tuple(r.raw_symbol for r in self.resolutions if not r.found)


@dataclass(frozen=True)
class CostEstimate:
    dataset: str
    schema: str
    symbols: Tuple[str, ...]
    start: str
    end: str
    mode: str
    quoted_cost_usd: float
    stype_in: str = "raw_symbol"


@dataclass(frozen=True)
class RecordCountEstimate:
    dataset: str
    schema: str
    symbols: Tuple[str, ...]
    start: str
    end: str
    record_count: int
    stype_in: str = "raw_symbol"


@dataclass(frozen=True)
class BillableSizeEstimate:
    dataset: str
    schema: str
    symbols: Tuple[str, ...]
    start: str
    end: str
    billable_size_bytes: int
    stype_in: str = "raw_symbol"


@dataclass(frozen=True)
class RealBulkAcquisitionResult:
    """This module's own plain result shape for a REAL, billable bulk acquisition — returned
    ONLY by `acquire_reference_series_ohlcv1h()` / `acquire_gc_definitions()` below. The vendor's
    own `databento.DBNStore` object never escapes either method: this dataclass is built from it
    (`.nbytes`, `.to_df()`) and then the vendor object is discarded, exactly mirroring this
    module's existing translate-at-the-boundary discipline for the free metadata methods above.

    `object_path` is the local filesystem path the raw vendor DBN(+zstd) bytes were streamed
    to on disk (via the vendor SDK's own `path=` streaming parameter — this module never holds
    the full multi-hundred-MB/GB payload in memory as a single Python bytes object). The
    caller (a separately-authorised acquisition script — never this module) is responsible for
    hashing that file and recording it as an immutable native-source artefact via
    `market_truth.acquisition.source_store.NativeSourceStore`.
    """

    dataset: str
    schema: str
    symbols: Tuple[str, ...]
    stype_in: str
    start: str
    end: str
    object_path: str
    nbytes: int
    record_count: int
    adapter_version: str = ADAPTER_VERSION


ClientFactory = Callable[[str], Any]


def _default_client_factory(api_key: str) -> Any:
    if databento is None:  # pragma: no cover - exercised only when the optional dep is absent
        raise DatabentoAdapterError(
            "the 'databento' package is not installed — add it to requirements.txt / the active "
            "environment before constructing a real DatabentoHistoricalProvider"
        )
    return databento.Historical(key=api_key)


class DatabentoHistoricalProvider:
    """Zero-spend Databento historical metadata adapter. See module docstring for exact scope.

    Construction never makes a network call — constructing the vendor `databento.Historical`
    client only stores the key locally inside that object; no request is sent until one of the
    metadata methods below is called. Every such method is, per Databento's own documented
    pricing model and per the WO's own pre-download quotation gate
    (hmt2-provisional-acquisition-roadmap.md §10), a free, informational, metadata-only call —
    never a billable data transfer.
    """

    def __init__(self, *, api_key: str, client_factory: Optional[ClientFactory] = None) -> None:
        if not api_key or not api_key.strip():
            raise CredentialLoadError("api_key must be a non-empty string")
        self._api_key = api_key
        factory = client_factory or _default_client_factory
        try:
            self._client = factory(api_key)
        except DatabentoAdapterError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise DatabentoAdapterError(_scrub(str(exc), api_key)) from None
        self.capabilities: FrozenSet[str] = frozenset(
            {
                "DEFINITIONS_SYMBOLOGY_RESOLUTION",
                "COST_METADATA_ESTIMATE",
                "RECORD_COUNT_METADATA_ESTIMATE",
                "BILLABLE_SIZE_METADATA_ESTIMATE",
                # HMT-2 real-money checkpoint — two, and only two, narrowly-hardcoded billable
                # bulk-acquisition capabilities. See acquire_reference_series_ohlcv1h() /
                # acquire_gc_definitions() below. Neither is a generic "acquire anything"
                # capability; each is hardcoded to its own single authorised request shape.
                "REFERENCE_SERIES_GC_V0_OHLCV1H_BULK_ACQUISITION",
                "GC_FUT_DEFINITIONS_BULK_ACQUISITION",
            }
        )

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def _call(self, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Exception as exc:
            raise DatabentoAdapterError(_scrub(str(exc), self._api_key)) from None

    def resolve_symbology(
        self,
        *,
        dataset: str,
        symbols: Sequence[str],
        stype_in: str,
        stype_out: str,
        start_date: str,
        end_date: str,
    ) -> SymbologyResolutionResult:
        """Free metadata call (`client.symbology.resolve`) — resolves raw provider symbols to
        instrument metadata for the given date range. Translates the vendor's raw response into
        this module's own plain `SymbologyResolutionResult` at the boundary; the vendor's own
        response object/shape never escapes this method."""
        symbol_list = list(symbols)
        raw = self._call(
            lambda: self._client.symbology.resolve(
                dataset=dataset,
                symbols=symbol_list,
                stype_in=stype_in,
                stype_out=stype_out,
                start_date=start_date,
                end_date=end_date,
            )
        )
        return _translate_symbology_response(
            raw,
            dataset=dataset,
            stype_in=stype_in,
            stype_out=stype_out,
            start_date=start_date,
            end_date=end_date,
            requested_symbols=symbol_list,
        )

    def get_cost_estimate(
        self,
        *,
        dataset: str,
        schema: str,
        symbols: Sequence[str],
        start: str,
        end: str,
        mode: str = "historical-streaming",
        stype_in: str = "raw_symbol",
    ) -> CostEstimate:
        """Free metadata call (`client.metadata.get_cost`).

        HMT-2B.1 extension (narrow, documented, backward-compatible): adds an optional
        `stype_in` passthrough, defaulting to the vendor SDK's own default (`"raw_symbol"`) so
        every existing call site/test is unaffected. This is required for a genuinely correct
        quote against a non-raw-symbol request — e.g. HMT-2B.1 Part 3's continuous-contract
        quote (`symbols=["GC.v.0"], stype_in="continuous"`) and Part 4's parent-symbology
        definition-schema quote (`symbols=["GC.FUT"], stype_in="parent"`). Still a free,
        informational, metadata-only call — never a billable data transfer.
        """
        symbol_list = list(symbols)
        quoted = self._call(
            lambda: self._client.metadata.get_cost(
                dataset=dataset,
                symbols=symbol_list,
                schema=schema,
                start=start,
                end=end,
                mode=mode,
                stype_in=stype_in,
            )
        )
        return CostEstimate(
            dataset=dataset,
            schema=schema,
            symbols=tuple(symbol_list),
            start=str(start),
            end=str(end),
            mode=mode,
            quoted_cost_usd=float(quoted),
            stype_in=stype_in,
        )

    def get_record_count_estimate(
        self,
        *,
        dataset: str,
        schema: str,
        symbols: Sequence[str],
        start: str,
        end: str,
        stype_in: str = "raw_symbol",
    ) -> RecordCountEstimate:
        """See `get_cost_estimate` docstring for the HMT-2B.1 `stype_in` extension rationale —
        identical here. Free metadata call (`client.metadata.get_record_count`)."""
        symbol_list = list(symbols)
        count = self._call(
            lambda: self._client.metadata.get_record_count(
                dataset=dataset, symbols=symbol_list, schema=schema, start=start, end=end, stype_in=stype_in,
            )
        )
        return RecordCountEstimate(
            dataset=dataset,
            schema=schema,
            symbols=tuple(symbol_list),
            start=str(start),
            end=str(end),
            record_count=int(count),
            stype_in=stype_in,
        )

    def get_billable_size_estimate(
        self,
        *,
        dataset: str,
        schema: str,
        symbols: Sequence[str],
        start: str,
        end: str,
        stype_in: str = "raw_symbol",
    ) -> BillableSizeEstimate:
        """See `get_cost_estimate` docstring for the HMT-2B.1 `stype_in` extension rationale —
        identical here. Free metadata call (`client.metadata.get_billable_size`)."""
        symbol_list = list(symbols)
        size = self._call(
            lambda: self._client.metadata.get_billable_size(
                dataset=dataset, symbols=symbol_list, schema=schema, start=start, end=end, stype_in=stype_in,
            )
        )
        return BillableSizeEstimate(
            dataset=dataset,
            schema=schema,
            symbols=tuple(symbol_list),
            start=str(start),
            end=str(end),
            billable_size_bytes=int(size),
            stype_in=stype_in,
        )

    def acquire_reference_series_ohlcv1h(
        self,
        *,
        start: str = GOVERNED_REAL_ACQUISITION_START,
        end: str = GOVERNED_REAL_ACQUISITION_END_EXCLUSIVE,
        path: str,
    ) -> RealBulkAcquisitionResult:
        """HMT-2 real-money checkpoint — Request 1 of exactly two authorised real acquisitions.

        THE ONLY function in this entire codebase permitted to call the vendor's real bulk
        `timeseries.get_range` method with `schema="ohlcv-1h"`. `dataset`, `symbols`,
        `stype_in`, and `schema` are ALL hardcoded literal values at the call site below — never
        variables, never caller-influenced. The only parameter a caller may vary at all is the
        date range, and even that is validated against the governed HMT2B1 eligible-range
        constants (`GOVERNED_REAL_ACQUISITION_START` / `GOVERNED_REAL_ACQUISITION_END_EXCLUSIVE`,
        derived from `market_truth.acquisition.HMT2B1_ELIGIBLE_RANGE_START` /
        `HMT2B1_RESOLVED_FINAL_CUTOFF_DATE`) — any other value is rejected fail-closed
        (`DatabentoScopeError`) before any network call is made.

        PURPOSE BOUNDARY (repeated because it matters): this request is corpus-selection
        metadata ONLY. It is permanently NOT canonical GC Market Truth v2, NOT the P0 corpus,
        NOT a derived HERMES microstructure fact, NOT a DARWIN input, NOT a trading
        feature/signal.

        This is a genuinely billable request (~$0.55 at the governed range, per the free
        `get_cost_estimate()` quote reconfirmed immediately before every real call) — callers
        MUST re-confirm the quote via the free metadata methods above immediately before
        calling this method, exactly once, for the exactly-two-requests this checkpoint
        authorises. `path` is required: the raw vendor DBN(+zstd) response is streamed straight
        to that local file (never fully materialised as an in-memory `bytes` object by this
        module) so the caller can retain it as an immutable native-source artefact via
        `market_truth.acquisition.source_store.NativeSourceStore`.
        """
        _assert_governed_real_acquisition_range(start, end, caller="acquire_reference_series_ohlcv1h")
        store = self._call(
            lambda: self._client.timeseries.get_range(
                dataset="GLBX.MDP3",
                symbols=["GC.v.0"],
                stype_in="continuous",
                schema="ohlcv-1h",
                start=start,
                end=end,
                path=path,
            )
        )
        return _translate_real_bulk_acquisition(
            store,
            dataset="GLBX.MDP3",
            schema="ohlcv-1h",
            symbols=("GC.v.0",),
            stype_in="continuous",
            start=start,
            end=end,
            object_path=path,
        )

    def acquire_gc_definitions(
        self,
        *,
        start: str = GOVERNED_REAL_ACQUISITION_START,
        end: str = GOVERNED_REAL_ACQUISITION_END_EXCLUSIVE,
        path: str,
    ) -> RealBulkAcquisitionResult:
        """HMT-2 real-money checkpoint — Request 2 of exactly two authorised real acquisitions.

        THE ONLY function in this entire codebase permitted to call the vendor's real bulk
        `timeseries.get_range` method with `schema="definition"`. `dataset`, `symbols`,
        `stype_in`, and `schema` are ALL hardcoded literal values at the call site below —
        never variables, never caller-influenced. Only the date range may vary, and it is
        validated exactly as in `acquire_reference_series_ohlcv1h()` above (see that method's
        docstring — identical governed-range rule, same constants).

        PURPOSE: actual GC outright/spread identity verification and contract-mapping evidence
        (see `market_truth.futures.ContractMappingTable`) — NOT corpus-selection metadata, and
        NOT MBP-1/TBBO/trades/MBO data (none of which this method, or any other function in
        this package, ever requests).

        This is a genuinely billable request (~$1.69 at the governed range, per the free
        `get_cost_estimate()` quote reconfirmed immediately before every real call). `path` is
        required — see `acquire_reference_series_ohlcv1h()` docstring for the identical
        streamed-to-disk / immutable-native-artefact rationale (this request's payload is
        roughly 1GB; it is never fully materialised as a single in-memory `bytes` object by
        this module).
        """
        _assert_governed_real_acquisition_range(start, end, caller="acquire_gc_definitions")
        store = self._call(
            lambda: self._client.timeseries.get_range(
                dataset="GLBX.MDP3",
                symbols=["GC.FUT"],
                stype_in="parent",
                schema="definition",
                start=start,
                end=end,
                path=path,
            )
        )
        return _translate_real_bulk_acquisition(
            store,
            dataset="GLBX.MDP3",
            schema="definition",
            symbols=("GC.FUT",),
            stype_in="parent",
            start=start,
            end=end,
            object_path=path,
        )

    def download_historical_range(self, *args: Any, **kwargs: Any) -> None:
        """INTENTIONALLY UNIMPLEMENTED — always raises `DatabentoScopeError`.

        This is the one capability WO Part 1 explicitly forbids: any bulk historical-data-
        download method (`timeseries.get_range` or equivalent), for ANY schema argument —
        including `schema="definition"`. Definitions/instrument-metadata RECORDS are, in the
        real Databento SDK, retrieved via that exact same `timeseries.get_range` call family as
        billable trade/quote data. Rather than assume the definition schema is priced at zero — a
        judgment call this checkpoint declines to make without independent, live-verified
        confirmation — this adapter treats the WHOLE `timeseries.get_range` method family as out
        of scope, and relies solely on `resolve_symbology()` (a genuinely distinct, documented
        free endpoint) plus the three free `metadata.*` estimate calls above. Any actual data
        acquisition — including a future, possibly-free, definition-schema pull — requires a
        separately-authorised acquisition step per the HMT-2 pre-download quotation gate
        (docs/architecture/hmt0-market-truth-v2/hmt2-provisional-acquisition-roadmap.md §10).
        """
        raise DatabentoScopeError(
            "download_historical_range (any timeseries.get_range-equivalent bulk call, for any "
            "schema) is deliberately not implemented in HMT-2B — see this method's docstring"
        )


def _assert_governed_real_acquisition_range(start: str, end: str, *, caller: str) -> None:
    """Shared validator for the two real-acquisition methods above. Both use the SAME governed
    range (see `GOVERNED_REAL_ACQUISITION_START`/`_END_EXCLUSIVE`) — sharing this validator does
    NOT weaken either method's hardcoding: `dataset`/`symbols`/`stype_in`/`schema` are still
    literal constants at each call site (this only ever validates the two date-range strings,
    never anything schema/symbol/stype_in-shaped). Fails closed on any deviation whatsoever —
    never coerces, rounds, or silently widens/narrows a caller-supplied range."""
    if start != GOVERNED_REAL_ACQUISITION_START or end != GOVERNED_REAL_ACQUISITION_END_EXCLUSIVE:
        raise DatabentoScopeError(
            f"{caller}: start/end must exactly match the governed HMT-2 real-acquisition range "
            f"({GOVERNED_REAL_ACQUISITION_START!r} .. {GOVERNED_REAL_ACQUISITION_END_EXCLUSIVE!r} "
            f"exclusive, derived from market_truth.acquisition.HMT2B1_ELIGIBLE_RANGE_START / "
            f"HMT2B1_RESOLVED_FINAL_CUTOFF_DATE) — got start={start!r} end={end!r}. This method "
            f"is hardcoded to exactly one authorised request shape and refuses any other range."
        )


def _translate_real_bulk_acquisition(
    store: Any,
    *,
    dataset: str,
    schema: str,
    symbols: Tuple[str, ...],
    stype_in: str,
    start: str,
    end: str,
    object_path: str,
) -> RealBulkAcquisitionResult:
    """Translate the vendor `databento.DBNStore` object into this module's own plain
    `RealBulkAcquisitionResult` at the boundary — the vendor object itself never escapes this
    function (mirrors `_translate_symbology_response` above). `record_count` is obtained via
    `len(store.to_df())`; `nbytes` via the vendor object's own `.nbytes` property. Neither
    requires holding the raw byte payload in memory a second time (it was already streamed to
    `object_path` on disk by the caller's `path=` argument to `timeseries.get_range`)."""
    record_count = len(store.to_df())
    nbytes = int(store.nbytes)
    return RealBulkAcquisitionResult(
        dataset=dataset,
        schema=schema,
        symbols=tuple(symbols),
        stype_in=stype_in,
        start=str(start),
        end=str(end),
        object_path=str(object_path),
        nbytes=nbytes,
        record_count=record_count,
    )


def _translate_symbology_response(
    raw: Any,
    *,
    dataset: str,
    stype_in: str,
    stype_out: str,
    start_date: str,
    end_date: str,
    requested_symbols: Sequence[str],
) -> SymbologyResolutionResult:
    """Translate Databento's `symbology.resolve` response (a mapping per the documented API
    shape: `{'result': {<symbol>: [{'d0': .., 'd1': .., 's': ..}, ...]}, 'symbols': [...],
    'stype_in': ..., 'stype_out': ..., 'not_found': [...], 'partial': [...]}`) into this module's
    own plain dataclasses. Defensive: accepts either a real `dict` or any mapping-like object
    exposing the same keys (never asserts a specific vendor class), and never lets the raw
    object itself escape this function.
    """
    if hasattr(raw, "keys"):
        mapping: Mapping[str, Any] = raw
    else:  # pragma: no cover - defensive for an unexpected vendor return shape
        mapping = dict(raw) if raw else {}

    result_map = mapping.get("result", {}) or {}
    not_found = set(mapping.get("not_found", []) or [])

    resolutions = []
    for symbol in requested_symbols:
        entries = result_map.get(symbol, []) or []
        output_symbols = tuple(
            str(entry.get("s")) for entry in entries if isinstance(entry, Mapping) and entry.get("s")
        )
        found = bool(output_symbols) and symbol not in not_found
        resolutions.append(
            SymbologyResolution(
                raw_symbol=symbol,
                stype_in=stype_in,
                stype_out=stype_out,
                resolved_output_symbols=output_symbols,
                found=found,
            )
        )
    return SymbologyResolutionResult(
        dataset=dataset,
        stype_in=stype_in,
        stype_out=stype_out,
        start_date=start_date,
        end_date=end_date,
        resolutions=tuple(resolutions),
    )
