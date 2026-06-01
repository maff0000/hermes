"""WO-HELM-HERMES-STREAM-STATE-SCHEMA-FIX-0001 — tests.

Proves migration 013 widens hermes_service_health.stream_state to admit every
value the runtime emits — specifically PARTIAL_FLOWING, whose absence caused the
'(1265) Data truncated for column stream_state' defect that left
hermes_service_health unpopulated (process-level health contract dead).

Pure unit tests: no DB writes, no network, no fabric, no live migration apply.
The migration is NOT applied to any database by this test; it is validated by
parsing the migration SQL and the StreamState class and proving set parity,
append-only safety, and ordinal preservation.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE_DIR = HERE.parent
# Path setup (matches conftest.py / sibling test convention)
sys.path = [p for p in sys.path if 'config' not in p or 'tradingSignals' in p]
sys.path.insert(0, str(BASE_DIR))

from utils.watchdog import StreamState  # noqa: E402

MIGRATION_013 = BASE_DIR / 'migrations' / '013_stream_state_partial_flowing.sql'
MIGRATION_001 = BASE_DIR / 'migrations' / '001_hermes_health_and_incidents.sql'


def _strip_sql_comments(sql_text):
    """Remove SQL '--' line/inline comments so prose that mentions
    'stream_state ENUM' or 'truncated' cannot be mistaken for real DDL."""
    out = []
    for line in sql_text.splitlines():
        i = line.find('--')
        if i != -1:
            line = line[:i]
        out.append(line)
    return '\n'.join(out)


def _stream_state_enum_values(sql_text):
    """Extract the ordered enum value list of the stream_state column from a
    migration's ENUM(...) definition. Returns values in declared order."""
    code = _strip_sql_comments(sql_text)
    m = re.search(r"stream_state\s+ENUM\s*\((.*?)\)", code,
                  re.IGNORECASE | re.DOTALL)
    assert m, "stream_state ENUM(...) not found in SQL"
    return re.findall(r"'([^']+)'", m.group(1))


def _streamstate_code_values():
    return {
        v for k, v in vars(StreamState).items()
        if not k.startswith('_') and isinstance(v, str)
    }


class TestStreamStateClassValues(unittest.TestCase):
    def test_partial_flowing_is_emitted_by_code(self):
        # Regression anchor: the value at the heart of the defect.
        self.assertIn('PARTIAL_FLOWING', _streamstate_code_values())


class TestMigration013(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MIGRATION_013.exists(), "migration 013 missing")
        self.sql = MIGRATION_013.read_text()
        self.sql_code = _strip_sql_comments(self.sql)
        self.enum013 = _stream_state_enum_values(self.sql)
        self.enum001 = _stream_state_enum_values(MIGRATION_001.read_text())
        self.code_values = _streamstate_code_values()

    def test_all_code_states_fit_enum(self):
        """Every StreamState value the runtime can write must be in the enum."""
        missing = sorted(self.code_values - set(self.enum013))
        self.assertEqual(missing, [], f"enum does not admit emitted states: {missing}")

    def test_partial_flowing_present(self):
        self.assertIn('PARTIAL_FLOWING', self.enum013)

    def test_append_only_no_removal(self):
        """All original (migration 001) enum values must still be present."""
        removed = sorted(set(self.enum001) - set(self.enum013))
        self.assertEqual(removed, [], f"migration removed enum values: {removed}")

    def test_ordinal_preservation(self):
        """Original 7 values keep their original order/position (ordinals 1..7);
        the new value is appended, not inserted — so existing stored rows keep
        their exact meaning."""
        self.assertEqual(self.enum013[:len(self.enum001)], self.enum001,
                         "original enum ordinals not preserved (value inserted/reordered)")
        added = [v for v in self.enum013 if v not in self.enum001]
        self.assertEqual(added, ['PARTIAL_FLOWING'])
        self.assertEqual(self.enum013[-1], 'PARTIAL_FLOWING')

    def test_no_destructive_sql(self):
        # Check the SQL with comments stripped — prose must not trip this.
        upper = self.sql_code.upper()
        for forbidden in ('DROP TABLE', 'DROP COLUMN', 'DELETE ', 'TRUNCATE'):
            self.assertNotIn(forbidden, upper, f"forbidden destructive SQL: {forbidden}")
        self.assertIn('ALTER TABLE', upper)
        self.assertIn('MODIFY COLUMN', upper)

    def test_only_touches_stream_state(self):
        """Scope guard: exactly one ALTER TABLE, targeting hermes_service_health
        / stream_state only."""
        self.assertIn('hermes_service_health', self.sql_code)
        alters = re.findall(r"ALTER\s+TABLE", self.sql_code, re.IGNORECASE)
        self.assertEqual(len(alters), 1, "expected exactly one ALTER TABLE statement")


class TestRuntimeWritePathCoverage(unittest.TestCase):
    """Runtime-write coverage (DB-free): the value the watchdog writes on the
    FLOWING->PARTIAL_FLOWING transition must be enum-covered. A full DB
    round-trip write test requires the migration applied to a database, which
    this WO is explicitly forbidden from doing (R2D2 audit gates runtime
    action). This proves the emitted constant is covered instead."""
    def setUp(self):
        self.enum013 = _stream_state_enum_values(MIGRATION_013.read_text())
        self.watchdog_src = (BASE_DIR / 'utils' / 'watchdog.py').read_text()

    def test_runtime_emits_partial_flowing(self):
        self.assertIn('set_stream_state(StreamState.PARTIAL_FLOWING)', self.watchdog_src)

    def test_emitted_value_is_enum_covered(self):
        self.assertEqual(StreamState.PARTIAL_FLOWING, 'PARTIAL_FLOWING')
        self.assertIn(StreamState.PARTIAL_FLOWING, self.enum013)


if __name__ == '__main__':
    unittest.main(verbosity=2)
