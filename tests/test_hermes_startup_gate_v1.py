"""WO-HELM-HERMES-DEV-STARTUP-RECOVERY-RESILIENCE-AND-HEALTH-TRUTHFIX-0001 — startup DB gate tests.

Behavioural tests for utils/hermes_startup_gate_v1.wait_for_db_ready: the bounded,
observable wait-for-DB gate that replaces the unprotected lifespan DB access which
aborted boot #1 after the 2026-08-20 Dell reboot (pymysql 2013 during the DB race).

NON-VACUITY: every recovery test drives the REAL retry/timeout/backoff logic with a
probe that genuinely FAILS first (raises), then succeeds — never mocked success-only.
Pure unit tests: no DB, no network, no sleeping (injected fake sleep/clock).
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
BASE_DIR = HERE.parent
sys.path = [p for p in sys.path if 'config' not in p or 'tradingSignals' in p]
sys.path.insert(0, str(BASE_DIR))

from utils.hermes_startup_gate_v1 import (  # noqa: E402
    DbStartupTimeout, StartupGateConfig, StartupPhase, backoff_schedule,
    next_delay, wait_for_db_ready,
)

DB_CFG = {"host": "db-host", "port": 3306, "user": "u",
          "password": "sekret-fixture-value-9Q4x", "database": "tradingSignals"}


class FakeClock:
    """Deterministic monotonic clock advanced by the fake sleep."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeSleep:
    """Records every requested delay and advances the fake clock — no real sleeping."""

    def __init__(self, clock):
        self.clock = clock
        self.delays = []

    async def __call__(self, seconds):
        self.delays.append(seconds)
        self.clock.t += seconds


class FlakyProbe:
    """Fails (raises) for the first `fail_times` attempts, then succeeds."""

    def __init__(self, fail_times, exc=None):
        self.fail_times = fail_times
        self.calls = 0
        self.exc = exc or OSError("connection refused (DB not ready)")

    def __call__(self, db_config):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc


def _run(coro):
    return asyncio.run(coro)


class TestBackoffPure(unittest.TestCase):
    def test_schedule_is_bounded_exponential_capped(self):
        cfg = StartupGateConfig(timeout_seconds=180, initial_delay=1, max_delay=15, multiplier=2)
        self.assertEqual(backoff_schedule(cfg, 7), [1, 2, 4, 8, 15, 15, 15])

    def test_next_delay_never_exceeds_ceiling(self):
        cfg = StartupGateConfig(max_delay=15, multiplier=2)
        d = 0.0
        for _ in range(50):
            d = next_delay(d, cfg)
            self.assertLessEqual(d, cfg.max_delay)
            self.assertGreater(d, 0)

    def test_schedule_monotone_non_decreasing(self):
        cfg = StartupGateConfig(initial_delay=1, max_delay=30, multiplier=3)
        sched = backoff_schedule(cfg, 10)
        self.assertEqual(sched, sorted(sched))


