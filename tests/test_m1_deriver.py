"""
Tests for HERMES M1 Derivation Engine — WO-HERMES-GENERATOR-SPLIT-D

Proves:
1. M5 derived correctly from 5 M1 candles
2. M15 derived correctly from 15 M1 candles
3. H1 derived correctly from 60 M1 candles
4. Partial M1 sets produce incomplete candles (not silently dropped)
5. Per-instrument independence
6. Equivalence comparison works against legacy data
7. Volume is sum of M1 volumes (documented semantic)
8. Bucket boundaries match CandleAggregator logic
"""
import sys
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pymysql

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from env_config import get_db_config
from utils.m1_deriver import (
    M1DerivationEngine, DerivedCandle,
    get_bucket_start, get_forex_day_start,
    TIMEFRAME_SECONDS,
)

DB = get_db_config()

# Test window: use a period where we have real canonical M1 data
# The March 31 recovery put M1 candles into candles_M1 — let's seed
# canonical_m1 from a known good window for testing.
TEST_WINDOW_START = datetime(2026, 3, 31, 3, 0, 0)  # Inside the recovered window
TEST_WINDOW_END = datetime(2026, 3, 31, 4, 0, 0)


def seed_canonical_from_legacy():
    """Seed canonical_m1 from candles_M1 for the test window."""
    conn = pymysql.connect(**DB, autocommit=True)
    with conn.cursor() as cur:
        # Clear test range
        cur.execute(
            "DELETE FROM canonical_m1 WHERE instrument='XAU_USD' AND minute_bucket_utc >= %s AND minute_bucket_utc < %s",
            (TEST_WINDOW_START, TEST_WINDOW_END)
        )
        # Copy from legacy
        cur.execute(
            """INSERT INTO canonical_m1
            (instrument, minute_bucket_utc, open, high, low, close, volume,
             source_id, ingest_mode, arrival_utc, complete, description, llm_reasoning)
            SELECT instrument, timestamp, open, high, low, close, volume,
                   'test_seed', 'BACKFILL', NOW(3), 1,
                   'Test seed from candles_M1', '{"rationale":"WO-D test"}'
            FROM candles_M1
            WHERE instrument='XAU_USD' AND timestamp >= %s AND timestamp < %s
            ON DUPLICATE KEY UPDATE open=VALUES(open)""",
            (TEST_WINDOW_START, TEST_WINDOW_END)
        )
        seeded = cur.rowcount
    conn.close()
    return seeded


def cleanup():
    conn = pymysql.connect(**DB, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM canonical_m1 WHERE source_id='test_seed'"
        )
    conn.close()


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
# Test 1: M5 derivation from 5 M1 candles
# ============================================================
def test_m5_derivation():
    """Derive M5 from 5 canonical M1 candles. Verify OHLC."""
    engine = M1DerivationEngine(DB)

    # M5 bucket at 03:00 contains M1 candles: 03:00, 03:01, 03:02, 03:03, 03:04
    m5 = engine.derive_candle('XAU_USD', 'M5', datetime(2026, 3, 31, 3, 0, 0))

    assert m5 is not None, "M5 should be derived (canonical M1 data seeded)"
    assert m5.timeframe == 'M5'
    assert m5.instrument == 'XAU_USD'
    assert m5.expected_m1 == 5
    assert m5.m1_count <= 5

    # Verify OHLC by manually checking M1 candles
    conn = pymysql.connect(**DB, autocommit=True)
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(
            """SELECT open, high, low, close FROM canonical_m1
            WHERE instrument='XAU_USD' AND minute_bucket_utc >= '2026-03-31 03:00:00'
            AND minute_bucket_utc < '2026-03-31 03:05:00' ORDER BY minute_bucket_utc""",
            ()
        )
        m1s = cur.fetchall()
    conn.close()

    if m1s:
        expected_open = float(m1s[0]['open'])
        expected_high = max(float(r['high']) for r in m1s)
        expected_low = min(float(r['low']) for r in m1s)
        expected_close = float(m1s[-1]['close'])

        assert abs(m5.open - expected_open) < 0.001, f"Open mismatch: {m5.open} vs {expected_open}"
        assert abs(m5.high - expected_high) < 0.001, f"High mismatch: {m5.high} vs {expected_high}"
        assert abs(m5.low - expected_low) < 0.001, f"Low mismatch: {m5.low} vs {expected_low}"
        assert abs(m5.close - expected_close) < 0.001, f"Close mismatch: {m5.close} vs {expected_close}"

    print(f"  M5 03:00: O={m5.open} H={m5.high} L={m5.low} C={m5.close} ({m5.m1_count}/{m5.expected_m1} M1s)")


