"""
market_truth.futures — GC actual-contract identity + provider->canonical symbology mapping.

Implements docs/architecture/hmt0-market-truth-v2/gc-futures-identity-and-roll.md §1: every raw
and canonical GC event permanently retains the actual traded-contract identity — never a bare `GC`
root. Provider symbols (e.g. `GCZ26`) are mappings/provenance, never the durable canonical identity
itself.

Mapping design note (judgment call — see final report)
--------------------------------------------------------
A single-digit-year provider symbol such as the raw wire form `GCZ6` is genuinely ambiguous across
decades with no further context (is it 2016 or 2026?) — resolving it correctly in a real system
needs the provider's own definitions/symbology capability (an explicit instrument-identity lookup
keyed by dataset/date), not month-code arithmetic on the bare string. Rather than guess a decade
from an under-specified symbol, this module implements the mapping as an EXPLICIT, VERSIONED,
HASHABLE TABLE (`ContractMappingTable`) loaded from an evidence-bearing fixture file — exactly the
"definitions/symbology" provider capability named in provider-abstraction.md, and exactly the
"explicit, deterministic, hashable, versioned, evidence-bearing" mapping this WO requires. Any
symbol absent from the table, or any malformed entry, fails closed (`ContractMappingError`) — this
module never algorithmically guesses a contract from an ambiguous symbol.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Tuple

CONTRACT_MAPPING_SCHEMA_VERSION = "hmt1-gc-contract-mapping-v1"

_VALID_MONTHS = frozenset(range(1, 13))
_MIN_YEAR = 1970
_MAX_YEAR = 2200


class ContractMappingError(ValueError):
    """Raised on any ambiguous/invalid/unknown provider->canonical contract mapping. Fail-closed:
    this is always raised, never guessed around."""


@dataclass(frozen=True)
class GcContractIdentity:
    """The durable, actual-contract identity for one GC delivery month/year on one venue.

    `canonical_id()` is the string every canonical event/partition path uses. It is intentionally
    never just `"GC"` — that bare-root form is rejected everywhere in this package (see
    `test_futures_identity.py::test_bare_root_is_never_a_valid_identity`).
    """

    delivery_year: int
    delivery_month: int
    venue: str = "COMEX"
    product_root: str = "GC"

    def __post_init__(self) -> None:
        if not isinstance(self.delivery_year, int) or isinstance(self.delivery_year, bool):
            raise ContractMappingError("delivery_year must be a plain int")
        if not (_MIN_YEAR <= self.delivery_year <= _MAX_YEAR):
            raise ContractMappingError(f"delivery_year {self.delivery_year} out of plausible range")
        if self.delivery_month not in _VALID_MONTHS:
            raise ContractMappingError(f"delivery_month {self.delivery_month} must be 1..12")
        if not self.venue:
            raise ContractMappingError("venue must be non-empty")
        if not self.product_root or self.product_root.strip() == "":
            raise ContractMappingError("product_root must be non-empty")

    def canonical_id(self) -> str:
        """e.g. `COMEX:GC:2026-12` — venue + product root + zero-padded delivery year/month. Never
        a bare root symbol; always fully qualifies the actual traded contract."""
        return f"{self.venue}:{self.product_root}:{self.delivery_year:04d}-{self.delivery_month:02d}"


@dataclass(frozen=True)
class ContractMappingTable:
    """An explicit, versioned, hashable provider->canonical GC contract mapping.

    `entries` maps a provider's raw symbol (exactly as that provider emits it, e.g. `"GCZ26"`) to
    the `GcContractIdentity` it denotes. Construction fails closed on a duplicate provider symbol
    mapping to two different contracts (an internally inconsistent table is never silently
    accepted).
    """

    version: str
    entries: Mapping[str, GcContractIdentity]

    def __post_init__(self) -> None:
        if not self.version:
            raise ContractMappingError("ContractMappingTable.version must be non-empty")
        if not self.entries:
            raise ContractMappingError("ContractMappingTable.entries must be non-empty")
        for symbol, contract in self.entries.items():
            if not symbol:
                raise ContractMappingError("provider symbol keys must be non-empty")
            if not isinstance(contract, GcContractIdentity):
                raise ContractMappingError(f"mapping target for {symbol!r} must be a GcContractIdentity")

    def resolve(self, provider_symbol: str) -> GcContractIdentity:
        """Resolve a raw provider symbol to its canonical contract identity. Fails closed
        (`ContractMappingError`) if the symbol is absent, empty, or a bare root — never guesses."""
        if not provider_symbol or not provider_symbol.strip():
            raise ContractMappingError("provider_symbol must be non-empty")
        if provider_symbol.strip().upper() in ("GC", "GC.FUT", "GC.V.0"):
            raise ContractMappingError(
                f"{provider_symbol!r} is a bare/continuous root, never a valid actual-contract mapping input"
            )
        try:
            return self.entries[provider_symbol]
        except KeyError as exc:
            raise ContractMappingError(f"no governed mapping for provider symbol {provider_symbol!r}") from exc

    def content_sha256(self) -> str:
        """Deterministic, evidence-bearing hash of the whole mapping table (version + every entry,
        sorted by provider symbol so entry insertion order never affects the hash)."""
        h = hashlib.sha256()
        h.update(self.version.encode("utf-8"))
        for symbol in sorted(self.entries):
            contract = self.entries[symbol]
            h.update(b"\x1f")
            h.update(symbol.encode("utf-8"))
            h.update(b"\x1f")
            h.update(contract.canonical_id().encode("utf-8"))
        return h.hexdigest()

    @classmethod
    def from_json_file(cls, path: Path) -> "ContractMappingTable":
        """Load a mapping table from an evidence-bearing JSON fixture of the shape::

            {
              "version": "hmt1-gc-contract-mapping-v1",
              "mappings": [
                {"provider_symbol": "GCZ26", "venue": "COMEX", "product_root": "GC",
                 "delivery_year": 2026, "delivery_month": 12}
              ]
            }

        Fails closed on a missing key, a duplicate provider symbol with a conflicting target, or
        any entry that does not construct a valid `GcContractIdentity`.
        """
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        try:
            version = raw["version"]
            mappings = raw["mappings"]
        except (KeyError, TypeError) as exc:
            raise ContractMappingError(f"malformed contract mapping file {path}: missing version/mappings") from exc

        entries: dict[str, GcContractIdentity] = {}
        for entry in mappings:
            try:
                provider_symbol = entry["provider_symbol"]
                contract = GcContractIdentity(
                    delivery_year=entry["delivery_year"],
                    delivery_month=entry["delivery_month"],
                    venue=entry.get("venue", "COMEX"),
                    product_root=entry.get("product_root", "GC"),
                )
            except (KeyError, TypeError) as exc:
                raise ContractMappingError(f"malformed contract mapping entry in {path}: {entry!r}") from exc

            if provider_symbol in entries and entries[provider_symbol] != contract:
                raise ContractMappingError(
                    f"conflicting duplicate mapping for {provider_symbol!r} in {path}: "
                    f"{entries[provider_symbol].canonical_id()} vs {contract.canonical_id()}"
                )
            entries[provider_symbol] = contract

        return cls(version=version, entries=entries)