class TestWaitForDbReady(unittest.TestCase):
    def _gate(self, probe, cfg=None):
        clock = FakeClock()
        sleep = FakeSleep(clock)
        logger = MagicMock()
        cfg = cfg or StartupGateConfig(timeout_seconds=180, initial_delay=1, max_delay=15, multiplier=2)
        return clock, sleep, logger, cfg, wait_for_db_ready(
            DB_CFG, cfg, logger, probe_fn=probe, sleep_fn=sleep, clock=clock)

    def test_db_ready_immediately_single_attempt_no_sleep(self):
        probe = FlakyProbe(fail_times=0)
        _, sleep, logger, _, coro = self._gate(probe)
        attempts = _run(coro)
        self.assertEqual(attempts, 1)
        self.assertEqual(sleep.delays, [])          # no tight loop, no needless wait
        logger.info.assert_called()                 # DB_READY logged

    def test_db_becomes_available_later_recovers_without_restart(self):
        """The defect scenario: DB not ready at app start, ready shortly after.
        The gate must retry with bounded backoff and return success — no crash,
        no manual restart, no tight loop."""
        probe = FlakyProbe(fail_times=4)
        _, sleep, logger, _, coro = self._gate(probe)
        attempts = _run(coro)
        self.assertEqual(attempts, 5)               # 4 real failures + 1 success
        self.assertEqual(sleep.delays, [1, 2, 4, 8])  # bounded exponential, no tight loop
        self.assertGreaterEqual(logger.warning.call_count, 4)  # every wait is observable

    def test_backoff_caps_at_max_delay(self):
        probe = FlakyProbe(fail_times=8)
        _, sleep, _, _, coro = self._gate(probe)
        _run(coro)
        self.assertEqual(sleep.delays, [1, 2, 4, 8, 15, 15, 15, 15])

    def test_budget_exhaustion_fails_visibly(self):
        """DB never comes up: the gate must raise DbStartupTimeout (visible
        failure), never return pretending to be healthy."""
        probe = FlakyProbe(fail_times=10 ** 9)
        cfg = StartupGateConfig(timeout_seconds=30, initial_delay=1, max_delay=15, multiplier=2)
        clock, sleep, logger, _, coro = self._gate(probe, cfg)
        with self.assertRaises(DbStartupTimeout):
            _run(coro)
        self.assertGreaterEqual(clock.t, 0)
        self.assertLessEqual(sum(sleep.delays), cfg.timeout_seconds + cfg.max_delay)
        logger.critical.assert_called()             # exhaustion is loudly logged

    def test_timeout_error_carries_cause_and_attempts(self):
        probe = FlakyProbe(fail_times=10 ** 9, exc=OSError("still booting"))
        cfg = StartupGateConfig(timeout_seconds=5, initial_delay=1, max_delay=2, multiplier=2)
        _, _, _, _, coro = self._gate(probe, cfg)
        try:
            _run(coro)
            self.fail("expected DbStartupTimeout")
        except DbStartupTimeout as exc:
            self.assertIsInstance(exc.__cause__, OSError)
            self.assertIn("not ready", str(exc))

    def test_never_sleeps_past_remaining_budget(self):
        probe = FlakyProbe(fail_times=10 ** 9)
        cfg = StartupGateConfig(timeout_seconds=10, initial_delay=4, max_delay=60, multiplier=4)
        clock, sleep, _, _, coro = self._gate(probe, cfg)
        with self.assertRaises(DbStartupTimeout):
            _run(coro)
        # Each individual sleep is clipped to the remaining budget (+small floor).
        self.assertTrue(all(d <= cfg.timeout_seconds for d in sleep.delays), sleep.delays)

    def test_no_secret_material_in_logs(self):
        """The gate logs host/port/attempt metadata — never the DB password."""
        probe = FlakyProbe(fail_times=2)
        _, _, logger, _, coro = self._gate(probe)
        _run(coro)
        for call in list(logger.warning.call_args_list) + list(logger.info.call_args_list):
            joined = " ".join(str(a) for a in call.args)
            self.assertNotIn(DB_CFG["password"], joined)


class TestStartupGateConfigFromEnv(unittest.TestCase):
    def test_from_env_reads_governed_keys_with_defaults(self):
        """GOV-ENV-001: values come from the external env contract; defaults are
        the documented ones. Same keys DEV and PROD — no environment branch."""
        import os
        saved = {k: os.environ.get(k) for k in (
            "DB_STARTUP_WAIT_TIMEOUT_SECONDS", "DB_STARTUP_RETRY_INITIAL_DELAY",
            "DB_STARTUP_RETRY_MAX_DELAY", "DB_STARTUP_RETRY_BACKOFF_MULTIPLIER")}
        try:
            for k in saved:
                os.environ.pop(k, None)
            cfg = StartupGateConfig.from_env()
            self.assertEqual((cfg.timeout_seconds, cfg.initial_delay, cfg.max_delay, cfg.multiplier),
                             (180.0, 1.0, 15.0, 2.0))
            os.environ["DB_STARTUP_WAIT_TIMEOUT_SECONDS"] = "240"
            os.environ["DB_STARTUP_RETRY_MAX_DELAY"] = "30"
            cfg2 = StartupGateConfig.from_env()
            self.assertEqual((cfg2.timeout_seconds, cfg2.max_delay), (240.0, 30.0))
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_startup_phase_vocabulary(self):
        self.assertEqual(
            (StartupPhase.STARTING, StartupPhase.WAITING_FOR_DB,
             StartupPhase.CONNECTING_OANDA, StartupPhase.STARTED),
            ("STARTING", "WAITING_FOR_DB", "CONNECTING_OANDA", "STARTED"))


if __name__ == "__main__":
    unittest.main()
