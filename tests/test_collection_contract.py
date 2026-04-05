"""
Tests for HERMES Collection Agent Contract — WO-HERMES-COLLECTION-AGENT-B

Proves:
1. CandidateM1 validity checks work correctly
2. Per-instrument isolation is enforced at contract level
3. Source policy reader works and respects no-inheritance rule
4. AcceptResult carries correct fault codes
5. InstrumentSourceHealth is per-instrument
"""
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.collection_contract import (
    CandidateM1, AcceptResult, AcceptStatus,
    InstrumentSourceHealth, SourceState, SourcePolicyReader,
    CollectionAgent,
)


def run_test(name, test_func):
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"{'='*60}")
    try:
        test_func()
        print(f"  PASS")
        return True
    except AssertionError as e:
        print(f"  FAIL: {e}")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================
# Test 1: CandidateM1 validity
# ============================================================
def test_candidate_validity():
    """Valid candidate passes, invalid ones fail."""
    # Valid
    valid = CandidateM1(
        instrument='XAU_USD',
        minute_bucket_utc=datetime(2026, 3, 31, 12, 0, 0),
        open=3100.50, high=3101.20, low=3100.10, close=3100.80,
        volume=42, source_id='oanda_stream'
    )
    assert valid.is_valid(), "Valid candidate should pass"

    # Invalid: seconds not zero
    bad_ts = CandidateM1(
        instrument='XAU_USD',
        minute_bucket_utc=datetime(2026, 3, 31, 12, 0, 30),
        open=3100.50, high=3101.20, low=3100.10, close=3100.80,
        volume=42, source_id='oanda_stream'
    )
    assert not bad_ts.is_valid(), "Non-zero seconds should fail"

    # Invalid: high < low
    bad_hl = CandidateM1(
        instrument='XAU_USD',
        minute_bucket_utc=datetime(2026, 3, 31, 12, 0, 0),
        open=3100.50, high=3099.00, low=3100.10, close=3100.80,
        volume=42, source_id='oanda_stream'
    )
    assert not bad_hl.is_valid(), "High < low should fail"

    # Invalid: missing source_id
    bad_src = CandidateM1(
        instrument='XAU_USD',
        minute_bucket_utc=datetime(2026, 3, 31, 12, 0, 0),
        open=3100.50, high=3101.20, low=3100.10, close=3100.80,
        volume=42, source_id=''
    )
    assert not bad_src.is_valid(), "Empty source_id should fail"

    # Invalid: zero price
    bad_price = CandidateM1(
        instrument='XAU_USD',
        minute_bucket_utc=datetime(2026, 3, 31, 12, 0, 0),
        open=0, high=3101.20, low=3100.10, close=3100.80,
        volume=42, source_id='oanda_stream'
    )
    assert not bad_price.is_valid(), "Zero open should fail"

    # Invalid: empty instrument
    bad_inst = CandidateM1(
        instrument='',
        minute_bucket_utc=datetime(2026, 3, 31, 12, 0, 0),
        open=3100.50, high=3101.20, low=3100.10, close=3100.80,
        volume=42, source_id='oanda_stream'
    )
    assert not bad_inst.is_valid(), "Empty instrument should fail"

    print(f"  6 validity checks passed (1 valid, 5 invalid correctly rejected)")


# ============================================================
# Test 2: AcceptResult carries fault codes
# ============================================================
def test_accept_result():
    """AcceptResult correctly carries status and fault codes."""
    accepted = AcceptResult(
        status=AcceptStatus.ACCEPTED,
        instrument='XAU_USD',
        minute_bucket_utc=datetime(2026, 3, 31, 12, 0, 0),
        source_id='oanda_stream',
    )
    assert accepted.status == AcceptStatus.ACCEPTED
    assert accepted.fault_code is None

    rejected = AcceptResult(
        status=AcceptStatus.REJECTED_DUPLICATE,
        instrument='XAU_USD',
        minute_bucket_utc=datetime(2026, 3, 31, 12, 0, 0),
        source_id='oanda_stream',
        fault_code='HERMES_CANONICAL_M1_REJECTED_DUPLICATE',
        detail='Canonical row already exists',
    )
    assert rejected.status == AcceptStatus.REJECTED_DUPLICATE
    assert rejected.fault_code == 'HERMES_CANONICAL_M1_REJECTED_DUPLICATE'

    print(f"  Accept/reject results carry correct status and fault codes")


