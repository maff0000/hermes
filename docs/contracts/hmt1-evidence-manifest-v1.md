# HMT-1 — Evidence manifest contract (`hmt1-evidence-manifest-v1`)

**Status:** IMPLEMENTED (HMT-1). Matches `market_truth/evidence.py` exactly.

## Purpose

Implements `docs/architecture/hmt0-market-truth-v2/deterministic-replay-and-evidence.md`: a
versioned manifest whose deterministic fields must be bit-identical across two honest replays of
the same governed input, with no dynamic wall-clock timestamp contaminating any identity/hash
field.

## Fields

| Field | Meaning | In `deterministic_fields_sha256()`? |
|---|---|---|
| `manifest_version` | `hmt1-evidence-manifest-v1` | yes |
| `source_fixture_hashes` | fixture file name -> SHA-256 of its raw bytes | yes |
| `fixture_schema_version` | `hmt1-fixture-line-schema-v1` | yes |
| `provider_adapter_version` | `hmt1-fixture-provider-v1` | yes |
| `contract_mapping_version` / `contract_mapping_hash` | mapping table version + `ContractMappingTable.content_sha256()` | yes |
| `canonical_schema_version` | `contracts.MARKET_EVENT_CONTRACT_SCHEMA_VERSION` (or an override passed to `replay.run_pipeline`) | yes |
| `canonicaliser_version` | `canonicaliser.CANONICALISER_VERSION` (or override) | yes |
| `event_identity_algorithm_version` | `identity.EVENT_IDENTITY_ALGORITHM_VERSION` | yes |
| `partition_contract_version` | `hmt1-research-partition-contract-v1` | yes |
| `writer_library` / `writer_version` / `writer_config_id` | `pyarrow` / the pinned installed version / `zstd-default-level-v1` | yes |
| `source_record_count` | raw records consumed across all fixture files in this run | yes |
| `canonical_event_counts_by_family` | `market_trade`/`top_of_book`/`market_quote` -> count | yes |
| `canonical_event_set_hash` | `replay.compute_event_set_hash()` — SHA-256 over every event's row bytes, sorted by the bytes themselves | yes |
| `partition_content_hashes` / `artifact_hashes` | relative partition path -> semantic / physical hash | yes |
| `provenance_quality_summary` | `EventQualityState` value -> count | yes |
| `audit_generated_at` | human-convenience wall-clock stamp, set by the caller if desired | **NO — explicitly excluded** |

## Determinism guarantee

`EvidenceManifest.deterministic_fields_bytes()` serializes every field above except
`audit_generated_at` using the same length-prefixed encoding as `identity.py`/`partition.py`, with
every mapping's keys sorted before encoding. `test_evidence_manifest.py` proves: (a) two replays of
the same governed input produce an identical `deterministic_fields_sha256()`; (b) stamping
`audit_generated_at` with an arbitrary wall-clock value never changes that hash; (c) changing
`canonicaliser_version`, `schema_version`, or the contract-mapping version changes it (WO
acceptance #21) while leaving individual event identities untouched (identity is about *what the
event is*, not the governed shape/version that produced it).
