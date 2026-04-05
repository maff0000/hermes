"""
Tests for HERMES Watchdog — WO-HERMES-STREAM-WATCHDOG-0001

Proves:
1. False reconnect detection (proof window expiry)
2. Tick staleness detection during market hours
3. M1 candle stagnation detection
4. Recovery exhaustion → fatal exit path
5. Market-closed suppression (no false RED)
"""
import asyncio
import json
import sys
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.watchdog import (
    HermesWatchdog, HealthPersistence, FaultCode,
    StreamState, HealthState, RecoveryState, DataFlowState,
)


class MockPersistence:
    """In-memory mock of HealthPersistence for testing."""

    def __init__(self):
        self.health = {
            'service_name': 'hermes',
            'environment': 'TEST',
            'last_tick_utc': None,
            'last_stream_msg_utc': None,
            'last_candle_m1_utc': None,
            'last_signal_utc': None,
            'stream_state': StreamState.DISCONNECTED,
            'data_flow_state': DataFlowState.UNKNOWN,
            'health_state': HealthState.RED,
            'recovery_state': RecoveryState.IDLE,
            'fault_code': None,
            'fault_detail_json': None,
            'recovery_attempt_no': 0,
            'updated_at': datetime.now(timezone.utc),
        }
        self.incidents = []
        self._next_incident_id = 1

    def update_health(self, **kwargs):
        for k, v in kwargs.items():
            if v is not None and k in self.health:
                self.health[k] = v
        self.health['updated_at'] = datetime.now(timezone.utc)

    def read_health(self):
        return dict(self.health)

    def open_incident(self, severity, fault_code, fault_summary, diagnostic_json=None):
        iid = self._next_incident_id
        self._next_incident_id += 1
        self.incidents.append({
            'incident_id': iid,
            'severity': severity,
            'fault_code': fault_code,
            'fault_summary': fault_summary,
            'diagnostic_json': diagnostic_json,
            'status': 'OPEN',
            'opened_at': datetime.now(timezone.utc),
        })
        return iid

    def close_incident(self, incident_id, resolution_json=None):
        for inc in self.incidents:
            if inc['incident_id'] == incident_id:
                inc['status'] = 'RESOLVED'
                inc['resolution_json'] = resolution_json

    def get_open_incidents(self):
        return [i for i in self.incidents if i['status'] == 'OPEN']

    def get_last_candle_m1_utc(self, instrument=None):
        return self.health.get('last_candle_m1_utc')


class MockServiceState:
    """Minimal mock of main.ServiceState."""
    def __init__(self):
        self.latest_ticks = {}


def make_watchdog(
    market_open=True,
    tick_threshold=10,
    candle_threshold=15,
    proof_window=5,
    max_recovery=3,
    watchdog_interval=1,
):
    """Create a watchdog with test-friendly config."""
    persist = MockPersistence()
    svc_state = MockServiceState()

    def mock_market_check(ts=None):
        return (market_open, "test market state")

    config = {
        'tick_staleness_threshold_sec': tick_threshold,
        'candle_staleness_threshold_sec': candle_threshold,
        'recovery_proof_window_sec': proof_window,
        'max_recovery_attempts': max_recovery,
        'watchdog_interval_sec': watchdog_interval,
    }

    wd = HermesWatchdog(
        service_state=svc_state,
        persistence=persist,
        config=config,
        market_hours_checker=mock_market_check,
    )

    return wd, persist


def run_test(name, test_func):
    """Run a single async test."""
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"{'='*60}")
    try:
        asyncio.get_event_loop().run_until_complete(test_func())
        print(f"  PASS ✓")
        return True
    except AssertionError as e:
        print(f"  FAIL ✗: {e}")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


