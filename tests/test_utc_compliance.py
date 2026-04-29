"""WO-TRADING-SIGNALS-UTCNOW-CLEANUP-0001 — extended UTC compliance regression test.

Builds on WO-TRADING-SIGNALS-UTC-SWEEP-0001 (PR #5). Now bans ``datetime.utcnow()``
repo-wide on the protected runtime + data-ingestion paths AND covers shell
ops scripts that previously used ``UTC_TIMESTAMP()``.

Architect closure-compliance bar: this test is the automated banned-token
scan that prevents stale-Redis-time-bombs and BST/UTC drift from silently
returning to tradingSignals.

Run: ``python3 -m unittest tests.test_utc_compliance -v``
"""
from __future__ import annotations

import os
import re
import unittest
from datetime import datetime, timedelta, timezone


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


# All Python runtime + data-ingestion files in scope. Pre-WO sweep covered
# the R2D2 priority bundle (B1/B2/B3/H6/L2); this WO extends to every
# datetime.utcnow / time.time / UTC_TIMESTAMP site outside tests, migrations,
# runbooks, and historical evidence docs.
PROTECTED_FILES = (
    # Original sweep
    "healthcheck/signal_health.py",
    "utils/break_detector.py",
    "utils/level_engine.py",
    "utils/db_writer.py",
    "scripts/archive_old_data.py",
    "utils/atr_calculator.py",
    # Added this WO
    "main.py",
    "adapters/oanda.py",
    "adapters/base.py",
    "utils/redis_publisher.py",
    "utils/compression_detector.py",
    "utils/gap_scanner.py",
    "utils/discord_alerts.py",
    "utils/trading_hours.py",
    "services/market-map/market_map.py",
    "scripts/backfill_signals_m15.py",
    "scripts/backfill_oanda.py",
    "scripts/backfill_signals.py",
)

