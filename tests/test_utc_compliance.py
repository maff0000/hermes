"""WO-TRADING-SIGNALS-UTC-SWEEP-0001 — UTC compliance regression test.

Architect rule for this WO: forbid SQL ``NOW()`` / ``UTC_TIMESTAMP()`` and
naked ``datetime.now()`` outside of comments / docstrings, in the live signal
+ level path. ``datetime.utcnow()`` exists in some of these files outside the
R2D2 priority bundle (B1/B2/B3/H6/L2) and is queued for a follow-up sweep —
this test does NOT yet ban ``datetime.utcnow()`` repo-wide.

Run: ``python3 -m unittest tests.test_utc_compliance -v``
"""
from __future__ import annotations

import os
import re
import unittest


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


# Files in scope of WO-TRADING-SIGNALS-UTC-SWEEP-0001 (R2D2 priority bundle)
PROTECTED_FILES = (
    "healthcheck/signal_health.py",
    "utils/break_detector.py",
    "utils/level_engine.py",
    "utils/db_writer.py",
    "scripts/archive_old_data.py",
    "utils/atr_calculator.py",
)


def _strip_comments_and_strings(src: str) -> str:
    """Remove ``# ...`` line comments, triple-quoted strings, and single/double-
    quoted string literals so the regex check ignores prose mentions of the
    forbidden tokens. Keeps newlines for grep accuracy."""
    out: list[str] = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        # Triple-quoted string
        if src[i:i + 3] in ('"""', "'''"):
            quote = src[i:i + 3]
            j = src.find(quote, i + 3)
            if j == -1:
                break
            out.append("\n" * src[i:j].count("\n"))
            i = j + 3
            continue
        # Line comment
        if c == "#":
            j = src.find("\n", i)
            if j == -1:
                break
            i = j  # keep the newline
            continue
        # Single-line string literal
        if c in ("'", '"'):
            quote = c
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == quote:
                    break
                if src[j] == "\n":
                    break
                j += 1
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


_BANNED_RX = (
    re.compile(r"\bNOW\s*\(\s*\)"),
    re.compile(r"\bUTC_TIMESTAMP\s*\(\s*\)"),
    re.compile(r"\bdatetime\.now\s*\(\s*\)"),
    re.compile(r"\btime\.time\s*\(\s*\)"),
)


def _read(rel: str) -> str:
    with open(os.path.join(ROOT, rel)) as f:
        return f.read()


class TestNoBannedTimeCallsInProtectedFiles(unittest.TestCase):
    """No SQL NOW(), UTC_TIMESTAMP, naked datetime.now() (zero-arg), or
    time.time() in the live signal + level files this WO covers."""

    def test_protected_files_are_clean(self):
        for rel in PROTECTED_FILES:
            stripped = _strip_comments_and_strings(_read(rel))
            for rx in _BANNED_RX:
                m = rx.search(stripped)
                self.assertIsNone(
                    m,
                    f"{rel}: banned token {rx.pattern!r} present at offset "
                    f"{m.start() if m else 'n/a'}",
                )


class TestSignalHealthCutoffIsUTC(unittest.TestCase):
    """B1: signal_health ATR-baseline query passes a timezone-aware UTC
    cutoff into parameterized SQL, not server-local NOW()."""

    def test_atr_baseline_uses_utc_cutoff(self):
        src = _read("healthcheck/signal_health.py")
        self.assertIn(
            "cutoff_utc = datetime.now(timezone.utc) - timedelta(hours=1)",
            src,
        )
        self.assertIn("AND timestamp >= %s", src)
        self.assertNotIn("DATE_SUB(NOW()", src)


class TestHermesLevelsCutoffIsUTC(unittest.TestCase):
    """B2 / B3: break_detector + level_engine queries on
    hermes_levels.valid_until use a parameterized timezone-aware UTC value."""

    def test_break_detector(self):
        src = _read("utils/break_detector.py")
        self.assertIn("now_utc = datetime.now(timezone.utc)", src)
        self.assertIn("valid_until > %s", src)

    def test_level_engine(self):
        src = _read("utils/level_engine.py")
        self.assertIn("now_utc = datetime.now(timezone.utc)", src)
        self.assertIn("valid_until > %s", src)


class TestArchiveCutoffIsUTC(unittest.TestCase):
    """H6: db_writer + archive_old_data cutoffs are UTC-aware so they
    compare apples-to-apples with UTC-stored timestamps."""

    def test_db_writer_archive_cutoff(self):
        src = _read("utils/db_writer.py")
        self.assertIn(
            "cutoff = datetime.now(timezone.utc) - timedelta(days=months_to_keep * 30)",
            src,
        )

    def test_archive_old_data_cutoff(self):
        src = _read("scripts/archive_old_data.py")
        self.assertIn(
            "cutoff = datetime.now(timezone.utc) - timedelta(days=MONTHS_TO_KEEP * 30)",
            src,
        )


class TestAtrCalculatorCacheIsUTC(unittest.TestCase):
    """L2: atr_calculator cache TTL — internal-relative comparison, but
    must use timezone-aware UTC for rule consistency."""

    def test_cache_uses_utc_aware_now(self):
        src = _read("utils/atr_calculator.py")
        self.assertIn(
            "(datetime.now(timezone.utc) - cached_time).total_seconds()",
            src,
        )
        self.assertIn(
            "self._cache[cache_key] = (atr, datetime.now(timezone.utc))",
            src,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
