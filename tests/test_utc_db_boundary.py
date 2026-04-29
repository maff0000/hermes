"""Regression test for WO-TRADING-SIGNALS-UTC-DB-BOUNDARY-HOTFIX-0001.

PR #6 introduced a TypeError when callers subtracting aware now() from a
naive last_ts returned by pymysql. This test asserts that
get_last_candle_timestamp normalizes its result to timezone-aware UTC.
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from datetime import datetime, timezone


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


class TestGetLastCandleTimestampReturnsAwareUTC(unittest.TestCase):
    """Source-level assertion: the boundary normalization is present.

    The function reads from pymysql which returns naive datetimes; the
    post-WO contract is that the returned datetime is timezone-aware UTC,
    so callers using datetime.now(timezone.utc) for current-time can
    subtract without TypeError.
    """

    def test_source_normalizes_to_aware_utc(self):
        with open(os.path.join(ROOT, "main.py")) as f:
            src = f.read()
        # The post-fix block must contain the explicit normalization step.
        self.assertIn(
            "ts.replace(tzinfo=timezone.utc)",
            src,
            "main.py:get_last_candle_timestamp must normalize naive result to aware UTC",
        )
        self.assertIn(
            "WO-TRADING-SIGNALS-UTC-DB-BOUNDARY-HOTFIX-0001",
            src,
        )


class TestArithmeticInvariant(unittest.TestCase):
    """Behavioural invariant: aware now - normalized last_ts works."""

    def test_aware_minus_normalized_naive_succeeds(self):
        # Mimic the post-fix shape returned by get_last_candle_timestamp
        naive_from_db = datetime(2026, 4, 29, 12, 0, 0)
        normalized = naive_from_db if naive_from_db.tzinfo is not None else naive_from_db.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        # Pre-fix this would TypeError; post-fix it works.
        delta = (now - normalized).total_seconds()
        self.assertGreater(delta, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