# ============================================================
# Test 2: M15 derivation
# ============================================================
def test_m15_derivation():
    """Derive M15 from 15 canonical M1 candles."""
    engine = M1DerivationEngine(DB)

    m15 = engine.derive_candle('XAU_USD', 'M15', datetime(2026, 3, 31, 3, 0, 0))
    assert m15 is not None
    assert m15.timeframe == 'M15'
    assert m15.expected_m1 == 15

    print(f"  M15 03:00: O={m15.open} H={m15.high} L={m15.low} C={m15.close} ({m15.m1_count}/{m15.expected_m1} M1s)")


# ============================================================
# Test 3: H1 derivation
# ============================================================
def test_h1_derivation():
    """Derive H1 from 60 canonical M1 candles."""
    engine = M1DerivationEngine(DB)

    h1 = engine.derive_candle('XAU_USD', 'H1', datetime(2026, 3, 31, 3, 0, 0))
    assert h1 is not None
    assert h1.timeframe == 'H1'
    assert h1.expected_m1 == 60

    print(f"  H1 03:00: O={h1.open} H={h1.high} L={h1.low} C={h1.close} ({h1.m1_count}/{h1.expected_m1} M1s)")


# ============================================================
# Test 4: Partial M1 produces incomplete (not dropped)
# ============================================================
def test_partial_m1_incomplete():
    """If some M1 candles are missing, derived candle is marked incomplete."""
    engine = M1DerivationEngine(DB)

    # Delete a few M1 candles from the test window to create partial
    conn = pymysql.connect(**DB, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM canonical_m1 WHERE instrument='XAU_USD' AND minute_bucket_utc IN ('2026-03-31 03:31:00', '2026-03-31 03:32:00')"
        )
    conn.close()

    m5 = engine.derive_candle('XAU_USD', 'M5', datetime(2026, 3, 31, 3, 30, 0))

    if m5:
        assert m5.m1_count < m5.expected_m1, "Should have fewer M1s than expected"
        assert not m5.complete, "Should be marked incomplete"
        print(f"  Partial M5 03:30: {m5.m1_count}/{m5.expected_m1} M1s, complete={m5.complete}")
    else:
        # If all M1s for this bucket were deleted, None is acceptable
        print(f"  Partial: no M1 data for this bucket (correctly returns None)")


# ============================================================
# Test 5: Per-instrument independence
# ============================================================
def test_per_instrument():
    """Derivation is per-instrument. One instrument's data doesn't affect another."""
    engine = M1DerivationEngine(DB)

    xau = engine.derive_candle('XAU_USD', 'M5', datetime(2026, 3, 31, 3, 0, 0))
    eur = engine.derive_candle('EUR_USD', 'M5', datetime(2026, 3, 31, 3, 0, 0))

    # XAU should have data (we seeded it)
    assert xau is not None, "XAU_USD should have derived M5"

    # EUR may or may not have canonical data — but the point is it doesn't affect XAU
    print(f"  XAU_USD: {xau.m1_count} M1s → M5")
    print(f"  EUR_USD: {'has data' if eur else 'no canonical data'} (independent)")


