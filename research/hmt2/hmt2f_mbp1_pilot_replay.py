#!/usr/bin/env python3
"""HMT-2 (real-money checkpoint) — Part 5: one full pipeline run over the two real, retained
MBP-1 pilot artefacts — adapter -> canonicaliser -> partition -> evidence manifest -> reload ->
row-level reconstruction check. Reuses `market_truth.canonicaliser`, `market_truth.futures`,
`market_truth.identity`, `market_truth.partition`, `market_truth.evidence`, and
`market_truth.replay.compute_event_set_hash` completely unchanged — only the PROVIDER
(`market_truth.providers.databento_mbp1.DatabentoMbp1PilotProvider`) is new, mirroring
`market_truth.replay.run_pipeline`'s own structure (which is hardcoded to the fixture provider)
for a genuine retained-artefact provider instead.

Zero network access, no credential read: only ALREADY-RETAINED local bytes are read (via
`DatabentoMbp1PilotProvider` -> `iter_retained_mbp1_records()`, itself a local, read-only
`databento.DBNStore.from_file()` decode — never `.get_range`, never `databento.Historical`,
never `load_databento_api_key`). This is what makes it safe to run this script 3+ times, in
separate subprocesses, with no risk of any further billable request.

Each invocation is ONE full run, writing its own disposable Parquet/Zstd research-partition tree
under `--output-root` and a JSON run-summary (every dimension needed for a cross-run determinism
comparison — see `hmt2g_mbp1_pilot_replay_compare.py`) to `--summary-path`.

Run (repeat 3x with a different PYTHONHASHSEED, each a genuinely separate OS process):

    PYTHONHASHSEED=0 /tmp/hmt2-work-venv/bin/python3 research/hmt2/hmt2f_mbp1_pilot_replay.py \\
        --output-root /tmp/hmt2-mbp1-replay/run1 --summary-path /tmp/hmt2-mbp1-replay/run1.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pyarrow as pa  # noqa: E402

from market_truth.canonicaliser import CANONICALISER_VERSION, Canonicaliser  # noqa: E402
from market_truth.contracts import MARKET_EVENT_CONTRACT_SCHEMA_VERSION  # noqa: E402
from market_truth.evidence import EVIDENCE_MANIFEST_VERSION, EvidenceManifest  # noqa: E402
from market_truth.futures import ContractMappingTable  # noqa: E402
from market_truth.identity import EVENT_IDENTITY_ALGORITHM_VERSION  # noqa: E402
from market_truth.partition import (  # noqa: E402
    PARQUET_WRITER_LIBRARY,
    PARTITION_CONTRACT_VERSION,
    PartitionReader,
    PartitionWriter,
    event_family_name,
    serialize_event_row,
)
from market_truth.providers.databento_mbp1 import (  # noqa: E402
    DATABENTO_MBP1_PROVIDER_VERSION,
    DatabentoMbp1PilotProvider,
)
from market_truth.replay import compute_event_set_hash  # noqa: E402
from market_truth.acquisition.hmt2_slice_guard import require_hmt2_slice  # noqa: E402

_SCRIPT_NAME = os.path.basename(__file__)

MAPPING_TABLE_PATH = os.path.join(_THIS_DIR, "gc-contract-mapping-table-v2.json")
CATALOGUE_PATH = os.path.join(
    _REPO_ROOT, "research-source", "hmt2-gc-mbp1-v1", "manifest", "mbp1_pilot_acquisition_catalogue.json"
)

PILOT_SESSION_IDS = ["GC-2019-03-29", "GC-2019-03-22"]  # SCHEDULED_EVENT, MATCHED_CONTROL
PROVIDER_ID = "databento"
DATASET_ID = "GLBX.MDP3"


def _artefact_path_for(session_id: str) -> str:
    return os.path.join(
        _REPO_ROOT, "research-source", "hmt2-gc-mbp1-v1", "sessions", session_id, "source", f"gc_mbp1_{session_id}.dbn.zst"
    )


def _load_catalogue_by_session() -> dict:
    with open(CATALOGUE_PATH, "r", encoding="utf-8") as f:
        catalogue = json.load(f)
    return {record["session_id"]: record for record in catalogue.values()}


def run(output_root: str, summary_path: str) -> dict:
    catalogue_by_session = _load_catalogue_by_session()
    mapping_table = ContractMappingTable.from_json_file(Path(MAPPING_TABLE_PATH))
    canonicaliser = Canonicaliser(mapping_table=mapping_table)

    all_events = []
    source_hashes = {}
    record_counts_by_session = {}
    quality_by_session = {}
    quality_counter: Counter = Counter()
    record_count = 0

    for session_id in PILOT_SESSION_IDS:
        evidence = catalogue_by_session[session_id]
        path = _artefact_path_for(session_id)
        provider = DatabentoMbp1PilotProvider(
            retained_path=path,
            session_id=session_id,
            acquisition_epoch=evidence["request_identity"],
            provider_id=PROVIDER_ID,
            dataset_id=DATASET_ID,
        )
        actual_sha256 = provider.content_sha256()
        if actual_sha256 != evidence["sha256"]:
            raise RuntimeError(
                f"{session_id}: retained artefact sha256 drift — evidence={evidence['sha256']} "
                f"actual={actual_sha256}. Refusing to canonicalise possibly-corrupted bytes."
            )
        source_hashes[session_id] = actual_sha256

        session_record_count = 0
        for record in provider.iter_records():
            session_record_count += 1
            record_count += 1
            for event in canonicaliser.canonicalise(record):
                all_events.append(event)
                quality_counter[event.quality_state.value] += 1
        record_counts_by_session[session_id] = session_record_count
        quality_by_session[session_id] = provider.quality_counters.to_dict()

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
        source_fixture_hashes=source_hashes,
        fixture_schema_version=DATABENTO_MBP1_PROVIDER_VERSION,
        provider_adapter_version=DATABENTO_MBP1_PROVIDER_VERSION,
        contract_mapping_version=mapping_table.version,
        contract_mapping_hash=mapping_table.content_sha256(),
        canonical_schema_version=MARKET_EVENT_CONTRACT_SCHEMA_VERSION,
        canonicaliser_version=CANONICALISER_VERSION,
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

    # Reload check — reconstruct canonical events straight back from the written Parquet
    # partitions and confirm every row byte-serializes identically to the in-memory events.
    reader = PartitionReader(Path(output_root))
    reloaded_rows_hex = []
    for r in partition_results:
        for event in reader.read_events(r.relative_dir, r.event_family):
            reloaded_rows_hex.append(serialize_event_row(event).hex())

    live_rows_hex = sorted(serialize_event_row(e).hex() for e in all_events)
    reloaded_rows_hex.sort()

    summary = {
        "pythonhashseed": os.environ.get("PYTHONHASHSEED"),
        "pid": os.getpid(),
        "source_hashes": dict(sorted(source_hashes.items())),
        "record_counts_by_session": dict(sorted(record_counts_by_session.items())),
        "source_record_count": record_count,
        "canonical_event_counts_by_family": dict(sorted(event_counts.items())),
        "canonical_event_set_hash": event_set_hash,
        "identity_hashes_sorted": sorted(e.identity_hash for e in all_events),
        "event_rows_sorted_hex": live_rows_hex,
        "reload_reconstructed_rows_sorted_hex": reloaded_rows_hex,
        "reload_reconstruction_matches_live": reloaded_rows_hex == live_rows_hex,
        "partition_content_hashes": dict(sorted(manifest.partition_content_hashes.items())),
        "artifact_hashes": dict(sorted(manifest.artifact_hashes.items())),
        "manifest_deterministic_fields_sha256": manifest.deterministic_fields_sha256(),
        "quality_by_session": quality_by_session,
        "writer_version": writer_version,
        "writer_config_id": writer_config_id,
    }

    os.makedirs(os.path.dirname(summary_path), exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
        f.write("\n")

    return summary


def main() -> None:
    # HMT2 SLICE CONTAINMENT GUARD -- must be the first thing that happens in this real-data
    # entry point's main(), before any file I/O against retained corpus data. See
    # market_truth.acquisition.hmt2_slice_guard for why.
    require_hmt2_slice(script_name=_SCRIPT_NAME)

    ap = argparse.ArgumentParser()
    ap.add_argument("--output-root", required=True)
    ap.add_argument("--summary-path", required=True)
    args = ap.parse_args()
    summary = run(args.output_root, args.summary_path)
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k not in ("event_rows_sorted_hex", "reload_reconstructed_rows_sorted_hex", "identity_hashes_sorted")},
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
