"""HMT-1 — fail-closed round-up: duplicate/conflict, ambiguous mapping, and the derived-fact-
identity module's forbidden-P0-fact-token static guard (WO §15/§17 acceptance #19/#20)."""
import re
from pathlib import Path

import pytest

from market_truth.canonicaliser import Canonicaliser, DuplicateConflictError
from market_truth.futures import ContractMappingError, ContractMappingTable
from market_truth.providers.fixture import FixtureMarketDataProvider
import market_truth.derived_fact_identity as derived_fact_identity

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "hmt1"
MAPPING_PATH = FIXTURES_DIR / "gc_contract_mapping_v1.json"


def test_same_source_identity_with_conflicting_payload_fails_closed():
    canonicaliser = Canonicaliser(mapping_table=ContractMappingTable.from_json_file(MAPPING_PATH))
    provider = FixtureMarketDataProvider(
        FIXTURES_DIR / "gc_conflict_case_v1.jsonl", provider_id="hmt1-fixture-provider", dataset_id="hmt1-test"
    )
    records = list(provider.iter_records())
    assert records[0].source_artifact_id == records[1].source_artifact_id
    canonicaliser.canonicalise(records[0])
    with pytest.raises(DuplicateConflictError):
        canonicaliser.canonicalise(records[1])


def test_ambiguous_or_invalid_gc_mapping_fails_closed():
    table = ContractMappingTable.from_json_file(MAPPING_PATH)
    for bad_symbol in ("GC", "GCZ6", "GCZ99", "NOT_A_GC_SYMBOL", ""):
        with pytest.raises(ContractMappingError):
            table.resolve(bad_symbol)


def test_canonicaliser_fails_closed_when_mapping_is_ambiguous(tmp_path):
    bad_fixture = tmp_path / "unmapped_symbol.jsonl"
    bad_fixture.write_text(
        '{"__synthetic__": true, "record_type": "TRADE", '
        '"source_classification": "CENTRALISED_EXCHANGE_FUTURES_GENUINE_ORDER_FLOW", '
        '"provider_symbol": "GCZ99", "source_artifact_id": "x", '
        '"source_event_time": "2026-09-15T14:10:00.000Z", "source_timestamp_precision": "MILLISECOND", '
        '"provider_receive_time": "2026-09-15T14:10:00.004Z", "provider_receive_quality": "GENUINE", '
        '"source_sequence": 4001, "sequence_domain": "FIXTURE_PROVIDER_V1_NATIVE_SEQUENCE", '
        '"acquisition_epoch": "e", "historical_provenance_era": "MDP3_FROM_2017_05_21", "quality_state": "OK", '
        '"payload": {"price_mantissa": 100000, "price_scale": 2, "quantity": 1}}\n'
    )
    canonicaliser = Canonicaliser(mapping_table=ContractMappingTable.from_json_file(MAPPING_PATH))
    provider = FixtureMarketDataProvider(bad_fixture, provider_id="hmt1-fixture-provider", dataset_id="hmt1-test")
    from market_truth.canonicaliser import CanonicalisationError

    with pytest.raises(CanonicalisationError):
        canonicaliser.canonicalise(next(provider.iter_records()))


_FORBIDDEN_P0_FACT_TOKENS = (
    "volume_at_price", "point_of_control", "poc", "value_area", "vah", "val",
    "low_volume_node", "high_volume_node", "lvn", "hvn", "delta", "cvd",
    "cumulative_volume_delta", "absorption", "failed_aggression", "failed_auction",
    "imbalance", "squeeze", "trapped_flow", "aggression_cluster", "flow_efficiency",
)


def test_derived_fact_module_has_no_forbidden_p0_fact_tokens():
    """WO §15: derived_fact_identity.py is identity/provenance scaffolding ONLY. No real P0
    microstructure fact computation may ever appear in this module — enforced the same way this
    repository already guards main.py against forbidden tokens
    (tests/test_stream_silent_stall_recovery.py::T11_NoForbiddenTokens)."""
    source = Path(derived_fact_identity.__file__).read_text(encoding="utf-8").lower()
    # The module's own docstring explicitly NAMES the forbidden facts to say it does not compute
    # them — strip the module docstring before scanning so that explanatory prose doesn't trip its
    # own guard, then confirm no forbidden token appears in the executable code below it.
    body = source.split('"""', 2)[-1] if source.count('"""') >= 2 else source
    for token in _FORBIDDEN_P0_FACT_TOKENS:
        pattern = r"\b" + re.escape(token) + r"\b"
        assert not re.search(pattern, body), f"forbidden P0 fact token {token!r} found in derived_fact_identity.py body"


def test_derived_fact_identity_is_scaffolding_only_no_compute_function():
    forbidden_function_prefixes = ("compute_", "calculate_", "derive_")
    for name in dir(derived_fact_identity):
        assert not name.startswith(forbidden_function_prefixes), f"unexpected computation function: {name}"
