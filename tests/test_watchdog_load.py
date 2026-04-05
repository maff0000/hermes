"""
Tests for HERMES Watchdog v2 — WO-HERMES-STREAM-WATCHDOG-0001A

Proves:
1. record_tick() does NOT open DB connections (in-memory only)
2. Persistence occurs on cadence/state-change, not per tick
3. Health snapshot reflects fresh in-memory state
4. Stale detection still works with in-memory timestamps
5. Burst tick simulation stays stable
6. Logger injection works (errors visible)
7. All original WO-1 acceptance tests still pass
"""
import asyncio
import sys
import os
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.watchdog import (
    HermesWatchdog, HealthPersistence, FaultCode,
    StreamState, HealthState, RecoveryState, DataFlowState,
)


class MockPersistence:
    """In-memory mock that counts DB calls."""

    def __init__(self):
        self.health = {
            'service_name': 'hermes', 'environment': 'TEST',
            'last_tick_utc': None, 'last_stream_msg_utc': None,
            'last_candle_m1_utc': None, 'last_signal_utc': None,
            'stream_state': StreamState.DISCONNECTED,
            'data_flow_state': DataFlowState.UNKNOWN,
            'health_state': HealthState.RED,
            'recovery_state': RecoveryState.IDLE,
            'fault_code': None, 'fault_detail_json': None,
            'recovery_attempt_no': 0,
            'updated_at': datetime.now(timezone.utc),
        }
        self.incidents = []
        self._next_incident_id = 1
        self._db_config = {'host': 'mock', 'port': 0, 'user': '', 'password': '', 'database': ''}

        # Counters for proving behavior
        self.update_health_call_count = 0
        self.open_incident_call_count = 0

    def update_health(self, **kwargs):
        self.update_health_call_count += 1
        for k, v in kwargs.items():
            if v is not None and k in self.health:
                self.health[k] = v
        self.health['updated_at'] = datetime.now(timezone.utc)

    def read_health(self):
        return dict(self.health)

    def open_incident(self, severity, fault_code, fault_summary, diagnostic_json=None):
        self.open_incident_call_count += 1
        iid = self._next_incident_id
        self._next_incident_id += 1
        self.incidents.append({
            'incident_id': iid, 'severity': severity, 'fault_code': fault_code,
            'fault_summary': fault_summary, 'status': 'OPEN',
            'opened_at': datetime.now(timezone.utc),
        })
        return iid

    def close_incident(self, incident_id, resolution_json=None):
        for inc in self.incidents:
            if inc['incident_id'] == incident_id:
                inc['status'] = 'RESOLVED'

    def get_open_incidents(self):
        return [i for i in self.incidents if i['status'] == 'OPEN']


class MockLogger:
    """Captures log calls for visibility proof."""
    def __init__(self):
        self.messages = []

    def _log(self, level, msg):
        self.messages.append((level, msg))

    def info(self, msg): self._log('info', msg)
    def warning(self, msg): self._log('warning', msg)
    def error(self, msg): self._log('error', msg)
    def critical(self, msg): self._log('critical', msg)
    def debug(self, msg): self._log('debug', msg)


class MockServiceState:
    def __init__(self):
        self.latest_ticks = {}


def make_watchdog(market_open=True, tick_threshold=10, candle_threshold=15,
                  proof_window=5, max_recovery=3, watchdog_interval=2):
    persist = MockPersistence()
    mock_logger = MockLogger()
    config = {
        'tick_staleness_threshold_sec': tick_threshold,
        'candle_staleness_threshold_sec': candle_threshold,
        'recovery_proof_window_sec': proof_window,
        'max_recovery_attempts': max_recovery,
        'watchdog_interval_sec': watchdog_interval,
    }
    wd = HermesWatchdog(
        service_state=MockServiceState(),
        persistence=persist,
        config=config,
        market_hours_checker=lambda ts=None: (market_open, "test"),
        logger=mock_logger,
    )
    return wd, persist, mock_logger


