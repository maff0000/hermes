"""HMT-2 (real-money checkpoint) — GC definitions-schema processing: outright/spread filtering
and provider-symbol -> canonical-contract mapping-table construction.

Pure, deterministic, stdlib-only. No network. No provider dependency (`databento` is NEVER
imported here — mirrors the HMT-2A/2B/2B.1 no-network-guard discipline exactly;
`tests/hmt2/test_no_network_guard.py` scans this whole package, this module included). The
real, retained `GC.FUT` parent `definition`-schema DBN artefact is read and translated into the
plain `DefinitionRecord` records this module consumes by a separate one-off script OUTSIDE this
package (`research/hmt2/generate_gc_definitions_v2.py`, which legitimately imports `databento`).

Reuses (does not reinvent) `market_truth.futures.GcContractIdentity` /
`ContractMappingTable` for the canonical-contract identity model.

PURPOSE: actual GC outright/spread identity verification and contract-mapping evidence — NOT
corpus-selection metadata, and NOT MBP-1/TBBO/trades/MBO data.

REAL-DATA CORRECTION (this checkpoint, discovered running `generate_gc_definitions_v2.py`
against the real, retained `GC.FUT` definitions artefact for the first time): the real
Databento `raw_symbol` wire form for a GC outright is `GC<month-code><ONE-digit-year-code>`
(e.g. `GCG8`, `GCZ6`) — NOT the `GC<month-code><TWO-digit-year>` shape (`GCZ26`) this module
originally assumed. Confirmed structurally, not just anecdotally: within this corpus's own
~9.3-year real acquisition window (2017-05-21..2026-09-19), 27 of the 120 distinct real outright
`raw_symbol` strings are already genuinely ambiguous — the SAME raw_symbol string (e.g. `GCF8`)
denotes TWO DIFFERENT real contracts roughly a decade apart (delivery 2018 vs. delivery 2028),
because a contract is listed in the definitions feed years before its own expiration. This is
exactly the ambiguity `market_truth.futures`'s own module docstring already anticipated and
disclaimed ("a single-digit-year provider symbol ... is genuinely ambiguous across decades ...
this module never algorithmically guesses"). The fix: `DefinitionRecord` now also carries the
real, structured, always-populated (0 nulls across all 70,016 real outright records this
checkpoint) `maturity_year`/`maturity_month` fields from the same definition-schema row, and
`build_contract_mapping_entries()` uses THOSE as the authoritative delivery-year/month source —
never the ambiguous year digit(s) in the wire symbol string. `parse_outright_symbol()` (the
original 2-digit-year regex parser) is kept, UNCHANGED, as a fallback ONLY for a record that
genuinely has no maturity fields at all (e.g. a synthetic/legacy caller), and
`parse_month_code_letter()` (new) is used purely as a defensive cross-check of the symbol's own
month-code letter against the authoritative `maturity_month` — never to derive the year.
`ContractMappingTable.entries` remains keyed by raw provider `raw_symbol` (an existing,
unmodified `market_truth.futures` design choice) — so the 27 real, genuinely-conflicting
raw_symbol collisions are correctly caught by the existing `CONFLICTING_DUPLICATE_MAPPING`
anomaly path (now a REAL, reachable code path, not merely defensive/future-proofing as this
module's own tests originally, incorrectly, asserted) and excluded from the table, never
silently guessed at. See the WO final report for the real, exact anomaly counts from this run.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence, Tuple

from market_truth.futures import GcContractIdentity

GC_DEFINITIONS_PROCESSING_VERSION = "hmt2-gc-definitions-processing-v2"

# Databento's `definition` schema field naming (real schema, cross-checked against the real
# retained artefact during this checkpoint — see research/hmt2/generate_gc_definitions_v2.py):
# `instrument_class` is a single-character code. `"F"` denotes a genuine outright future.
# Every other value (calendar spreads, etc.) is treated as NOT an outright, never guessed at
# more specifically than that — this module only needs the binary outright/not-outright split.
# Independently re-verified this checkpoint against the real, installed `databento_dbn==0.86.0`
# package: `InstrumentClass.FUTURE.value == "F"`.
OUTRIGHT_INSTRUMENT_CLASS = "F"

# Accepts EITHER a one-digit (real Databento wire form, e.g. `GCG8`) or two-digit (`GCZ26`)
# year-code shape — used ONLY to extract the month-code LETTER for a defensive cross-check
# against the authoritative `maturity_month` field (see module docstring); the year digit(s)
# captured here are NEVER used to derive delivery_year (genuinely ambiguous across decades for
# the one-digit real form).
_GC_OUTRIGHT_SYMBOL_ANY_YEAR_DIGITS_RE = re.compile(r"^GC([FGHJKMNQUVXZ])(\d{1,2})$")
# The original, UNCHANGED two-digit-year shape this module's `parse_outright_symbol()` parses
# into an actual (delivery_year, delivery_month) — retained as a fallback ONLY for a record that
# has no `maturity_year`/`maturity_month` at all (see `build_contract_mapping_entries`).
_GC_OUTRIGHT_SYMBOL_TWO_DIGIT_RE = re.compile(r"^GC([FGHJKMNQUVXZ])(\d{2})$")
_MONTH_CODE_TO_NUMBER = {
    "F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
    "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12,
}

# Fallback-path-only assumption (see `parse_outright_symbol` docstring): this module (and this
# whole corpus's real-acquisition range, 2017-05-21..2026-09-18) never crosses a century
# boundary, so a bare two-digit year unambiguously maps to 2000+YY in that narrow fallback path
# — see market_truth/futures.py's own module docstring for why bare month-code arithmetic is
# otherwise disclaimed as unreliable in general; this is a narrower, disclosed, range-scoped
# simplifying assumption, not a contradiction of that general caution. The PRIMARY path (real
# `maturity_year`/`maturity_month` fields) never uses this constant at all.
_CENTURY = 2000


class GcDefinitionsProcessingError(ValueError):
    """Raised on a structurally invalid definition record — fails closed, never guesses."""


@dataclass(frozen=True)
class DefinitionRecord:
    """One `GC.FUT` parent `definition`-schema record, translated into this module's own plain
    shape by the caller (never a vendor-typed/DataFrame-row object here).

    `maturity_year`/`maturity_month` (added this checkpoint): the real, structured,
    always-populated Databento definition-schema fields — the AUTHORITATIVE source of an
    outright's delivery year/month (see module docstring for why the wire `raw_symbol` string
    alone is not reliable for this). `None` only for a synthetic/legacy caller that never had
    these fields available; `build_contract_mapping_entries()` falls back to symbol-parsing in
    that case only.
    """

    raw_symbol: str
    instrument_class: str
    instrument_id: int
    expiration_utc: Optional[str] = None  # ISO date/datetime string, informational cross-check only
    maturity_year: Optional[int] = None
    maturity_month: Optional[int] = None


def is_outright(record: DefinitionRecord) -> bool:
    return record.instrument_class == OUTRIGHT_INSTRUMENT_CLASS


def partition_outrights_and_others(
    records: Sequence[DefinitionRecord],
) -> tuple[tuple[DefinitionRecord, ...], tuple[DefinitionRecord, ...]]:
    outrights = tuple(r for r in records if is_outright(r))
    others = tuple(r for r in records if not is_outright(r))
    return outrights, others


def parse_outright_symbol(raw_symbol: str) -> Optional[tuple[int, int]]:
    """Parses a plain `GC<month-code><2-digit-year>` outright symbol (e.g. `GCZ26`) into
    `(delivery_year, delivery_month)`, or `None` if `raw_symbol` does not match that exact shape
    (never guessed at, never raises — an unparseable symbol is reported by the caller as an
    excluded/anomalous entry, not silently skipped).

    UNCHANGED since before this checkpoint's real-data correction. This is deliberately NOT used
    to parse the real Databento one-digit-year wire form (`GCZ26` two-digit vs. real `GCZ6`
    one-digit) — see module docstring. Kept only as `build_contract_mapping_entries()`'s
    fallback for a record with no `maturity_year`/`maturity_month`.
    """
    match = _GC_OUTRIGHT_SYMBOL_TWO_DIGIT_RE.match(raw_symbol.strip().upper())
    if not match:
        return None
    month_code, two_digit_year = match.group(1), match.group(2)
    return _CENTURY + int(two_digit_year), _MONTH_CODE_TO_NUMBER[month_code]


def parse_month_code_letter(raw_symbol: str) -> Optional[str]:
    """Extracts ONLY the month-code letter from a `GC<month-code><1-or-2-digit-year-code>`
    outright symbol shape (accepts BOTH the real one-digit wire form and the two-digit form) —
    for a defensive cross-check against the authoritative `maturity_month` field. NEVER used to
    derive a delivery year (see module docstring: the year digit(s) here are genuinely ambiguous
    across decades for the real one-digit form). Returns `None` if the shape doesn't match at
    all; never raises."""
    if not raw_symbol:
        return None
    match = _GC_OUTRIGHT_SYMBOL_ANY_YEAR_DIGITS_RE.match(raw_symbol.strip().upper())
    if not match:
        return None
    return match.group(1)


@dataclass(frozen=True)
class ContractMappingBuildResult:
    entries: Mapping[str, GcContractIdentity]
    anomalies: tuple[dict, ...]  # unparseable / cross-check-mismatch / conflicting-duplicate symbols


def _authoritative_delivery_year_month(record: DefinitionRecord) -> Tuple[Optional[tuple], Optional[str]]:
    """Returns `((delivery_year, delivery_month), None)` on success, or `(None, anomaly_reason)`
    on failure. PRIMARY source: `record.maturity_year`/`maturity_month` (real, authoritative —
    see module docstring). FALLBACK (only when both are `None`, e.g. a synthetic/legacy caller):
    `parse_outright_symbol(record.raw_symbol)` — the original, unchanged two-digit-year parser.
    """
    if record.maturity_year is not None and record.maturity_month is not None:
        return (record.maturity_year, record.maturity_month), None
    parsed = parse_outright_symbol(record.raw_symbol)
    if parsed is None:
        return None, "UNPARSEABLE_OUTRIGHT_SYMBOL_SHAPE"
    return parsed, None


def build_contract_mapping_entries(
    outright_records: Sequence[DefinitionRecord],
) -> ContractMappingBuildResult:
    """Builds provider-symbol -> `GcContractIdentity` entries for every outright record whose
    delivery year/month can be determined (see `_authoritative_delivery_year_month`).

    Cross-checks, each capable of excluding a record as an anomaly rather than silently trusting
    it:
      1. If the symbol also parses under `parse_month_code_letter()`, its month-code LETTER must
         agree with the authoritative `maturity_month` — a mismatch is a genuine data anomaly
         (`SYMBOL_MONTH_CODE_MISMATCH`), never silently resolved in favour of one or the other.
      2. `expiration_utc`'s own year, when given, must be within +-1 calendar year of the
         delivery year (a December contract expiring in early January of the following year is
         expected and tolerated) — `EXPIRATION_YEAR_CROSS_CHECK_MISMATCH` otherwise.
      3. A duplicate `raw_symbol` mapping to two DIFFERENT contracts is flagged
         (`CONFLICTING_DUPLICATE_MAPPING`) and PERMANENTLY excluded from the table — this module
         never arbitrarily picks one, and never lets a later record for the same symbol quietly
         re-populate the table after a conflict was detected (a `raw_symbol` once flagged
         ambiguous is `poisoned` for the rest of this call — see the real, corrected bug-fix
         disclosure below). This is a REAL, reachable path for the actual Databento one-digit-
         year wire form (see module docstring) — 27 real raw_symbols hit this in the real corpus
         this checkpoint processed.

    BUG FIX (this checkpoint, disclosed): an earlier version of this function popped a
    conflicting symbol out of `entries` on detecting the conflict, but did not prevent a LATER
    record for that same symbol from being silently added back in afterwards (since, once
    popped, `raw_symbol in entries` was false again) — for a real symbol with many repeated
    definition-feed republication rows across two different delivery periods, this could leave
    an order-dependent, silently-resolved entry in the table for a genuinely ambiguous symbol,
    contradicting this function's own stated guarantee. Fixed with an explicit `poisoned` set:
    once a symbol is flagged conflicting, no subsequent record for it is ever added to `entries`
    again, regardless of row order.
    """
    entries: dict[str, GcContractIdentity] = {}
    poisoned: set = set()
    anomalies: list[dict] = []

    for record in outright_records:
        parsed, reason = _authoritative_delivery_year_month(record)
        if parsed is None:
            anomalies.append({"raw_symbol": record.raw_symbol, "reason": reason})
            continue
        delivery_year, delivery_month = parsed

        month_code = parse_month_code_letter(record.raw_symbol)
        if month_code is not None and _MONTH_CODE_TO_NUMBER[month_code] != delivery_month:
            anomalies.append(
                {
                    "raw_symbol": record.raw_symbol,
                    "reason": "SYMBOL_MONTH_CODE_MISMATCH",
                    "symbol_month_code": month_code,
                    "maturity_month": delivery_month,
                }
            )
            continue

        if record.expiration_utc:
            try:
                expiration_year = int(record.expiration_utc[:4])
            except (ValueError, TypeError, IndexError):
                expiration_year = None
            if expiration_year is not None and abs(expiration_year - delivery_year) > 1:
                anomalies.append(
                    {
                        "raw_symbol": record.raw_symbol,
                        "reason": "EXPIRATION_YEAR_CROSS_CHECK_MISMATCH",
                        "parsed_delivery_year": delivery_year,
                        "expiration_year": expiration_year,
                    }
                )
                continue

        contract = GcContractIdentity(delivery_year=delivery_year, delivery_month=delivery_month)

        if record.raw_symbol in poisoned:
            # Already known ambiguous from an earlier record — permanently excluded; every
            # further occurrence is silently skipped (the anomaly was already logged once at
            # the moment of first detection, below), never re-added.
            continue

        if record.raw_symbol in entries and entries[record.raw_symbol] != contract:
            anomalies.append(
                {
                    "raw_symbol": record.raw_symbol,
                    "reason": "CONFLICTING_DUPLICATE_MAPPING",
                    "existing": entries[record.raw_symbol].canonical_id(),
                    "conflicting": contract.canonical_id(),
                }
            )
            entries.pop(record.raw_symbol, None)
            poisoned.add(record.raw_symbol)
            continue
        entries[record.raw_symbol] = contract

    return ContractMappingBuildResult(entries=entries, anomalies=tuple(anomalies))
