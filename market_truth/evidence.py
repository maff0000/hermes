"""
market_truth.evidence — versioned evidence manifest.

Implements docs/architecture/hmt0-market-truth-v2/deterministic-replay-and-evidence.md: a manifest
whose deterministic fields must be bit-identical across two honest replays of the same governed
input. No dynamic wall-clock timestamp may contaminate any deterministic identity/hash field (WO
§13) — `audit_generated_at` is carried as a clearly separate, explicitly non-deterministic field,
and is excluded from `deterministic_fields_bytes()` below.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Tuple

from market_truth.identity import encode_field

EVIDENCE_MANIFEST_VERSION = "hmt1-evidence-manifest-v1"
FIXTURE_LINE_SCHEMA_VERSION_DEFAULT = "hmt1-fixture-line-schema-v1"


@dataclass(frozen=True)
class EvidenceManifest:
    manifest_version: str
    source_fixture_hashes: Mapping[str, str]  # fixture file name -> sha256 of its raw bytes
    fixture_schema_version: str
    provider_adapter_version: str
    contract_mapping_version: str
    contract_mapping_hash: str
    canonical_schema_version: str
    canonicaliser_version: str
    event_identity_algorithm_version: str
    partition_contract_version: str
    writer_library: str
    writer_version: str
    writer_config_id: str
    source_record_count: int
    canonical_event_counts_by_family: Mapping[str, int]
    canonical_event_set_hash: str
    partition_content_hashes: Mapping[str, str]  # relative_path -> partition_content_sha256
    artifact_hashes: Mapping[str, str]  # relative_path -> artifact_sha256
    provenance_quality_summary: Mapping[str, int]  # quality_state value -> count
    audit_generated_at: Optional[str] = None  # human-convenience only; NEVER identity-bearing

    def deterministic_fields_bytes(self) -> bytes:
        """Canonical, length-prefixed serialization of every field EXCEPT `audit_generated_at`.
        Two replays of the same governed input must produce identical bytes here."""
        parts = [
            self.manifest_version,
            self.fixture_schema_version,
            self.provider_adapter_version,
            self.contract_mapping_version,
            self.contract_mapping_hash,
            self.canonical_schema_version,
            self.canonicaliser_version,
            self.event_identity_algorithm_version,
            self.partition_contract_version,
            self.writer_library,
            self.writer_version,
            self.writer_config_id,
            str(self.source_record_count),
            self.canonical_event_set_hash,
        ]
        encoded = [encode_field(p) for p in parts]

        for mapping in (
            self.source_fixture_hashes,
            self.canonical_event_counts_by_family,
            self.partition_content_hashes,
            self.artifact_hashes,
            self.provenance_quality_summary,
        ):
            for k in sorted(mapping):
                encoded.append(encode_field(str(k)))
                encoded.append(encode_field(str(mapping[k])))

        return b"".join(encoded)

    def deterministic_fields_sha256(self) -> str:
        return hashlib.sha256(self.deterministic_fields_bytes()).hexdigest()

    def to_json_dict(self) -> dict:
        return {
            "manifest_version": self.manifest_version,
            "source_fixture_hashes": dict(sorted(self.source_fixture_hashes.items())),
            "fixture_schema_version": self.fixture_schema_version,
            "provider_adapter_version": self.provider_adapter_version,
            "contract_mapping_version": self.contract_mapping_version,
            "contract_mapping_hash": self.contract_mapping_hash,
            "canonical_schema_version": self.canonical_schema_version,
            "canonicaliser_version": self.canonicaliser_version,
            "event_identity_algorithm_version": self.event_identity_algorithm_version,
            "partition_contract_version": self.partition_contract_version,
            "writer_library": self.writer_library,
            "writer_version": self.writer_version,
            "writer_config_id": self.writer_config_id,
            "source_record_count": self.source_record_count,
            "canonical_event_counts_by_family": dict(sorted(self.canonical_event_counts_by_family.items())),
            "canonical_event_set_hash": self.canonical_event_set_hash,
            "partition_content_hashes": dict(sorted(self.partition_content_hashes.items())),
            "artifact_hashes": dict(sorted(self.artifact_hashes.items())),
            "provenance_quality_summary": dict(sorted(self.provenance_quality_summary.items())),
            "deterministic_fields_sha256": self.deterministic_fields_sha256(),
            "audit_generated_at": self.audit_generated_at,
        }