def run_test(name, test_func):
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"{'='*60}")
    try:
        asyncio.get_event_loop().run_until_complete(test_func())
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
# Test 1: record_tick does NOT cause DB writes
# ============================================================
async def test_no_per_tick_db():
    """Burst 1000 ticks — persistence call count must be zero."""
    wd, persist, _ = make_watchdog()

    # Set flowing so ticks don't trigger promotion
    wd._current_stream_state = StreamState.FLOWING
    wd._current_health_state = HealthState.GREEN

    persist.update_health_call_count = 0  # Reset counter

    for i in range(1000):
        wd.record_tick(datetime.now(timezone.utc))

    assert persist.update_health_call_count == 0, \
        f"Expected 0 DB writes from 1000 ticks, got {persist.update_health_call_count}"

    # Verify in-memory state IS updated
    assert wd._last_tick_utc is not None
    print(f"  1000 ticks → {persist.update_health_call_count} DB writes (correct: 0)")


# ============================================================
# Test 2: Persistence on cadence
# ============================================================
async def test_cadence_persistence():
    """Watchdog loop persists on interval."""
    wd, persist, _ = make_watchdog(watchdog_interval=1)
    wd._current_stream_state = StreamState.FLOWING
    wd._current_health_state = HealthState.GREEN

    persist.update_health_call_count = 0

    # Simulate a few ticks
    for _ in range(10):
        wd.record_tick(datetime.now(timezone.utc))

    # Force a persist cycle
    wd._persist_if_needed()

    # Should have persisted once (state_dirty from initial set)
    assert persist.update_health_call_count >= 1, \
        f"Expected at least 1 persist, got {persist.update_health_call_count}"

    print(f"  Persist calls after cadence: {persist.update_health_call_count}")


# ============================================================
# Test 3: Health snapshot uses in-memory state
# ============================================================
async def test_snapshot_in_memory():
    """Health snapshot reflects in-memory tick, not DB."""
    wd, persist, _ = make_watchdog()

    fresh = datetime.now(timezone.utc)
    wd._current_stream_state = StreamState.FLOWING
    wd._current_health_state = HealthState.GREEN
    wd._last_tick_utc = fresh

    snapshot = wd.get_health_snapshot()

    assert snapshot['health_state'] == HealthState.GREEN
    assert snapshot['stream_state'] == StreamState.FLOWING
    assert snapshot['tick_age_seconds'] is not None
    assert snapshot['tick_age_seconds'] < 2.0, \
        f"Tick age should be <2s, got {snapshot['tick_age_seconds']}"

    print(f"  Snapshot tick age: {snapshot['tick_age_seconds']}s (in-memory, no DB read)")


# ============================================================
# Test 4: Stale detection with in-memory timestamps
# ============================================================
async def test_stale_from_memory():
    """Staleness detected from in-memory last_tick, not DB."""
    wd, persist, logger = make_watchdog(market_open=True, tick_threshold=5)

    old = datetime.now(timezone.utc) - timedelta(seconds=10)
    wd._current_stream_state = StreamState.FLOWING
    wd._last_tick_utc = old

    persist.update_health_call_count = 0
    await wd._evaluate(tick_threshold=5, candle_threshold=15)

    assert wd._current_fault_code == FaultCode.STALE_TICK
    assert wd._current_health_state == HealthState.RED

    # Should have persisted on fault (immediate)
    assert persist.update_health_call_count >= 1

    critical_msgs = [m for l, m in logger.messages if l == 'critical']
    assert len(critical_msgs) >= 1, "Expected critical log on fault"

    print(f"  Fault: {wd._current_fault_code}")
    print(f"  Persist calls on fault: {persist.update_health_call_count}")
    print(f"  Critical logs: {len(critical_msgs)}")


