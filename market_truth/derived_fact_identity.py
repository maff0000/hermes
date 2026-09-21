"""
market_truth.derived_fact_identity — identity/provenance SKELETON ONLY.

Implements docs/architecture/hmt0-market-truth-v2/derived-fact-taxonomy-and-ownership.md §2's
8-field minimum contract every derived fact must eventually satisfy. This module defines ONLY that
identity/provenance shape.

*** THIS MODULE COMPUTES NO REAL P0 MICROSTRUCTURE FACT. ***

No volume-at-price, POC, VAH/VAL, LVN/HVN, delta, CVD, absorption, failed-auction, balance/
imbalance, or squeeze/trapped-flow computation exists anywhere in this file, this package, or this
work order. Computing any of those is explicitly out of scope for HMT-1 (see
docs/architecture/hmt0-market-truth-v2/hmt1-provisional-scope.md §2) and requires a separate,
later, separately-authorised work order. `test_fail_closed.py::test_derived_fact_module_has_no_forbidden_p0_fact_tokens`
statically guards this file against ever silently acquiring one, in the same spirit as this
repository's own existing `tests/test_stream_silent_stall_recovery.py::T11_NoForbiddenTokens` guard.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

DERIVED_FACT_IDENTITY_SCHEMA_VERSION = "hmt1-derived-fact-identity-skeleton-v1"


class DerivedFactIdentityValidationError(ValueError):
    """Raised when a derived-fact identity skeleton is constructed with invalid/incomplete data."""


@dataclass(frozen=True)
class DerivedFactIdentity:
    """The 8-field minimum identity/provenance contract (derived-fact-taxonomy-and-ownership.md
    §2) every derived fact must eventually carry. Field order below matches that document's §2
    numbering 1-8 exactly.
    """

    fact_schema_version: str          # §2.1
    algorithm_version: str            # §2.2
    parameter_set_hash: str           # §2.3
    source_event_set_identity: str    # §2.4
    session_calendar_identity: str    # §2.5
    roll_policy_identity: Optional[str]  # §2.6 — None/"N/A" when not applicable to this fact
    completeness_state: str           # §2.7
    evidence_lineage: str             # §2.8

    def __post_init__(self) -> None:
        required = (
            "fact_schema_version", "algorithm_version", "parameter_set_hash",
            "source_event_set_identity", "session_calendar_identity",
            "completeness_state", "evidence_lineage",
        )
        for name in required:
            if not getattr(self, name):
                raise DerivedFactIdentityValidationError(f"DerivedFactIdentity.{name} must be non-empty")
        if self.roll_policy_identity is not None and not self.roll_policy_identity.strip():
            raise DerivedFactIdentityValidationError(
                "roll_policy_identity must be None or a non-empty string — never an empty string standing in for absence"
            )
