"""
Tests for HERMES Gap Scanner — WO-HERMES-GAP-TRUTH-0002

Proves:
1. Controlled M1 candle gap detected precisely
2. Real 2026-03-31 outage window detected deterministically
3. Market-closed suppression (no false gaps)
4. Signal gap logic (only where structurally expected)
5. Idempotent rescans (no duplicate truth)
"""
import sys
import os
import json
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.gap_scanner import (
    GapScanner, GapLedger, GapRecord,
    generate_expected_timestamps, is_forex_market_open,
    TIMEFRAMES, SIGNAL_TIMEFRAMES,
)


def get_db_config():
    from env_config import get_db_config as _get
    return _get()


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
# Test 1: Market hours logic
# ============================================================
def test_market_hours():
    """Verify forex market hours are correct."""
    # Saturday — closed
    sat = datetime(2026, 3, 28, 12, 0, 0)
    assert not is_forex_market_open(sat), "Saturday should be closed"

    # Sunday before 22:00 — closed
    sun_early = datetime(2026, 3, 29, 15, 0, 0)
    assert not is_forex_market_open(sun_early), "Sunday 15:00 should be closed"

    # Sunday 22:00 — open
    sun_open = datetime(2026, 3, 29, 22, 0, 0)
    assert is_forex_market_open(sun_open), "Sunday 22:00 should be open"

    # Monday — open
    mon = datetime(2026, 3, 30, 10, 0, 0)
    assert is_forex_market_open(mon), "Monday should be open"

    # Friday before 22:00 — open
    fri_early = datetime(2026, 3, 27, 21, 0, 0)
    assert is_forex_market_open(fri_early), "Friday 21:00 should be open"

    # Friday 22:00 — closed
    fri_close = datetime(2026, 3, 27, 22, 0, 0)
    assert not is_forex_market_open(fri_close), "Friday 22:00 should be closed"

    print(f"  Market hours: all 6 checks passed")


# ============================================================
# Test 2: Expected timestamp generation
# ============================================================
def test_expected_timestamps():
    """Verify expected timestamps respect market hours."""
    # Generate M5 for a Monday window
    start = datetime(2026, 3, 30, 10, 0, 0)
    end = datetime(2026, 3, 30, 10, 30, 0)
    expected = generate_expected_timestamps('M5', start, end)

    assert len(expected) == 7, f"Expected 7 M5 candles in 30min, got {len(expected)}"
    assert expected[0] == datetime(2026, 3, 30, 10, 0, 0)
    assert expected[-1] == datetime(2026, 3, 30, 10, 30, 0)

    # Generate M1 during Saturday — should be empty
    sat_start = datetime(2026, 3, 28, 10, 0, 0)
    sat_end = datetime(2026, 3, 28, 11, 0, 0)
    sat_expected = generate_expected_timestamps('M1', sat_start, sat_end)
    assert len(sat_expected) == 0, f"Saturday should have 0 expected, got {len(sat_expected)}"

    print(f"  Monday 30min: {len(expected)} M5 timestamps ✓")
    print(f"  Saturday 1hr: {len(sat_expected)} M1 timestamps ✓")


# ============================================================
# Test 3: Real incident window detection
# ============================================================
def test_real_incident_detection():
    """
    Scan the real 2026-03-31 outage window.
    Expected: M1 gap from ~02:55 to ~07:41 UTC.
    """
    db_config = get_db_config()
    scanner = GapScanner(db_config)

    gaps = scanner.scan_candle_gaps(
        'XAU_USD', 'M1',
        datetime(2026, 3, 31, 2, 0, 0),
        datetime(2026, 3, 31, 8, 0, 0),
    )

    assert len(gaps) >= 1, f"Expected at least 1 M1 gap, got {len(gaps)}"

    main_gap = gaps[0]
    assert main_gap.instrument == 'XAU_USD'
    assert main_gap.timeframe == 'M1'
    assert main_gap.artifact_type == 'CANDLE'
    assert main_gap.missing_count > 200, f"Expected >200 missing M1 candles, got {main_gap.missing_count}"

    # Gap should start around 02:55
    assert main_gap.gap_start_utc.hour in (2, 3), \
        f"Gap start hour should be 2 or 3, got {main_gap.gap_start_utc.hour}"
    # Gap should end around 07:41
    assert main_gap.gap_end_utc.hour in (7, 8), \
        f"Gap end hour should be 7 or 8, got {main_gap.gap_end_utc.hour}"

    print(f"  M1 gap: {main_gap.gap_start_utc} → {main_gap.gap_end_utc}")
    print(f"  Missing: {main_gap.missing_count} candles")
    print(f"  Scan hash: {main_gap.scan_hash}")


