"""HMT-1 — evidence.py: versioned manifest, deterministic fields excluding wall clock."""
import json
from pathlib import Path

from market_truth.replay import run_pipeline

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "hmt1"
MAPPING_PATH = FIXTURES_DIR / "gc_contract_mapping_v1.json"
GC_FIXTURE = FIXTURES_DIR / "gc_mbp1_equivalent_v1.jsonl"


def _run(tmp_path, **kwargs):
    return run_pipeline(
        [GC_FIXTURE], MAPPING_PATH, tmp_path, provider_id="hmt1-fixture-provider", dataset_id="hmt1-test", **kwargs
    )


def test_manifest_deterministic_hash_is_reproducible(tmp_path):
    a = _run(tmp_path / "a")
    b = _run(tmp_path / "b")
    assert a.manifest.deterministic_fields_sha256() == b.manifest.deterministic_fields_sha256()


def test_manifest_excludes_audit_generated_at_from_the_deterministic_hash(tmp_path):
    result = _run(tmp_path)
    manifest = result.manifest
    stamped = type(manifest)(**{**manifest.__dict__, "audit_generated_at": "2026-09-21T00:00:00Z"})
    assert stamped.deterministic_fields_sha256() == manifest.deterministic_fields_sha256()


def test_changing_canonicaliser_version_changes_the_manifest_identity(tmp_path):
    a = _run(tmp_path / "a", canonicaliser_version="hmt1-canonicaliser-v1")
    b = _run(tmp_path / "b", canonicaliser_version="hmt1-canonicaliser-v1-experimental")
    assert a.manifest.deterministic_fields_sha256() != b.manifest.deterministic_fields_sha256()
    # Event identities themselves are unaffected — identity is about *what the event is*, not the
    # governed shape/version that produced it (identity.py module docstring).
    assert sorted(e.identity_hash for e in a.events) == sorted(e.identity_hash for e in b.events)


def test_changing_schema_version_changes_the_manifest_and_partition_content_hash(tmp_path):
    a = _run(tmp_path / "a", schema_version="hmt1-market-event-contract-v1")
    b = _run(tmp_path / "b", schema_version="hmt1-market-event-contract-v1-experimental")
    assert a.manifest.canonical_schema_version != b.manifest.canonical_schema_version
    assert dict(a.manifest.partition_content_hashes) != dict(b.manifest.partition_content_hashes)


def test_manifest_to_json_dict_round_trips_through_json(tmp_path):
    result = _run(tmp_path)
    payload = result.manifest.to_json_dict()
    text = json.dumps(payload)
    reloaded = json.loads(text)
    assert reloaded["canonical_event_set_hash"] == result.manifest.canonical_event_set_hash
    assert reloaded["deterministic_fields_sha256"] == result.manifest.deterministic_fields_sha256()


def test_manifest_records_expected_counts(tmp_path):
    result = _run(tmp_path)
    assert result.source_record_count == 7
    assert result.manifest.source_record_count == 7
    assert sum(result.manifest.canonical_event_counts_by_family.values()) == len(result.events)
