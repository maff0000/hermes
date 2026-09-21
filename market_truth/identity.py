"""
market_truth.identity — the one governed event-identity algorithm.

Implements docs/architecture/hmt0-market-truth-v2/canonical-market-events.md +
deterministic-replay-and-evidence.md: a single SHA-256 identity over a versioned, explicit,
length-prefixed serialization of exactly the fields that identify *what the event is* — never a
field that could differ between two honest replays of the same governed input (no current time, no
PID, no object identity, no filesystem path, no random UUID, no replay execution time — none of
these fields even exist on `EventIdentityMaterial`, so there is nothing to accidentally include).

The same governed observation must receive the identical canonical event identity on every replay
(see `test_event_identity.py::test_identical_material_produces_identical_identity`).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

EVENT_IDENTITY_ALGORITHM_VERSION = "hmt1-event-identity-sha256-v1"

_FIELD_SEPARATOR_NOTE = (
    "Fields are length-prefixed (4-byte big-endian byte length + UTF-8 bytes), not delimiter-"
    "joined, so no field value could ever be crafted to collide with a separator character."
)


def encode_field(value: Optional[str]) -> bytes:
    """Length-prefix one field for a canonical serialization. `None` gets a reserved sentinel
    length (0xFFFFFFFF) so it can never collide with a genuine, if empty, string field. Shared by
    `EventIdentityMaterial.canonical_serialization()` below and by `partition.py`'s canonical row
    serialization, so the whole package has exactly one length-prefixed encoding convention."""
    if value is None:
        return (0xFFFFFFFF).to_bytes(4, "big")
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(4, "big") + encoded


@dataclass(frozen=True)
class EventIdentityMaterial:
    """Exactly the fields required to identify a canonical event (WO §6). Deliberately excludes
    canonicaliser_version/schema_version (provenance about *shape*, not identity of *what*) and
    hermes_receive_time (wall clock — see provenance.py module docstring).
    """

    event_family: str
    provider_id: str
    dataset_id: str
    source_artifact_id: str
    canonical_instrument_id: str
    source_record_identity: str
    source_sequence: Optional[int]
    sequence_domain: str
    source_event_time_text: str
    event_ordinal: int
    algorithm_version: str = EVENT_IDENTITY_ALGORITHM_VERSION

    def canonical_serialization(self) -> bytes:
        """Explicit, fixed field order. Never relies on dict/JSON key-ordering behaviour."""
        parts = [
            self.algorithm_version,
            self.event_family,
            self.provider_id,
            self.dataset_id,
            self.source_artifact_id,
            self.canonical_instrument_id,
            self.source_record_identity,
            None if self.source_sequence is None else str(self.source_sequence),
            self.sequence_domain,
            self.source_event_time_text,
            str(self.event_ordinal),
        ]
        return b"".join(encode_field(p) for p in parts)


def compute_event_identity(material: EventIdentityMaterial) -> str:
    """The one governed identity algorithm: SHA-256 hex digest of `material`'s canonical
    serialization."""
    return hashlib.sha256(material.canonical_serialization()).hexdigest()