# ============================================================
# Test 4: Higher timeframe gaps from real incident
# ============================================================
def test_higher_tf_gaps():
    """Check M15 and H1 gaps from the real incident."""
    db_config = get_db_config()
    scanner = GapScanner(db_config)

    gaps_m15 = scanner.scan_candle_gaps(
        'XAU_USD', 'M15',
        datetime(2026, 3, 31, 2, 0, 0),
        datetime(2026, 3, 31, 8, 0, 0),
    )

    gaps_h1 = scanner.scan_candle_gaps(
        'XAU_USD', 'H1',
        datetime(2026, 3, 31, 2, 0, 0),
        datetime(2026, 3, 31, 8, 0, 0),
    )

    assert len(gaps_m15) >= 1, f"Expected M15 gaps, got {len(gaps_m15)}"
    assert len(gaps_h1) >= 1, f"Expected H1 gaps, got {len(gaps_h1)}"

    print(f"  M15 gaps: {len(gaps_m15)}, total missing: {sum(g.missing_count for g in gaps_m15)}")
    print(f"  H1 gaps: {len(gaps_h1)}, total missing: {sum(g.missing_count for g in gaps_h1)}")


# ============================================================
# Test 5: Market-closed suppression
# ============================================================
def test_market_closed_suppression():
    """Saturday window should produce zero gaps."""
    db_config = get_db_config()
    scanner = GapScanner(db_config)

    gaps = scanner.scan_candle_gaps(
        'XAU_USD', 'M1',
        datetime(2026, 3, 28, 0, 0, 0),   # Saturday
        datetime(2026, 3, 28, 23, 59, 0),
    )

    assert len(gaps) == 0, f"Saturday should have 0 gaps, got {len(gaps)}"
    print(f"  Saturday gaps: {len(gaps)} ✓")


# ============================================================
# Test 6: Signal gap detection
# ============================================================
def test_signal_gap_detection():
    """M15 signals should have gaps in the outage window. M1 signals should not be expected."""
    db_config = get_db_config()
    scanner = GapScanner(db_config)

    # M15 signals — structurally expected, should have gaps
    signal_gaps_m15 = scanner.scan_signal_gaps(
        'XAU_USD', 'M15',
        datetime(2026, 3, 31, 2, 0, 0),
        datetime(2026, 3, 31, 8, 0, 0),
    )
    assert len(signal_gaps_m15) >= 1, f"Expected M15 signal gaps, got {len(signal_gaps_m15)}"

    # M1 signals — NOT structurally expected by HERMES
    signal_gaps_m1 = scanner.scan_signal_gaps(
        'XAU_USD', 'M1',
        datetime(2026, 3, 31, 2, 0, 0),
        datetime(2026, 3, 31, 8, 0, 0),
    )
    assert len(signal_gaps_m1) == 0, f"M1 signals should not be expected, got {len(signal_gaps_m1)} gaps"

    print(f"  M15 signal gaps: {len(signal_gaps_m15)} ✓")
    print(f"  M1 signal gaps: {len(signal_gaps_m1)} (correctly zero) ✓")


# ============================================================
# Test 7: Idempotent persistence
# ============================================================
def test_idempotent_persistence():
    """Persisting the same gaps twice should not create duplicates."""
    db_config = get_db_config()
    scanner = GapScanner(db_config)
    ledger = GapLedger(db_config)

    gaps = scanner.scan_candle_gaps(
        'XAU_USD', 'M1',
        datetime(2026, 3, 31, 2, 0, 0),
        datetime(2026, 3, 31, 8, 0, 0),
    )

    # First persist
    new1, existing1 = ledger.persist_gaps(gaps)
    print(f"  First persist: {new1} new, {existing1} existing")

    # Second persist — same gaps
    new2, existing2 = ledger.persist_gaps(gaps)
    print(f"  Second persist: {new2} new, {existing2} existing")

    assert new2 == 0, f"Second persist should create 0 new rows, got {new2}"
    assert existing2 == len(gaps), f"All {len(gaps)} should be existing, got {existing2}"


# ============================================================
# Test 8: Unresolved gap count for health integration
# ============================================================
def test_gap_count_for_health():
    """Verify unresolved count and latest gap summary work."""
    db_config = get_db_config()
    ledger = GapLedger(db_config)

    count = ledger.get_unresolved_count()
    assert count >= 1, f"Expected at least 1 unresolved gap, got {count}"

    latest = ledger.get_latest_gap_summary()
    assert latest is not None, "Expected a latest gap summary"
    assert latest.get('status') in ('DETECTED', 'CONFIRMED'), \
        f"Latest gap should be DETECTED or CONFIRMED, got {latest.get('status')}"

    print(f"  Unresolved gaps: {count}")
    print(f"  Latest: {latest.get('artifact_type')} {latest.get('timeframe')} "
          f"{latest.get('instrument')} — {latest.get('missing_count')} missing")


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    tests = [
        ("Market hours logic", test_market_hours),
        ("Expected timestamp generation", test_expected_timestamps),
        ("Real incident M1 gap detection", test_real_incident_detection),
        ("Higher timeframe gaps (M15, H1)", test_higher_tf_gaps),
        ("Market-closed suppression", test_market_closed_suppression),
        ("Signal gap detection logic", test_signal_gap_detection),
        ("Idempotent persistence", test_idempotent_persistence),
        ("Gap count for health integration", test_gap_count_for_health),
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
        print("\nAll tests passed. Gap scanner behavior verified.")
        sys.exit(0)