# ============================================================
# Test 1: False reconnect detection
# ============================================================
async def test_false_reconnect():
    """
    Simulate: reconnect claims success but no ticks arrive.
    Expected: proof window expires → HERMES_RECOVERY_FALSE_CONNECT → RED → incident
    """
    wd, persist = make_watchdog(market_open=True, proof_window=2)

    # Simulate reconnect — enter proof window
    wd.enter_proof_window()

    assert wd.stream_state == StreamState.CONNECTED_UNPROVEN, \
        f"Expected CONNECTED_UNPROVEN, got {wd.stream_state}"
    assert persist.health['recovery_state'] == RecoveryState.PROOF_WINDOW

    # Wait for proof window to expire
    await asyncio.sleep(3)

    # Run watchdog evaluation
    await wd._evaluate(tick_threshold=10, candle_threshold=15)

    # Verify fault detected
    assert persist.health['health_state'] == HealthState.RED, \
        f"Expected RED, got {persist.health['health_state']}"
    assert persist.health['fault_code'] == FaultCode.FALSE_CONNECT, \
        f"Expected {FaultCode.FALSE_CONNECT}, got {persist.health['fault_code']}"
    assert persist.health['recovery_state'] == RecoveryState.FAILED
    assert wd.stream_state == StreamState.STALE

    # Verify incident opened
    open_incidents = persist.get_open_incidents()
    assert len(open_incidents) >= 1, "Expected at least 1 open incident"
    assert open_incidents[0]['fault_code'] == FaultCode.FALSE_CONNECT

    print(f"  Fault code: {persist.health['fault_code']}")
    print(f"  Incidents opened: {len(open_incidents)}")


# ============================================================
# Test 2: Tick staleness during market hours
# ============================================================
async def test_tick_staleness():
    """
    Simulate: ticks stop arriving during market hours.
    Expected: HERMES_STREAM_STALE_TICK → RED → incident
    """
    wd, persist = make_watchdog(market_open=True, tick_threshold=5)

    # Set stream as flowing with a recent tick
    old_tick = datetime.now(timezone.utc) - timedelta(seconds=10)
    wd.set_stream_state(StreamState.FLOWING)
    wd.record_tick(old_tick)

    # Evaluate — tick is 10s old, threshold is 5s
    await wd._evaluate(tick_threshold=5, candle_threshold=15)

    assert persist.health['health_state'] == HealthState.RED, \
        f"Expected RED, got {persist.health['health_state']}"
    assert persist.health['fault_code'] == FaultCode.STALE_TICK
    assert wd.stream_state == StreamState.STALE

    open_incidents = persist.get_open_incidents()
    assert len(open_incidents) >= 1
    assert open_incidents[0]['fault_code'] == FaultCode.STALE_TICK

    print(f"  Fault code: {persist.health['fault_code']}")
    print(f"  Stream state: {wd.stream_state}")


# ============================================================
# Test 3: M1 candle stagnation
# ============================================================
async def test_candle_stagnation():
    """
    Simulate: M1 candles stop progressing despite ticks flowing.
    Expected: HERMES_STREAM_STALE_CANDLE → RED
    """
    wd, persist = make_watchdog(market_open=True, tick_threshold=120, candle_threshold=5)

    # Fresh tick but old candle
    fresh_tick = datetime.now(timezone.utc)
    old_candle = datetime.now(timezone.utc) - timedelta(seconds=10)

    wd.set_stream_state(StreamState.FLOWING)
    wd.record_tick(fresh_tick)
    wd.record_candle_m1(old_candle)

    # Evaluate — candle is 10s old, threshold is 5s
    await wd._evaluate(tick_threshold=120, candle_threshold=5)

    assert persist.health['health_state'] == HealthState.RED, \
        f"Expected RED, got {persist.health['health_state']}"
    assert persist.health['fault_code'] == FaultCode.STALE_CANDLE

    print(f"  Fault code: {persist.health['fault_code']}")


# ============================================================
# Test 4: Recovery exhaustion → fatal exit
# ============================================================
async def test_recovery_exhaustion():
    """
    Simulate: repeated recovery attempts exhaust limit.
    Expected: HERMES_RECOVERY_EXHAUSTED → FATAL incident → fatal exit triggered
    """
    wd, persist = make_watchdog(market_open=True, max_recovery=3)

    fatal_called = False

    async def mock_fatal():
        nonlocal fatal_called
        fatal_called = True

    wd.set_fatal_exit_callback(mock_fatal)

    # Simulate 3 recovery attempts
    for i in range(3):
        wd.record_recovery_attempt()

    assert wd.is_recovery_exhausted, "Should be exhausted after 3 attempts"
    assert wd.recovery_attempt_no == 3

    # Evaluate — should trigger exhaustion handler
    await wd._evaluate(tick_threshold=120, candle_threshold=180)

    assert persist.health['recovery_state'] == RecoveryState.EXHAUSTED
    assert persist.health['fault_code'] == FaultCode.RECOVERY_EXHAUSTED
    assert fatal_called, "Fatal exit callback should have been called"

    # Check for FATAL incident
    fatal_incidents = [i for i in persist.incidents if i['severity'] == 'FATAL']
    assert len(fatal_incidents) >= 1, "Expected FATAL incident"

    print(f"  Recovery state: {persist.health['recovery_state']}")
    print(f"  Fatal callback called: {fatal_called}")
    print(f"  FATAL incidents: {len(fatal_incidents)}")