# ============================================================
# Test 5: Burst tick stability
# ============================================================
async def test_burst_stability():
    """10000 rapid ticks followed by evaluation — system stays stable."""
    wd, persist, _ = make_watchdog(market_open=True, tick_threshold=120)
    wd._current_stream_state = StreamState.FLOWING
    wd._current_health_state = HealthState.GREEN

    start = time.monotonic()
    for _ in range(10000):
        wd.record_tick(datetime.now(timezone.utc))
    elapsed = time.monotonic() - start

    # Should be fast — no DB, no I/O
    assert elapsed < 1.0, f"10000 ticks took {elapsed:.3f}s — too slow for in-memory only"

    await wd._evaluate(tick_threshold=120, candle_threshold=180)
    assert wd._current_health_state == HealthState.GREEN

    print(f"  10000 ticks in {elapsed*1000:.1f}ms")
    print(f"  Health after burst: {wd._current_health_state}")


# ============================================================
# Test 6: Logger injection
# ============================================================
async def test_logger_injection():
    """Watchdog logs through injected logger, not standalone."""
    wd, persist, logger = make_watchdog()

    wd.set_stream_state(StreamState.FLOWING)
    wd.enter_proof_window()

    assert len(logger.messages) >= 2, f"Expected log messages, got {len(logger.messages)}"

    # Check state transition logged
    state_msgs = [m for l, m in logger.messages if 'Stream state' in m]
    assert len(state_msgs) >= 1, "Expected stream state transition in logs"

    print(f"  Log messages captured: {len(logger.messages)}")
    for level, msg in logger.messages[:5]:
        print(f"    [{level}] {msg}")


# ============================================================
# Test 7: False reconnect still works
# ============================================================
async def test_false_reconnect():
    wd, persist, _ = make_watchdog(market_open=True, proof_window=2)
    wd.enter_proof_window()
    await asyncio.sleep(3)
    await wd._evaluate(tick_threshold=10, candle_threshold=15)

    assert persist.health['fault_code'] == FaultCode.FALSE_CONNECT
    assert wd.stream_state == StreamState.STALE
    print(f"  False reconnect → {persist.health['fault_code']}")


# ============================================================
# Test 8: Recovery exhaustion still works
# ============================================================
async def test_recovery_exhaustion():
    wd, persist, _ = make_watchdog(max_recovery=3)
    fatal_called = False
    async def mock_fatal():
        nonlocal fatal_called
        fatal_called = True

    wd.set_fatal_exit_callback(mock_fatal)
    for _ in range(3):
        wd.record_recovery_attempt()

    await wd._evaluate(tick_threshold=120, candle_threshold=180)

    assert fatal_called
    assert persist.health['recovery_state'] == RecoveryState.EXHAUSTED
    print(f"  Exhaustion → fatal callback fired")


# ============================================================
# Test 9: Flow promotion persists immediately
# ============================================================
async def test_promotion_persists():
    """When stream promotes to FLOWING, state is marked dirty for immediate persist."""
    wd, persist, _ = make_watchdog()
    wd.enter_proof_window()

    persist.update_health_call_count = 0

    # Tick arrives — promotes to FLOWING
    wd.record_tick(datetime.now(timezone.utc))

    assert wd._state_dirty, "State should be dirty after promotion"
    assert wd.stream_state == StreamState.FLOWING

    # Persist cycle
    wd._persist_if_needed()
    assert persist.update_health_call_count >= 1
    assert persist.health['stream_state'] == StreamState.FLOWING
    assert persist.health['health_state'] == HealthState.GREEN

    print(f"  Promotion → FLOWING persisted in {persist.update_health_call_count} call(s)")


# ============================================================
if __name__ == "__main__":
    tests = [
        ("No per-tick DB writes (1000 ticks)", test_no_per_tick_db),
        ("Cadence-driven persistence", test_cadence_persistence),
        ("Health snapshot from in-memory", test_snapshot_in_memory),
        ("Stale detection from in-memory", test_stale_from_memory),
        ("Burst tick stability (10000 ticks)", test_burst_stability),
        ("Logger injection", test_logger_injection),
        ("False reconnect (regression)", test_false_reconnect),
        ("Recovery exhaustion (regression)", test_recovery_exhaustion),
        ("Flow promotion persists immediately", test_promotion_persists),
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