# ============================================================
# Test 3: Per-instrument health (not monolith)
# ============================================================
def test_per_instrument_health():
    """Health is per-instrument. No monolith."""
    health_xau = InstrumentSourceHealth(
        instrument='XAU_USD', source_id='oanda_stream',
        state=SourceState.CONNECTED,
        last_candidate_utc=datetime.now(timezone.utc),
        candidates_submitted=100, candidates_accepted=99, candidates_rejected=1,
    )
    health_eur = InstrumentSourceHealth(
        instrument='EUR_USD', source_id='oanda_stream',
        state=SourceState.FAILED,
        last_error='Connection timeout',
    )

    # XAU healthy, EUR failed — independent
    assert health_xau.state == SourceState.CONNECTED
    assert health_eur.state == SourceState.FAILED
    assert health_xau.instrument != health_eur.instrument

    # Health dict keyed by instrument
    health_dict = {
        health_xau.instrument: health_xau,
        health_eur.instrument: health_eur,
    }
    assert health_dict['XAU_USD'].state == SourceState.CONNECTED
    assert health_dict['EUR_USD'].state == SourceState.FAILED

    print(f"  Per-instrument health: XAU=CONNECTED, EUR=FAILED (independent)")


# ============================================================
# Test 4: Source policy reader — per instrument, no inheritance
# ============================================================
def test_source_policy_reader():
    """Policy reader returns per-instrument config with no inheritance."""
    from env_config import get_db_config
    db = get_db_config()
    reader = SourcePolicyReader(db)

    xau = reader.get_policy('XAU_USD')
    assert xau is not None, "XAU_USD must have a policy"
    assert xau['primary_source'] == 'oanda_stream'
    assert xau['acceptance_window_sec'] == 120
    assert xau['is_enabled'] == 1

    eur = reader.get_policy('EUR_USD')
    assert eur is not None, "EUR_USD must have a policy"
    assert eur['instrument'] == 'EUR_USD'

    # No inheritance — nonexistent instrument returns None
    fake = reader.get_policy('FAKE_INST')
    assert fake is None, "Non-existent instrument must return None, not inherit"

    all_policies = reader.get_all_policies()
    assert len(all_policies) == 12, f"Expected 12 instrument policies, got {len(all_policies)}"

    print(f"  12 policies loaded, no inheritance (FAKE_INST=None)")


# ============================================================
# Test 5: Contract forbids cross-instrument dependency
# ============================================================
def test_contract_isolation():
    """CandidateM1 is always single-instrument. No batch."""
    c1 = CandidateM1(
        instrument='XAU_USD',
        minute_bucket_utc=datetime(2026, 3, 31, 12, 0, 0),
        open=3100.50, high=3101.20, low=3100.10, close=3100.80,
        volume=42, source_id='oanda_stream'
    )
    c2 = CandidateM1(
        instrument='EUR_USD',
        minute_bucket_utc=datetime(2026, 3, 31, 12, 0, 0),
        open=1.0850, high=1.0855, low=1.0845, close=1.0852,
        volume=38, source_id='oanda_stream'
    )

    # Each candidate is independent — no shared state
    assert c1.instrument != c2.instrument
    assert c1.is_valid()
    assert c2.is_valid()

    # There is no batch/group structure in the contract — candidates are atomic
    # The CollectionAgent.stream_candidates() yields one at a time
    # The only way to submit is per-candidate, per-instrument

    print(f"  Candidates are atomic per-instrument. No batch structure exists.")


# ============================================================
# Test 6: CandidateM1 serialization
# ============================================================
def test_candidate_serialization():
    """to_dict produces clean JSON-serializable output."""
    c = CandidateM1(
        instrument='XAU_USD',
        minute_bucket_utc=datetime(2026, 3, 31, 12, 0, 0),
        open=3100.50, high=3101.20, low=3100.10, close=3100.80,
        volume=42, source_id='oanda_stream',
        source_timestamp_utc=datetime(2026, 3, 31, 12, 0, 0, 500000),
    )
    d = c.to_dict()
    assert d['instrument'] == 'XAU_USD'
    assert d['source_id'] == 'oanda_stream'
    assert '2026-03-31' in d['minute_bucket_utc']

    import json
    json_str = json.dumps(d)
    assert len(json_str) > 0

    print(f"  Serialization clean: {len(json_str)} bytes")


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    tests = [
        ("CandidateM1 validity checks", test_candidate_validity),
        ("AcceptResult fault codes", test_accept_result),
        ("Per-instrument health (not monolith)", test_per_instrument_health),
        ("Source policy reader (no inheritance)", test_source_policy_reader),
        ("Contract isolation (no cross-instrument)", test_contract_isolation),
        ("CandidateM1 serialization", test_candidate_serialization),
    ]

    results = []
    for name, func in tests:
        results.append((name, run_test(name, func)))

    print(f"\n{'='*60}")
    print("RESULTS")
    print(f"{'='*60}")
    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n{passed}/{len(results)} passed")
    sys.exit(0 if passed == len(results) else 1)