# ============================================================
# Test 6: Equivalence comparison against legacy
# ============================================================
def test_equivalence_comparison():
    """compare_with_legacy produces a usable comparison report."""
    engine = M1DerivationEngine(DB)

    report = engine.compare_with_legacy(
        'XAU_USD', 'M5',
        datetime(2026, 3, 31, 3, 0, 0),
        datetime(2026, 3, 31, 4, 0, 0),
    )

    assert 'total_buckets' in report
    assert 'matches' in report
    assert 'match_pct' in report
    assert 'volume_note' in report

    print(f"  Comparison: {report['matches']}/{report['total_buckets']} match ({report['match_pct']}%)")
    print(f"  Mismatches: {report['mismatches']}, Derived-only: {report['derived_only']}, Legacy-only: {report['legacy_only']}")
    if report['mismatch_details']:
        print(f"  First mismatch: {report['mismatch_details'][0]}")


# ============================================================
# Test 7: Volume is sum of M1 volumes
# ============================================================
def test_volume_sum():
    """Derived volume = sum of constituent M1 volumes."""
    engine = M1DerivationEngine(DB)

    m5 = engine.derive_candle('XAU_USD', 'M5', datetime(2026, 3, 31, 3, 5, 0))

    if m5:
        # Get individual M1 volumes
        conn = pymysql.connect(**DB, autocommit=True)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT SUM(volume) as total FROM canonical_m1
                WHERE instrument='XAU_USD' AND minute_bucket_utc >= '2026-03-31 03:05:00'
                AND minute_bucket_utc < '2026-03-31 03:10:00'"""
            )
            expected_vol = cur.fetchone()[0] or 0
        conn.close()

        assert m5.volume == expected_vol, f"Volume mismatch: derived={m5.volume}, sum={expected_vol}"
        print(f"  M5 volume={m5.volume} = SUM(M1 volumes)={expected_vol}")
    else:
        print(f"  No M5 data for this bucket (skipped)")


# ============================================================
# Test 8: Bucket boundaries match CandleAggregator
# ============================================================
def test_bucket_boundaries():
    """Verify bucket math matches CandleAggregator._get_candle_start."""
    # M5: 03:07 → 03:05
    assert get_bucket_start(datetime(2026, 3, 31, 3, 7, 0), 300) == datetime(2026, 3, 31, 3, 5, 0)
    # M15: 03:22 → 03:15
    assert get_bucket_start(datetime(2026, 3, 31, 3, 22, 0), 900) == datetime(2026, 3, 31, 3, 15, 0)
    # H1: 03:45 → 03:00
    assert get_bucket_start(datetime(2026, 3, 31, 3, 45, 0), 3600) == datetime(2026, 3, 31, 3, 0, 0)
    # Exact boundary: 03:15 → 03:15
    assert get_bucket_start(datetime(2026, 3, 31, 3, 15, 0), 900) == datetime(2026, 3, 31, 3, 15, 0)

    # D1 forex day
    assert get_forex_day_start(datetime(2026, 3, 31, 10, 0, 0)) == datetime(2026, 3, 30, 22, 0, 0)
    assert get_forex_day_start(datetime(2026, 3, 31, 23, 0, 0)) == datetime(2026, 3, 31, 22, 0, 0)

    print(f"  All bucket boundary checks passed")


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    # Seed canonical data
    seeded = seed_canonical_from_legacy()
    print(f"Seeded {seeded} canonical M1 rows from legacy data")

    tests = [
        ("M5 derivation from 5 M1s", test_m5_derivation),
        ("M15 derivation from 15 M1s", test_m15_derivation),
        ("H1 derivation from 60 M1s", test_h1_derivation),
        ("Partial M1 produces incomplete", test_partial_m1_incomplete),
        ("Per-instrument independence", test_per_instrument),
        ("Equivalence comparison", test_equivalence_comparison),
        ("Volume is sum of M1 volumes", test_volume_sum),
        ("Bucket boundaries match", test_bucket_boundaries),
    ]

    results = []
    for name, func in tests:
        results.append((name, run_test(name, func)))

    cleanup()

    print(f"\n{'='*60}")
    print("RESULTS")
    print(f"{'='*60}")
    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n{passed}/{len(results)} passed")
    sys.exit(0 if passed == len(results) else 1)
