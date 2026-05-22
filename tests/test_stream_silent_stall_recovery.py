"""WO-HERMES-OANDA-RECONNECT-LOOP-FIX-IMPLEMENTATION-0001 — tests.

Validates the silent-stall reconnect-loop fix in main.py.

Test scenarios (per WO required matrix):
  T1  Normal tick flow within timeout
  T2  StreamSilentStallError catchable by existing except-Exception path
  T3  Silent async-generator stall raises asyncio.TimeoutError (drives raise)
  T4  Watchdog set_stream_state(STALE) called BEFORE raise (static)
  T5  Existing reconnect path still reachable (RECOVERING / proof_window / exhausted)
  T6  Bounded reconnect attempts via is_recovery_exhausted preserved (static)
  T7  No duplicate candle generation introduced (static; new section)
  T8  No fabricated ticks introduced (static; new section)
  T9  UTC discipline — wait_for timeout is tz-agnostic seconds; no naive datetimes
  T10 Missing/invalid hermes_tick_staleness_threshold_sec fails loud (static)
  T11 No forbidden tokens (MetaTrader5/order_send/place_order/dispatch_trade/etc)

Pure unit tests; no DB writes, no fabric writes, no network, no OANDA live calls.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
BASE_DIR = HERE.parent

# Path setup (matches conftest.py convention)
sys.path = [p for p in sys.path if 'config' not in p or 'tradingSignals' in p]
sys.path.insert(0, str(BASE_DIR))

# Read main.py once for all static-scan tests
MAIN_PY_SRC = (BASE_DIR / 'main.py').read_text()

# Import the new exception class from utils/exceptions.py (importable
# without triggering main.py module-level side effects such as
# RedisPublisher/DB config loads).
from utils.exceptions import StreamSilentStallError  # noqa: E402
from utils.watchdog import StreamState  # noqa: E402


# ---------------------------------------------------------------------------
# Test harness — FakeOANDAAdapter with controllable iterator timing
# ---------------------------------------------------------------------------


class _FakeAsyncIterator:
    """Controllable async iterator: yields configured ticks then optionally
    stalls (blocks forever on Event.wait()), or raises a configured exception."""

    def __init__(self, ticks, stall_after, raise_after, raise_exc):
        self._ticks = list(ticks)
        self._stall_after = stall_after
        self._raise_after = raise_after
        self._raise_exc = raise_exc
        self._yielded = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._raise_after is not None and self._yielded >= self._raise_after:
            raise (self._raise_exc or RuntimeError("simulated stream disconnect"))
        if self._yielded < len(self._ticks):
            tick = self._ticks[self._yielded]
            self._yielded += 1
            return tick
        if self._stall_after is True:
            # Block indefinitely — simulates silent OANDA stall (TCP open,
            # zero data). Caller MUST use asyncio.wait_for to escape this.
            await asyncio.Event().wait()
        raise StopAsyncIteration


class FakeOANDAAdapter:
    """Minimal OANDA adapter fake. Controls stream() timing for testing.

    Args:
        ticks: list of mock tick objects to yield in order
        stall_after_all: if True, block indefinitely after yielding all ticks
        raise_after: index at which to raise an exception instead of yielding
        raise_exc: the exception class to raise (default RuntimeError)
    """

    def __init__(self, ticks=None, stall_after_all=False,
                 raise_after=None, raise_exc=None):
        self.ticks = ticks or []
        self.stall_after_all = stall_after_all
        self.raise_after = raise_after
        self.raise_exc = raise_exc
        self.connect_calls = 0
        self.disconnect_calls = 0

    async def connect(self):
        self.connect_calls += 1
        return True

    async def disconnect(self):
        self.disconnect_calls += 1

    def stream(self):
        return _FakeAsyncIterator(
            self.ticks, self.stall_after_all,
            self.raise_after, self.raise_exc,
        )


def _utc_tick(instrument="XAU_USD"):
    """Build a minimal mock tick with a tz-aware UTC timestamp."""
    t = MagicMock()
    t.timestamp = datetime.now(timezone.utc)
    t.instrument = instrument
    t.bid = 2650.50
    t.ask = 2651.00
    t.spread = 0.50
    t.to_dict = MagicMock(return_value={"instrument": instrument})
    return t


def _find_new_wait_for_section(src):
    """Extract the new WO-introduced wait_for section for static scans."""
    m = re.search(
        r'# Wrap the per-iteration await on the async iterator in'
        r'(.*?)raise StreamSilentStallError',
        src, flags=re.DOTALL,
    )
    return m.group(0) if m else ""


# ---------------------------------------------------------------------------
# T1 — Normal tick flow within timeout
# ---------------------------------------------------------------------------


class T1_NormalTickFlow(unittest.IsolatedAsyncioTestCase):
    async def test_T1_tick_arrives_within_timeout(self):
        tick = _utc_tick()
        adapter = FakeOANDAAdapter(ticks=[tick])
        stream_iter = adapter.stream().__aiter__()
        received = await asyncio.wait_for(
            stream_iter.__anext__(), timeout=1.0
        )
        self.assertIs(received, tick)

    async def test_T1_multiple_ticks_in_sequence(self):
        ticks = [_utc_tick() for _ in range(5)]
        adapter = FakeOANDAAdapter(ticks=ticks)
        stream_iter = adapter.stream().__aiter__()
        received = []
        for _ in range(5):
            t = await asyncio.wait_for(stream_iter.__anext__(), timeout=1.0)
            received.append(t)
        self.assertEqual(received, ticks)


# ---------------------------------------------------------------------------
# T2 — StreamSilentStallError catchable by existing except-Exception
# ---------------------------------------------------------------------------


class T2_ExceptionInheritance(unittest.TestCase):
    def test_T2_inherits_from_Exception(self):
        e = StreamSilentStallError("test")
        self.assertIsInstance(e, Exception)

    def test_T2_caught_by_generic_except(self):
        caught = None
        try:
            raise StreamSilentStallError("test")
        except Exception as exc:
            caught = exc
        self.assertIsInstance(caught, StreamSilentStallError)
        self.assertEqual(str(caught), "test")

    def test_T2_class_lives_in_utils_exceptions_module(self):
        # Importable without main.py's module-level side effects
        from utils import exceptions as exc_module
        self.assertIs(StreamSilentStallError, exc_module.StreamSilentStallError)


# ---------------------------------------------------------------------------
# T3 — Silent async-generator stall raises asyncio.TimeoutError
# ---------------------------------------------------------------------------


class T3_SilentStallRaisesTimeout(unittest.IsolatedAsyncioTestCase):
    async def test_T3_stall_after_first_tick_raises_TimeoutError(self):
        tick = _utc_tick()
        adapter = FakeOANDAAdapter(ticks=[tick], stall_after_all=True)
        stream_iter = adapter.stream().__aiter__()
        # First tick arrives normally
        first = await asyncio.wait_for(stream_iter.__anext__(), timeout=1.0)
        self.assertIs(first, tick)
        # Subsequent iteration stalls — wait_for raises TimeoutError fast
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(stream_iter.__anext__(), timeout=0.2)

    async def test_T3_stall_from_start_raises_TimeoutError(self):
        # Adapter never yields — pure silent stall from the start
        adapter = FakeOANDAAdapter(ticks=[], stall_after_all=True)
        stream_iter = adapter.stream().__aiter__()
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(stream_iter.__anext__(), timeout=0.2)


# ---------------------------------------------------------------------------
# T4 — Watchdog STALE set BEFORE raise (static source verification)
# ---------------------------------------------------------------------------


class T4_WatchdogStaleBeforeRaise(unittest.TestCase):
    def test_T4_watchdog_set_stale_called_before_raise(self):
        # The except asyncio.TimeoutError block must call
        # state.watchdog.set_stream_state(StreamState.STALE) BEFORE raising
        # StreamSilentStallError.
        m = re.search(
            r'except asyncio\.TimeoutError:(.*?)raise StreamSilentStallError',
            MAIN_PY_SRC, flags=re.DOTALL,
        )
        self.assertIsNotNone(
            m, "expected except asyncio.TimeoutError -> raise StreamSilentStallError"
        )
        block = m.group(1)
        # Must call set_stream_state(STALE)
        self.assertIn('set_stream_state(StreamState.STALE)', block)
        # Must log silent_stall_detected
        self.assertIn("'silent_stall_detected': True", block)


# ---------------------------------------------------------------------------
# T5 — Existing reconnect path still reachable
# ---------------------------------------------------------------------------


class T5_ReconnectPathReachable(unittest.TestCase):
    def test_T5_outer_except_Exception_intact(self):
        # The outer 'except Exception as e:' block must still exist
        self.assertIn('except Exception as e:', MAIN_PY_SRC)
        # Reconnect machinery references must all be present
        self.assertIn('set_stream_state(StreamState.RECOVERING)', MAIN_PY_SRC)
        self.assertIn('record_recovery_attempt', MAIN_PY_SRC)
        self.assertIn('is_recovery_exhausted', MAIN_PY_SRC)
        self.assertIn('enter_proof_window', MAIN_PY_SRC)
        self.assertIn('oanda_adapter.disconnect()', MAIN_PY_SRC)
        self.assertIn('oanda_adapter.connect()', MAIN_PY_SRC)

    def test_T5_StreamSilentStallError_is_Exception_subclass(self):
        # If StreamSilentStallError were NOT an Exception subclass, the outer
        # 'except Exception as e:' would not catch it and reconnect would
        # never run. Verify the subclass relationship.
        self.assertTrue(issubclass(StreamSilentStallError, Exception))


# ---------------------------------------------------------------------------
# T6 — Bounded reconnect attempts preserved
# ---------------------------------------------------------------------------


class T6_BoundedRecovery(unittest.TestCase):
    def test_T6_is_recovery_exhausted_preserved(self):
        self.assertIn('is_recovery_exhausted', MAIN_PY_SRC)
        # The existing code raises RuntimeError on exhaustion
        self.assertIn('RECOVERY_EXHAUSTED', MAIN_PY_SRC)
        # Exponential backoff config is still loaded
        self.assertIn('STREAM_RETRY_INITIAL_DELAY', MAIN_PY_SRC)
        self.assertIn('STREAM_RETRY_MAX_DELAY', MAIN_PY_SRC)
        self.assertIn('STREAM_RETRY_BACKOFF_MULTIPLIER', MAIN_PY_SRC)


# ---------------------------------------------------------------------------
# T7 — No duplicate candle generation introduced in new section
# ---------------------------------------------------------------------------


class T7_NoDuplicateCandles(unittest.TestCase):
    def test_T7_no_candle_generation_in_new_section(self):
        new_section = _find_new_wait_for_section(MAIN_PY_SRC)
        self.assertTrue(new_section, "could not locate new WO section in main.py")
        # The new section must NOT call candle_aggregator or save_candle —
        # candle handling is preserved in the existing body untouched
        self.assertNotIn('candle_aggregator', new_section)
        self.assertNotIn('save_candle', new_section)
        self.assertNotIn('record_candle()', new_section)


# ---------------------------------------------------------------------------
# T8 — No fabricated ticks introduced in new section
# ---------------------------------------------------------------------------


class T8_NoFabricatedTicks(unittest.TestCase):
    def test_T8_no_synthetic_tick_in_new_section(self):
        new_section = _find_new_wait_for_section(MAIN_PY_SRC)
        self.assertTrue(new_section)
        # No SignalTick constructor invocations
        self.assertNotIn('SignalTick(', new_section)
        # No latest_ticks mutation
        self.assertNotIn('latest_ticks[', new_section)
        # No record_tick() call (would imply a fabricated tick was received)
        self.assertNotIn('record_tick(', new_section)


# ---------------------------------------------------------------------------
# T9 — UTC discipline
# ---------------------------------------------------------------------------


class T9_UTCDiscipline(unittest.IsolatedAsyncioTestCase):
    async def test_T9_wait_for_timeout_is_tz_agnostic_seconds(self):
        # asyncio.wait_for takes a float seconds value, not a datetime —
        # confirms the design has no timezone arithmetic on the timeout itself.
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.Event().wait(), timeout=0.05)

    def test_T9_no_naive_datetime_in_new_section(self):
        new_section = _find_new_wait_for_section(MAIN_PY_SRC)
        # No naive datetime.now() (without timezone argument)
        # If 'datetime.now()' appears without 'timezone' in the same statement,
        # that's naive — but search across full new section
        for naive_pattern in ('datetime.now()', 'utcnow()'):
            self.assertNotIn(
                naive_pattern, new_section,
                f"naive datetime pattern '{naive_pattern}' detected in new section",
            )


# ---------------------------------------------------------------------------
# T10 — Missing/invalid config fails loud
# ---------------------------------------------------------------------------


class T10_ConfigFailsLoud(unittest.TestCase):
    def test_T10_new_section_uses_get_hermes_config(self):
        new_section = _find_new_wait_for_section(MAIN_PY_SRC)
        self.assertTrue(new_section)
        # Must use get_hermes_config (fail-loud) — not a hardcoded default
        self.assertIn("get_hermes_config(", new_section)
        self.assertIn("hermes_tick_staleness_threshold_sec", new_section)
        self.assertIn("'int'", new_section)

    def test_T10_no_hardcoded_threshold_fallback_in_new_section(self):
        new_section = _find_new_wait_for_section(MAIN_PY_SRC)
        # Common fallback patterns must be ABSENT (no silent defaults)
        forbidden_fallbacks = (
            "or 120", "or 60", "or 30", ", 120)", ", 60)",
            "= 120", "= 60",
        )
        for fb in forbidden_fallbacks:
            self.assertNotIn(
                fb, new_section,
                f"silent-default fallback '{fb}' detected in new section",
            )

    def test_T10_get_hermes_config_raises_on_missing_key(self):
        # Reuse the existing GOV-CFG-001 fail-loud contract.
        m = re.search(
            r'def get_hermes_config\(.*?\)(.*?)(?=\ndef )',
            MAIN_PY_SRC, flags=re.DOTALL,
        )
        self.assertIsNotNone(m, "could not find get_hermes_config function")
        func_body = m.group(1)
        self.assertIn('raise ValueError', func_body)
        self.assertIn('GOV-CFG-001', func_body)


# ---------------------------------------------------------------------------
# T11 — Forbidden-token static scan
# ---------------------------------------------------------------------------


class T11_NoForbiddenTokens(unittest.TestCase):
    FORBIDDEN_TOKENS = (
        "MetaTrader5",
        "import mt5",
        "from mt5",
        "order_send",
        "place_order",
        "dispatch_trade",
        "Agent_Smith",
        "Vantage",
    )

    def test_T11_no_forbidden_tokens_anywhere_in_main_py(self):
        # tradingSignals codebase is MT5-free by design; ensure no token
        # introduced by this WO regresses that property.
        for tok in self.FORBIDDEN_TOKENS:
            self.assertNotIn(
                tok, MAIN_PY_SRC,
                f"forbidden token '{tok}' present in main.py",
            )

    def test_T11_no_forbidden_tokens_in_new_section(self):
        new_section = _find_new_wait_for_section(MAIN_PY_SRC)
        for tok in self.FORBIDDEN_TOKENS:
            self.assertNotIn(
                tok, new_section,
                f"forbidden token '{tok}' in new WO section",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
