"""HMT-1 — partition.py: research-partition writer/reader, semantic vs physical determinism."""
from pathlib import Path

from market_truth.canonicaliser import Canonicaliser
from market_truth.contracts import MarketTradeEvent, TopOfBookEvent
from market_truth.futures import ContractMappingTable
from market_truth.partition import PartitionReader, PartitionWriter, serialize_event_row
from market_truth.providers.fixture import FixtureMarketDataProvider

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "hmt1"
MAPPING_PATH = FIXTURES_DIR / "gc_contract_mapping_v1.json"


def _events():
    mapping = ContractMappingTable.from_json_file(MAPPING_PATH)
    canonicaliser = Canonicaliser(mapping_table=mapping)
    provider = FixtureMarketDataProvider(
        FIXTURES_DIR / "gc_mbp1_equivalent_v1.jsonl", provider_id="hmt1-fixture-provider", dataset_id="hmt1-test"
    )
    out = []
    for record in provider.iter_records():
        out.extend(canonicaliser.canonicalise(record))
    return out


def test_write_produces_the_documented_partition_layout(tmp_path):
    events = _events()
    results = PartitionWriter(tmp_path).write(events)
    for result in results:
        assert result.relative_dir.startswith("schema=v1/venue=COMEX/product=GC/contract=COMEX_GC_2026-12/date=2026-09-15/")
        assert result.relative_dir.endswith(f"event_type={result.event_family}")
        assert (tmp_path / result.relative_dir / result.file_name).exists()


def test_reload_reconstructs_identical_canonical_events(tmp_path):
    events = _events()
    results = PartitionWriter(tmp_path).write(events)
    reader = PartitionReader(tmp_path)

    original_rows = sorted(serialize_event_row(e) for e in events)
    reloaded_events = []
    for result in results:
        reloaded_events.extend(reader.read_events(result.relative_dir, result.event_family))
    reloaded_rows = sorted(serialize_event_row(e) for e in reloaded_events)

    assert reloaded_rows == original_rows
    assert len(reloaded_events) == len(events)


def test_contract_identity_survives_the_full_round_trip(tmp_path):
    events = [e for e in _events() if isinstance(e, (MarketTradeEvent, TopOfBookEvent))]
    results = PartitionWriter(tmp_path).write(events)
    reader = PartitionReader(tmp_path)
    for result in results:
        for reloaded in reader.read_events(result.relative_dir, result.event_family):
            assert reloaded.contract_id == "COMEX:GC:2026-12"
            assert reloaded.contract_id != "GC"


def test_partition_content_hash_is_reproducible_across_two_writes_to_different_roots(tmp_path):
    events = _events()
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    results_a = PartitionWriter(root_a).write(events)
    results_b = PartitionWriter(root_b).write(events)

    hashes_a = {r.relative_path: r.partition_content_sha256 for r in results_a}
    hashes_b = {r.relative_path: r.partition_content_sha256 for r in results_b}
    assert hashes_a == hashes_b


def test_artifact_byte_determinism_is_measured_and_reported(tmp_path):
    """Physical Parquet artifact determinism (WO §12/§7): measured empirically here. This test
    asserts nothing about the OUTCOME either way — a mismatch is a legitimate, honestly-reported
    finding (see the HMT-1 final report), not a test failure. Semantic determinism
    (`partition_content_sha256`, proven above) remains mandatory regardless of this result."""
    events = _events()
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    results_a = PartitionWriter(root_a).write(events)
    results_b = PartitionWriter(root_b).write(events)

    artifacts_a = {r.relative_path: r.artifact_sha256 for r in results_a}
    artifacts_b = {r.relative_path: r.artifact_sha256 for r in results_b}
    # Recorded for the report, not asserted: see module docstring above.
    print("artifact_sha256 run A:", artifacts_a)
    print("artifact_sha256 run B:", artifacts_b)
    print("physical artifact byte-determinism:", artifacts_a == artifacts_b)
