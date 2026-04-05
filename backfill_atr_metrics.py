#!/usr/bin/env python3
"""
STORY-D002-09: Backfill ATR baseline metrics for historical signals
HERMES Signal Enrichment - Day State Classification Support

Computes and updates:
- atr_baseline: 20-bar rolling average of ATR-14
- atr_opening_shock: First 30 min ATR / baseline ratio
- atr_day_ratio: Current day range / baseline

Usage:
    python backfill_atr_metrics.py [--instrument XAU_USD] [--dry-run]
"""

import os
import sys
import argparse
import pymysql
from datetime import datetime, timedelta
from collections import defaultdict

# Add parent for env_config
sys.path.insert(0, os.path.dirname(__file__))
from env_config import get_db_config


def get_db_connection():
    """Get database connection."""
    cfg = get_db_config()
    return pymysql.connect(
        host=cfg['host'],
        port=cfg['port'],
        user=cfg['user'],
        password=cfg['password'],
        database=cfg['database'],
        autocommit=False
    )


def backfill_instrument(conn, instrument: str, dry_run: bool = False):
    """
    Backfill ATR metrics for a single instrument.

    Args:
        conn: Database connection
        instrument: Instrument code
        dry_run: If True, don't actually update

    Returns:
        Number of records updated
    """
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    # Get all signals ordered by timestamp with candle data
    print(f"  Fetching signals for {instrument}...")
    cursor.execute("""
        SELECT s.id, s.timestamp, s.atr_14,
               c.high as candle_high, c.low as candle_low
        FROM signals s
        LEFT JOIN candles_M5 c ON c.instrument = s.instrument AND c.timestamp = s.timestamp
        WHERE s.instrument = %s
        ORDER BY s.timestamp ASC
    """, (instrument,))

    signals = cursor.fetchall()
    print(f"  Found {len(signals)} signals")

    if not signals:
        return 0

    # Track state for rolling calculations
    atr_history = []  # Last 20 ATR values for baseline
    day_state = {}    # Current day tracking: date, high, low, opening_shock

    updates = []
    batch_size = 1000

    for i, sig in enumerate(signals):
        sig_id = sig['id']
        timestamp = sig['timestamp']
        atr_14 = float(sig['atr_14'] or 0)
        candle_high = float(sig['candle_high'] or 0)
        candle_low = float(sig['candle_low'] or 0)

        sig_date = timestamp.date()

        # Update ATR history
        if atr_14 > 0:
            atr_history.append(atr_14)
            if len(atr_history) > 20:
                atr_history = atr_history[-20:]

        # Compute ATR baseline
        atr_baseline = 0.0
        if atr_history:
            atr_baseline = round(sum(atr_history) / len(atr_history), 5)

        # Track day state
        is_new_day = day_state.get('date') != sig_date

        if is_new_day:
            # New day - reset tracking
            day_state = {
                'date': sig_date,
                'high': candle_high,
                'low': candle_low,
                'opening_shock': 0.0,
                'shock_frozen': False
            }
        else:
            # Update day high/low
            if candle_high > 0:
                day_state['high'] = max(day_state.get('high', 0), candle_high)
            if candle_low > 0 and day_state.get('low', 0) > 0:
                day_state['low'] = min(day_state['low'], candle_low)
            elif candle_low > 0:
                day_state['low'] = candle_low

        # Compute opening shock (first 30 min of day = 6 M5 bars)
        day_start = datetime.combine(sig_date, datetime.min.time())
        minutes_since_open = (timestamp - day_start).seconds // 60

        atr_opening_shock = 0.0
        if minutes_since_open <= 30 and atr_baseline > 0:
            atr_opening_shock = round(atr_14 / atr_baseline, 4) if atr_baseline > 0 else 0.0
            day_state['opening_shock'] = atr_opening_shock
        else:
            # Use frozen value from earlier
            atr_opening_shock = day_state.get('opening_shock', 0.0)

        # Compute day ratio
        atr_day_ratio = 0.0
        day_range = day_state.get('high', 0) - day_state.get('low', 0)
        if day_range > 0 and atr_baseline > 0:
            atr_day_ratio = round(day_range / atr_baseline, 4)

        updates.append((atr_baseline, atr_opening_shock, atr_day_ratio, sig_id))

        # Batch update
        if len(updates) >= batch_size:
            if not dry_run:
                cursor.executemany("""
                    UPDATE signals SET
                        atr_baseline = %s,
                        atr_opening_shock = %s,
                        atr_day_ratio = %s
                    WHERE id = %s
                """, updates)
                conn.commit()
            print(f"    Updated {i+1}/{len(signals)} ({100*(i+1)//len(signals)}%)")
            updates = []

    # Final batch
    if updates:
        if not dry_run:
            cursor.executemany("""
                UPDATE signals SET
                    atr_baseline = %s,
                    atr_opening_shock = %s,
                    atr_day_ratio = %s
                WHERE id = %s
            """, updates)
            conn.commit()
        print(f"    Updated {len(signals)}/{len(signals)} (100%)")

    cursor.close()
    return len(signals)


def main():
    parser = argparse.ArgumentParser(description='Backfill ATR baseline metrics')
    parser.add_argument('--instrument', type=str, help='Single instrument to backfill')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')
    args = parser.parse_args()

    print("=" * 60)
    print("STORY-D002-09: ATR Baseline Metrics Backfill")
    print("=" * 60)

    if args.dry_run:
        print("DRY RUN MODE - No changes will be made")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Get instruments to process
    if args.instrument:
        instruments = [args.instrument]
    else:
        cursor.execute("SELECT DISTINCT instrument FROM signals ORDER BY instrument")
        instruments = [row[0] for row in cursor.fetchall()]

    print(f"\nInstruments to process: {instruments}")
    print()

    total_updated = 0
    for instrument in instruments:
        print(f"Processing {instrument}...")
        count = backfill_instrument(conn, instrument, args.dry_run)
        total_updated += count
        print(f"  Completed: {count} signals")
        print()

    print("=" * 60)
    print(f"TOTAL: {total_updated} signals {'would be ' if args.dry_run else ''}updated")
    print("=" * 60)

    conn.close()


if __name__ == '__main__':
    main()