# Shell ops scripts where the previous policy allowed UTC_TIMESTAMP() —
# now banned per architect rule, replaced by shell-computed CUTOFF_UTC_*.
PROTECTED_SHELL = (
    "ops/canonical_m1_parallel_writer.sh",
    "ops/equivalence_check.sh",
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
        if src[i:i + 3] in ('"""', "'''"):
            quote = src[i:i + 3]
            j = src.find(quote, i + 3)
            if j == -1:
                break
            out.append("\n" * src[i:j].count("\n"))
            i = j + 3
            continue
        if c == "#":
            j = src.find("\n", i)
            if j == -1:
                break
            i = j
            continue
        if c in ("'", '"'):
            quote = c
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == quote or src[j] == "\n":
                    break
                j += 1
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


_BANNED_PY = (
    re.compile(r"\bNOW\s*\(\s*\)"),
    re.compile(r"\bUTC_TIMESTAMP\s*\(\s*\)"),
    re.compile(r"\bdatetime\.now\s*\(\s*\)"),
    re.compile(r"\bdatetime\.utcnow\s*\("),    # WO-TRADING-SIGNALS-UTCNOW-CLEANUP-0001
    re.compile(r"\btime\.time\s*\(\s*\)"),
)

_BANNED_SH = (
    re.compile(r"\bUTC_TIMESTAMP\s*\(\s*\)"),
    re.compile(r"\bNOW\s*\(\s*\)"),
)


def _read(rel: str) -> str:
    with open(os.path.join(ROOT, rel)) as f:
        return f.read()


def _strip_shell(src: str) -> str:
    """For shell scripts, strip `# ...` comments through end-of-line.
    Strings inside heredocs / SQL parameters are preserved intact."""
    out: list[str] = []
    for line in src.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            out.append("\n")
            continue
        # Inline comment: trim trailing # ... unless inside `'` or `"` (heuristic).
        # For our purposes, runtime-banned tokens never appear in trailing comments
        # we care about, so we just keep the line as-is.
        out.append(line)
    return "".join(out)


# --------------------------------------------------------------------------- #
# Banned-token scan
# --------------------------------------------------------------------------- #

class TestNoBannedTimeCallsInPythonProtectedFiles(unittest.TestCase):
    """No SQL NOW(), UTC_TIMESTAMP, naked datetime.now() (zero-arg),
    datetime.utcnow(), or time.time() in the protected Python files.
    Comments and string literals are stripped before the scan."""

    def test_protected_python_files_are_clean(self):
        failures: list[str] = []
        for rel in PROTECTED_FILES:
            stripped = _strip_comments_and_strings(_read(rel))
            for rx in _BANNED_PY:
                m = rx.search(stripped)
                if m:
                    failures.append(
                        f"{rel}: banned token {rx.pattern!r} at offset {m.start()}"
                    )
        self.assertFalse(failures, "\n".join(failures))


class TestNoBannedTokensInShellOps(unittest.TestCase):
    """Shell ops scripts that issue SQL must not use UTC_TIMESTAMP() / NOW().
    Pre-cleanup these scripts inlined SQL with UTC_TIMESTAMP(); post-cleanup
    they compute CUTOFF_UTC_* shell-side via `date -u --date=...`."""

    def test_protected_shell_files_are_clean(self):
        failures: list[str] = []
        for rel in PROTECTED_SHELL:
            stripped = _strip_shell(_read(rel))
            for rx in _BANNED_SH:
                m = rx.search(stripped)
                if m:
                    failures.append(
                        f"{rel}: banned token {rx.pattern!r} at offset {m.start()}"
                    )
        self.assertFalse(failures, "\n".join(failures))


# --------------------------------------------------------------------------- #
# Canary — banned tokens DO appear in non-protected files (sanity check that
# the scan logic actually works; this catches a regression where the strip
# function silently zaps everything).
# --------------------------------------------------------------------------- #

class TestScanLogicCanary(unittest.TestCase):
    """Sanity: feed the scan a synthetic hostile string; it must catch it."""

    def test_scan_catches_naive_utcnow(self):
        hostile = "result = datetime.utcnow()"
        stripped = _strip_comments_and_strings(hostile)
        rx = re.compile(r"\bdatetime\.utcnow\s*\(")
        self.assertIsNotNone(rx.search(stripped),
                             "scan logic broken — would not catch utcnow")

    def test_scan_catches_time_time(self):
        hostile = "elapsed = time.time() - start"
        stripped = _strip_comments_and_strings(hostile)
        rx = re.compile(r"\btime\.time\s*\(\s*\)")
        self.assertIsNotNone(rx.search(stripped),
                             "scan logic broken — would not catch time.time()")

    def test_scan_does_not_false_positive_on_comment(self):
        benign = "# datetime.utcnow() is now banned, so we use timezone.utc instead"
        stripped = _strip_comments_and_strings(benign)
        rx = re.compile(r"\bdatetime\.utcnow\s*\(")
        self.assertIsNone(rx.search(stripped),
                          "scan over-matches: hits comments")

    def test_scan_does_not_false_positive_on_docstring(self):
        benign = '"""historical: pre-WO this used datetime.utcnow() everywhere"""'
        stripped = _strip_comments_and_strings(benign)
        rx = re.compile(r"\bdatetime\.utcnow\s*\(")
        self.assertIsNone(rx.search(stripped),
                          "scan over-matches: hits docstrings")


# --------------------------------------------------------------------------- #
# Truth invariants — the post-cleanup substitution preserves UTC semantics.
# --------------------------------------------------------------------------- #

class TestUTCSubstitutionTruthInvariant(unittest.TestCase):
    """Invariants that prove the cleanup is behavior-equivalent for UTC math.

    These tests are deliberately framework-free; they exercise the canonical
    substitution patterns directly so any drift in those patterns is caught
    before it reaches deployed code."""

    def test_utcnow_replacement_is_timezone_aware_utc(self):
        # Old: datetime.utcnow() returns naive datetime
        # New: datetime.now(timezone.utc) returns aware datetime
        # Same instant; new form is timezone-aware.
        new = datetime.now(timezone.utc)
        self.assertIsNotNone(new.tzinfo)
        self.assertEqual(new.utcoffset(), timedelta(0))

    def test_time_time_replacement_is_posix_seconds(self):
        # Old: time.time() returns POSIX seconds since UTC epoch
        # New: datetime.now(timezone.utc).timestamp() also returns POSIX seconds
        # Numerically equivalent (within a few microseconds).
        new = datetime.now(timezone.utc).timestamp()
        self.assertIsInstance(new, float)
        # POSIX seconds since 1970-01-01 — must be > 2025-01-01 epoch.
        self.assertGreater(new, 1735689600.0)

    def test_round_trip_naive_utcnow_to_aware_now(self):
        # If a caller writes a timestamp with one form and reads with the
        # other, the same instant is preserved.
        instant_naive = datetime(2026, 4, 29, 15, 0, 0)  # what utcnow would have returned
        instant_aware = datetime(2026, 4, 29, 15, 0, 0, tzinfo=timezone.utc)
        # Same wall-clock; the aware version has +00:00 offset.
        self.assertEqual(
            instant_naive.replace(tzinfo=timezone.utc),
            instant_aware,
        )

    def test_iso_round_trip_preserves_utc(self):
        emitted = datetime.now(timezone.utc).isoformat()
        parsed = datetime.fromisoformat(emitted)
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(parsed.utcoffset(), timedelta(0))


# --------------------------------------------------------------------------- #
# Tripwire — the test universe contains what we expect.
# --------------------------------------------------------------------------- #

class TestProtectedFilesAllExist(unittest.TestCase):
    """If a protected file is renamed or removed without updating this list,
    the scan would silently shrink. Tripwire that asserts every listed file
    actually exists on disk."""

    def test_all_python_protected_files_exist(self):
        missing = [f for f in PROTECTED_FILES if not os.path.exists(os.path.join(ROOT, f))]
        self.assertFalse(missing, f"missing protected files: {missing}")

    def test_all_shell_protected_files_exist(self):
        missing = [f for f in PROTECTED_SHELL if not os.path.exists(os.path.join(ROOT, f))]
        self.assertFalse(missing, f"missing protected shell files: {missing}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
