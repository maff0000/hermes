"""HMT-1 — FixtureMarketDataProvider: the one deterministic provider implementation.

No network call, no credential, no vendor SDK dependency anywhere in this module (WO acceptance
#24) — proven here by construction (nothing in this test touches a socket) and, package-wide, by
the static guard in test_fail_closed.py.
"""
from pathlib import Path

import pytest

from market_truth.provider import RawRecordType
from market_truth.providers.fixture import FixtureLoadError, FixtureMarketDataProvider

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "hmt1"


def test_loads_gc_mbp1_fixture_in_file_order():
    provider = FixtureMarketDataProvider(
        FIXTURES_DIR / "gc_mbp1_equivalent_v1.jsonl", provider_id="hmt1-fixture-provider", dataset_id="hmt1-test"
    )
    records = list(provider.iter_records())
    assert len(records) == 7
    assert [r.source_artifact_id for r in records] == [f"gc-mbp1-000{i}" for i in range(1, 8)]
    assert records[0].record_type is RawRecordType.TOP_OF_BOOK_UPDATE
    assert records[5].record_type is RawRecordType.TRADE


def test_content_sha256_is_stable_across_loads():
    a = FixtureMarketDataProvider(
        FIXTURES_DIR / "gc_mbp1_equivalent_v1.jsonl", provider_id="hmt1-fixture-provider", dataset_id="hmt1-test"
    )
    b = FixtureMarketDataProvider(
        FIXTURES_DIR / "gc_mbp1_equivalent_v1.jsonl", provider_id="hmt1-fixture-provider", dataset_id="hmt1-test"
    )
    assert a.content_sha256() == b.content_sha256()


def test_rejects_a_line_missing_the_synthetic_marker(tmp_path):
    bad_fixture = tmp_path / "unlabeled.jsonl"
    bad_fixture.write_text(
        '{"record_type": "QUOTE", "source_classification": "BROKER_OTC_EXECUTABLE_QUOTE_OBSERVATION", '
        '"provider_symbol": "XAUUSD", "source_artifact_id": "x", "source_event_time": "2026-01-01T00:00:00Z", '
        '"source_timestamp_precision": "SECOND", "provider_receive_time": null, '
        '"provider_receive_quality": "NOT_AVAILABLE", "source_sequence": null, "sequence_domain": "NOT_AVAILABLE", '
        '"acquisition_epoch": "e", "historical_provenance_era": "NOT_APPLICABLE", "quality_state": "OK", '
        '"payload": {"instrument_id": "x", "bid": null, "ask": null}}\n'
    )
    with pytest.raises(FixtureLoadError):
        FixtureMarketDataProvider(bad_fixture, provider_id="p", dataset_id="d")


def test_rejects_structurally_invalid_json_line(tmp_path):
    bad_fixture = tmp_path / "broken.jsonl"
    bad_fixture.write_text("{not valid json\n")
    with pytest.raises(FixtureLoadError):
        FixtureMarketDataProvider(bad_fixture, provider_id="p", dataset_id="d")


def test_loads_broker_quote_fixture():
    provider = FixtureMarketDataProvider(
        FIXTURES_DIR / "broker_quote_equivalent_v1.jsonl", provider_id="hmt1-fixture-provider", dataset_id="hmt1-test"
    )
    records = list(provider.iter_records())
    assert len(records) == 3
    assert all(r.record_type is RawRecordType.QUOTE for r in records)
