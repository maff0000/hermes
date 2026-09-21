"""
market_truth.replay — deterministic replay harness.

Implements the WO §14 pipeline end to end: fixture bytes -> hash/validate -> fixture provider ->
contract mapping -> canonicaliser -> canonical events -> stable event IDs -> partition writer ->
evidence manifest -> reload partition -> canonical event reconstruction -> exact comparison.

This is the module `test_replay_determinism.py` and the WO §18 "running proof" both drive directly
— a real, runnable pipeline, not just isolated unit tests.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

from market_truth.canonicaliser import CANONICALISER_VERSION, Canonicaliser
from market_truth.contracts import MARKET_EVENT_CONTRACT_SCHEMA_VERSION
from market_truth.evidence import EVIDENCE_MANIFEST_VERSION, EvidenceManifest
from market_truth.futures import ContractMappingTable
from market_truth.identity import EVENT_IDENTITY_ALGORITHM_VERSION
from market_truth.partition import (
    PARTITION_CONTRACT_VERSION,
    PARQUET_WRITER_LIBRARY,
    PartitionReader,
    PartitionWriter,
    event_family_name,
    event_sort_key,
    serialize_event_row,
)
from market_truth.providers.fixture import FIXTURE_LINE_SCHEMA_VERSION, FIXTURE_PROVIDER_VERSION, FixtureMarketDataProvider

import pyarrow as pa


def compute_event_set_hash(events: Sequence[object]) -> str:
    """Canonical event-set hash: SHA-256 over every event's row bytes, sorted by the bytes
    themselves (not by input order) so the hash never depends on canonicalisation/emission order."""
    h = hashlib.sha256()
    for row_bytes in sorted(serialize_event_row(e) for e in events):
        h.update(len(row_bytes).to_bytes(4, "big"))
        h.update(row_bytes)
    return h.hexdigest()


@dataclass(frozen=True)
class ReplayRunResult:
    events: Tuple[object, ...]
    partition_results: Tuple[object, ...]
    manifest: EvidenceManifest
    source_record_count: int


def run_pipeline(
    fixture_paths: Sequence[Path],
    mapping_table_path: Path,
    output_root: Path,
    *,
    provider_id: str,
    dataset_id: str,
    canonicaliser_version: str = CANONICALISER_VERSION,
    schema_version: str = MARKET_EVENT_CONTRACT_SCHEMA_VERSION,
) -> ReplayRunResult:
    """Run the full HMT-1 pipeline once, from governed fixture bytes to a written research
    partition and an evidence manifest. A fresh `Canonicaliser` is constructed on every call, so
    two calls (e.g. run A / run B) never share top-of-book/dedupe state."""
    mapping_table = ContractMappingTable.from_json_file(Path(mapping_table_path))
    canonicaliser = Canonicaliser(
        mapping_table=mapping_table,
        canonicaliser_version=canonicaliser_version,
        schema_version=schema_version,
    )

    all_events: List[object] = []
    fixture_hashes: Dict[str, str] = {}
    quality_counter: Counter = Counter()
    record_count = 0

    for fixture_path in fixture_paths:
        provider = FixtureMarketDataProvider(Path(fixture_path), provider_id=provider_id, dataset_id=dataset_id)
        fixture_hashes[Path(fixture_path).name] = provider.content_sha256()
        for record in provider.iter_records():
            record_count += 1
            for event in canonicaliser.canonicalise(record):
                all_events.append(event)
                quality_counter[event.quality_state.value] += 1

    writer = PartitionWriter(Path(output_root))
    partition_results = writer.write(all_events)

    event_counts = Counter(event_family_name(e) for e in all_events)
    event_set_hash = compute_event_set_hash(all_events)

    if partition_results:
        writer_version = partition_results[0].writer_version
        writer_config_id = partition_results[0].writer_config_id
    else:
        writer_version = pa.__version__
        writer_config_id = PartitionWriter.WRITER_CONFIG_ID

    manifest = EvidenceManifest(
        manifest_version=EVIDENCE_MANIFEST_VERSION,
        source_fixture_hashes=fixture_hashes,
        fixture_schema_version=FIXTURE_LINE_SCHEMA_VERSION,
        provider_adapter_version=FIXTURE_PROVIDER_VERSION,
        contract_mapping_version=mapping_table.version,
        contract_mapping_hash=mapping_table.content_sha256(),
        canonical_schema_version=schema_version,
        canonicaliser_version=canonicaliser_version,
        event_identity_algorithm_version=EVENT_IDENTITY_ALGORITHM_VERSION,
        partition_contract_version=PARTITION_CONTRACT_VERSION,
        writer_library=PARQUET_WRITER_LIBRARY,
        writer_version=writer_version,
        writer_config_id=writer_config_id,
        source_record_count=record_count,
        canonical_event_counts_by_family=dict(event_counts),
        canonical_event_set_hash=event_set_hash,
        partition_content_hashes={r.relative_path: r.partition_content_sha256 for r in partition_results},
        artifact_hashes={r.relative_path: r.artifact_sha256 for r in partition_results},
        provenance_quality_summary=dict(quality_counter),
    )

    return ReplayRunResult(
        events=tuple(all_events),
        partition_results=tuple(partition_results),
        manifest=manifest,
        source_record_count=record_count,
    )


@dataclass(frozen=True)
class ReplayComparison:
    event_count_match: bool
    identity_list_match: bool
    event_content_match: bool
    event_set_hash_match: bool
    partition_content_hash_match: bool
    artifact_hash_match: bool
    manifest_deterministic_match: bool
    reload_reconstruction_match: bool
    details: Mapping[str, str]

    @property
    def all_match(self) -> bool:
        return all(
            [
                self.event_count_match,
                self.identity_list_match,
                self.event_content_match,
                self.event_set_hash_match,
                self.partition_content_hash_match,
                self.artifact_hash_match,
                self.manifest_deterministic_match,
                self.reload_reconstruction_match,
                # artifact_hash_match is now REQUIRED for all_match: under the governed, pinned
                # HMT-1 writer contract (pyarrow==17.0.0, zstd-default-level-v1), physical Parquet
                # artifact identity must reproduce exactly, same as the semantic
                # `partition_content_sha256`. A mismatch here is a real regression and must make
                # the aggregate acceptance property false so it is caught, not silently reported.
            ]
        )


def _reload_all_events(root: Path, partition_results: Sequence[object]) -> List[object]:
    reader = PartitionReader(Path(root))
    events: List[object] = []
    for result in partition_results:
        events.extend(reader.read_events(result.relative_dir, result.event_family))
    return events


def compare_runs(a: ReplayRunResult, b: ReplayRunResult, root_a: Path, root_b: Path) -> ReplayComparison:
    details: Dict[str, str] = {}

    event_count_match = len(a.events) == len(b.events)
    details["event_count_a"] = str(len(a.events))
    details["event_count_b"] = str(len(b.events))

    ids_a = [e.identity_hash for e in sorted(a.events, key=event_sort_key)]
    ids_b = [e.identity_hash for e in sorted(b.events, key=event_sort_key)]
    identity_list_match = ids_a == ids_b

    rows_a = sorted(serialize_event_row(e) for e in a.events)
    rows_b = sorted(serialize_event_row(e) for e in b.events)
    event_content_match = rows_a == rows_b

    event_set_hash_match = a.manifest.canonical_event_set_hash == b.manifest.canonical_event_set_hash
    details["event_set_hash_a"] = a.manifest.canonical_event_set_hash
    details["event_set_hash_b"] = b.manifest.canonical_event_set_hash

    partition_content_hash_match = dict(a.manifest.partition_content_hashes) == dict(b.manifest.partition_content_hashes)
    artifact_hash_match = dict(a.manifest.artifact_hashes) == dict(b.manifest.artifact_hashes)
    details["artifact_hashes_a"] = repr(dict(a.manifest.artifact_hashes))
    details["artifact_hashes_b"] = repr(dict(b.manifest.artifact_hashes))

    manifest_deterministic_match = a.manifest.deterministic_fields_sha256() == b.manifest.deterministic_fields_sha256()
    details["manifest_deterministic_hash_a"] = a.manifest.deterministic_fields_sha256()
    details["manifest_deterministic_hash_b"] = b.manifest.deterministic_fields_sha256()

    reloaded_a = _reload_all_events(root_a, a.partition_results)
    reloaded_b = _reload_all_events(root_b, b.partition_results)
    reloaded_rows_a = sorted(serialize_event_row(e) for e in reloaded_a)
    reloaded_rows_b = sorted(serialize_event_row(e) for e in reloaded_b)
    reload_reconstruction_match = reloaded_rows_a == rows_a and reloaded_rows_b == rows_b and reloaded_rows_a == reloaded_rows_b

    return ReplayComparison(
        event_count_match=event_count_match,
        identity_list_match=identity_list_match,
        event_content_match=event_content_match,
        event_set_hash_match=event_set_hash_match,
        partition_content_hash_match=partition_content_hash_match,
        artifact_hash_match=artifact_hash_match,
        manifest_deterministic_match=manifest_deterministic_match,
        reload_reconstruction_match=reload_reconstruction_match,
        details=details,
    )


def replay_twice(
    fixture_paths: Sequence[Path],
    mapping_table_path: Path,
    root_a: Path,
    root_b: Path,
    *,
    provider_id: str,
    dataset_id: str,
    **kwargs,
) -> Tuple[ReplayRunResult, ReplayRunResult, ReplayComparison]:
    """Run the pipeline twice, into two separate disposable roots, and compare every WO §14
    dimension. This is "run A == run B" made concrete and runnable."""
    result_a = run_pipeline(fixture_paths, mapping_table_path, root_a, provider_id=provider_id, dataset_id=dataset_id, **kwargs)
    result_b = run_pipeline(fixture_paths, mapping_table_path, root_b, provider_id=provider_id, dataset_id=dataset_id, **kwargs)
    comparison = compare_runs(result_a, result_b, root_a, root_b)
    return result_a, result_b, comparison