# ============================================================
# Test 5: Market-closed suppression
# ============================================================
async def test_market_closed_suppression():
    """
    Simulate: no ticks during market-closed period.
    Expected: no false RED, no stale incident
    """
    wd, persist = make_watchdog(market_open=False, tick_threshold=5)

    # Set stream as flowing with a very old tick
    old_tick = datetime.now(timezone.utc) - timedelta(hours=2)
    wd.set_stream_state(StreamState.FLOWING)
    wd.record_tick(old_tick)

    # Evaluate
    await wd._evaluate(tick_threshold=5, candle_threshold=15)

    # Should NOT be RED — market is closed
    assert persist.health['health_state'] != HealthState.RED or persist.health['fault_code'] is None, \
        f"Should not fault during market close. Health: {persist.health['health_state']}, Fault: {persist.health['fault_code']}"

    # No stale incidents
    stale_incidents = [i for i in persist.incidents if i['fault_code'] in (FaultCode.STALE_TICK, FaultCode.STALE_CANDLE)]
    assert len(stale_incidents) == 0, f"No stale incidents during market close, got {len(stale_incidents)}"

    print(f"  Health state: {persist.health['health_state']}")
    print(f"  Stale incidents: {len(stale_incidents)}")


# ============================================================
# Test 6: Data flow promotion after proof
# ============================================================
async def test_flow_promotion():
    """
    Simulate: reconnect → proof window → tick arrives → FLOWING/GREEN
    """
    wd, persist = make_watchdog(market_open=True, proof_window=30)

    wd.enter_proof_window()
    assert wd.stream_state == StreamState.CONNECTED_UNPROVEN

    # Fresh tick arrives
    wd.record_tick(datetime.now(timezone.utc))

    # Should promote to FLOWING
    assert wd.stream_state == StreamState.FLOWING, \
        f"Expected FLOWING, got {wd.stream_state}"
    assert persist.health['health_state'] == HealthState.GREEN
    assert persist.health['recovery_state'] == RecoveryState.IDLE

    print(f"  Stream state: {wd.stream_state}")
    print(f"  Health state: {persist.health['health_state']}")


# ============================================================
# Test 7: Incident closes on recovery
# ============================================================
async def test_incident_lifecycle():
    """
    Simulate: fault → incident opens → recovery → incident closes
    """
    wd, persist = make_watchdog(market_open=True, tick_threshold=5)

    # Trigger a fault
    old_tick = datetime.now(timezone.utc) - timedelta(seconds=10)
    wd.set_stream_state(StreamState.FLOWING)
    wd.record_tick(old_tick)
    await wd._evaluate(tick_threshold=5, candle_threshold=15)

    # Should have open incident
    assert len(persist.get_open_incidents()) >= 1

    # Simulate recovery
    wd.record_tick(datetime.now(timezone.utc))  # Fresh tick promotes to FLOWING

    # Incident should be closed
    assert len(persist.get_open_incidents()) == 0, \
        f"Expected 0 open incidents after recovery, got {len(persist.get_open_incidents())}"

    print(f"  Open incidents after recovery: {len(persist.get_open_incidents())}")


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    tests = [
        ("False reconnect detection", test_false_reconnect),
        ("Tick staleness during market hours", test_tick_staleness),
        ("M1 candle stagnation", test_candle_stagnation),
        ("Recovery exhaustion → fatal exit", test_recovery_exhaustion),
        ("Market-closed suppression", test_market_closed_suppression),
        ("Data flow promotion after proof", test_flow_promotion),
        ("Incident lifecycle (open → close)", test_incident_lifecycle),
    ]

    results = []
    for name, func in tests:
        results.append((name, run_test(name, func)))

    print(f"\n{'='*60}")
    print(f"RESULTS")
    print(f"{'='*60}")

    passed = sum(1 for _, ok in results if ok)
    total = len(results)

    for name, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")

    print(f"\n{passed}/{total} passed")

    if passed < total:
        sys.exit(1)
    else:
        print("\nAll tests passed. Watchdog behavior verified.")
        sys.exit(0)
