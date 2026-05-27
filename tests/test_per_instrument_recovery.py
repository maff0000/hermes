#!/usr/bin/env python3
"""Tests for the per-instrument recovery trigger.

Originally added by WO-HERMES-SIGNAL-SERVICE-DEV-PER-INSTRUMENT-RESUBSCRIBE-REPAIR-0001.
Updated by WO-HERMES-PER-INSTRUMENT-RECOVERY-CONFIG-PROMOTION-0001 — thresholds
have moved from module constants to governed hermes_config rows loaded into
self._config at lifespan init. Tests now inject a mock config dict.

Pure unit tests against utils/watchdog.py — no DB, no fabric, no service
side effects. Covers:

  - decide-to-alert + cooldown + max-attempts cap
  - cross-instrument isolation
  - PARTIAL_FLOWING transitions (only from/to FLOWING)
  - fail-loud KeyError when config keys missing
  - no scoring/decision attributes added
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

sys.path.insert(0, "/srv-dev/tradingSignals")

from utils.watchdog import (  # noqa: E402
    HermesWatchdog,
    HealthState,
    StreamState,
)

# Live values mirrored from hermes_config rows added by migration 012.
SUSTAINED_RED_THRESHOLD_SEC = 300
RECOVERY_COOLDOWN_SEC = 600
MAX_RECOVERY_ATTEMPTS_PER_HOUR = 3


def _make_watchdog(*, sustained_red_threshold_sec=SUSTAINED_RED_THRESHOLD_SEC,
                   recovery_cooldown_sec=RECOVERY_COOLDOWN_SEC,
                   max_recovery_attempts_per_hour=MAX_RECOVERY_ATTEMPTS_PER_HOUR,
                   include_recovery_keys=True):
    """Build a HermesWatchdog with mock config (matches main.py wiring)."""
    service_state = MagicMock()
    persistence = MagicMock()
    config = {}
    if include_recovery_keys:
        config['per_instrument_sustained_red_threshold_sec'] = sustained_red_threshold_sec
        config['per_instrument_recovery_cooldown_sec'] = recovery_cooldown_sec
        config['per_instrument_max_recovery_attempts_per_hour'] = max_recovery_attempts_per_hour
    market_hours_checker = MagicMock(return_value=True)
    logger = MagicMock()
    w = HermesWatchdog(
        service_state=service_state,
        persistence=persistence,
        config=config,
        market_hours_checker=market_hours_checker,
        logger=logger,
    )
    w._current_stream_state = StreamState.FLOWING
    return w


class TestPerInstrumentRecoveryTrigger(unittest.TestCase):
    def test_sustained_red_below_threshold_does_not_request(self):
        w = _make_watchdog()
        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        w._instrument_health = {"XAU_USD": (HealthState.RED, "HERMES_INSTRUMENT_M1_STALE")}
        w._per_instrument_red_since["XAU_USD"] = now - timedelta(seconds=SUSTAINED_RED_THRESHOLD_SEC - 1)
        w._evaluate_per_instrument_recovery(now, red_count=1)
        self.assertFalse(w._recovery_request_pending)

    def test_sustained_red_past_threshold_requests_recovery(self):
        w = _make_watchdog()
        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        w._instrument_health = {"XAU_USD": (HealthState.RED, "HERMES_INSTRUMENT_M1_STALE")}
        w._per_instrument_red_since["XAU_USD"] = now - timedelta(seconds=SUSTAINED_RED_THRESHOLD_SEC + 1)
        w._evaluate_per_instrument_recovery(now, red_count=1)
        self.assertTrue(w._recovery_request_pending)
        self.assertIn("XAU_USD", w._recovery_request_reason)
        self.assertIn("sustained_red", w._recovery_request_reason)

    def test_consume_recovery_request_returns_reason_and_clears(self):
        w = _make_watchdog()
        w._recovery_request_pending = True
        w._recovery_request_reason = "test_reason"
        out = w.consume_recovery_request()
        self.assertEqual(out, "test_reason")
        self.assertFalse(w._recovery_request_pending)
        self.assertIsNone(w._recovery_request_reason)
        self.assertIsNone(w.consume_recovery_request())

    def test_consume_when_no_request_returns_none(self):
        w = _make_watchdog()
        self.assertIsNone(w.consume_recovery_request())

    def test_cooldown_prevents_immediate_re_request(self):
        w = _make_watchdog()
        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        w._instrument_health = {"XAU_USD": (HealthState.RED, "HERMES_INSTRUMENT_M1_STALE")}
        w._per_instrument_red_since["XAU_USD"] = now - timedelta(seconds=SUSTAINED_RED_THRESHOLD_SEC + 100)
        w._evaluate_per_instrument_recovery(now, red_count=1)
        self.assertTrue(w._recovery_request_pending)
        w.consume_recovery_request()
        later = now + timedelta(seconds=RECOVERY_COOLDOWN_SEC // 2)
        w._evaluate_per_instrument_recovery(later, red_count=1)
        self.assertFalse(w._recovery_request_pending)

    def test_cooldown_expires_allows_re_request(self):
        w = _make_watchdog()
        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        w._instrument_health = {"XAU_USD": (HealthState.RED, "HERMES_INSTRUMENT_M1_STALE")}
        w._per_instrument_red_since["XAU_USD"] = now - timedelta(seconds=SUSTAINED_RED_THRESHOLD_SEC + 100)
        w._evaluate_per_instrument_recovery(now, red_count=1)
        w.consume_recovery_request()
        later = now + timedelta(seconds=RECOVERY_COOLDOWN_SEC + 60)
        w._evaluate_per_instrument_recovery(later, red_count=1)
        self.assertTrue(w._recovery_request_pending)

    def test_max_attempts_window_caps_requests(self):
        w = _make_watchdog()
        start = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        w._instrument_health = {"XAU_USD": (HealthState.RED, "HERMES_INSTRUMENT_M1_STALE")}
        for i in range(MAX_RECOVERY_ATTEMPTS_PER_HOUR):
            w._recovery_attempts_window.append(start - timedelta(seconds=i * 60))
        w._last_recovery_request_at = start - timedelta(seconds=RECOVERY_COOLDOWN_SEC + 60)
        w._per_instrument_red_since["XAU_USD"] = start - timedelta(seconds=SUSTAINED_RED_THRESHOLD_SEC + 100)
        w._evaluate_per_instrument_recovery(start, red_count=1)
        self.assertFalse(w._recovery_request_pending)

    def test_cross_instrument_isolation(self):
        w = _make_watchdog()
        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        w._instrument_health = {
            "XAU_USD": (HealthState.RED, "HERMES_INSTRUMENT_M1_STALE"),
            "EUR_USD": (HealthState.GREEN, None),
        }
        w._per_instrument_red_since["XAU_USD"] = now - timedelta(seconds=SUSTAINED_RED_THRESHOLD_SEC + 100)
        w._evaluate_per_instrument_recovery(now, red_count=1)
        self.assertTrue(w._recovery_request_pending)
        self.assertIn("XAU_USD", w._recovery_request_reason)
        self.assertNotIn("EUR_USD", w._recovery_request_reason)
        self.assertNotIn("EUR_USD", w._per_instrument_red_since)

    def test_red_clears_when_instrument_recovers(self):
        w = _make_watchdog()
        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        w._instrument_health = {"XAU_USD": (HealthState.RED, "HERMES_INSTRUMENT_M1_STALE")}
        w._evaluate_per_instrument_recovery(now, red_count=1)
        self.assertIn("XAU_USD", w._per_instrument_red_since)
        w._instrument_health = {"XAU_USD": (HealthState.GREEN, None)}
        w._evaluate_per_instrument_recovery(now + timedelta(seconds=10), red_count=0)
        self.assertNotIn("XAU_USD", w._per_instrument_red_since)


class TestConfigPromotionFailLoud(unittest.TestCase):
    """WO-HERMES-PER-INSTRUMENT-RECOVERY-CONFIG-PROMOTION-0001:
    Missing config keys must fail loud (KeyError) rather than silently
    defaulting. Validates the no-silent-defaults architect doctrine."""

    def test_missing_sustained_red_threshold_raises_KeyError(self):
        w = _make_watchdog(include_recovery_keys=False)
        # Manually add the other two so we hit the missing one cleanly.
        w._config['per_instrument_recovery_cooldown_sec'] = RECOVERY_COOLDOWN_SEC
        w._config['per_instrument_max_recovery_attempts_per_hour'] = MAX_RECOVERY_ATTEMPTS_PER_HOUR
        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        w._instrument_health = {"XAU_USD": (HealthState.RED, "X")}
        with self.assertRaises(KeyError) as ctx:
            w._evaluate_per_instrument_recovery(now, red_count=1)
        self.assertIn("per_instrument_sustained_red_threshold_sec", str(ctx.exception))

    def test_missing_recovery_cooldown_raises_KeyError(self):
        w = _make_watchdog(include_recovery_keys=False)
        w._config['per_instrument_sustained_red_threshold_sec'] = SUSTAINED_RED_THRESHOLD_SEC
        w._config['per_instrument_max_recovery_attempts_per_hour'] = MAX_RECOVERY_ATTEMPTS_PER_HOUR
        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        w._instrument_health = {"XAU_USD": (HealthState.RED, "X")}
        with self.assertRaises(KeyError) as ctx:
            w._evaluate_per_instrument_recovery(now, red_count=1)
        self.assertIn("per_instrument_recovery_cooldown_sec", str(ctx.exception))

    def test_missing_max_attempts_raises_KeyError(self):
        w = _make_watchdog(include_recovery_keys=False)
        w._config['per_instrument_sustained_red_threshold_sec'] = SUSTAINED_RED_THRESHOLD_SEC
        w._config['per_instrument_recovery_cooldown_sec'] = RECOVERY_COOLDOWN_SEC
        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        w._instrument_health = {"XAU_USD": (HealthState.RED, "X")}
        with self.assertRaises(KeyError) as ctx:
            w._evaluate_per_instrument_recovery(now, red_count=1)
        self.assertIn("per_instrument_max_recovery_attempts_per_hour", str(ctx.exception))

    def test_all_three_keys_present_no_KeyError(self):
        """Sanity: when all keys present + healthy state, no exception."""
        w = _make_watchdog()  # include_recovery_keys=True default
        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        w._instrument_health = {"XAU_USD": (HealthState.GREEN, None)}
        # Should complete without raising
        w._evaluate_per_instrument_recovery(now, red_count=0)

    def test_config_values_used_verbatim_via_thresholds(self):
        """Inject non-default values and verify behaviour reflects them."""
        # Tight threshold (10s) — sustained for 11s should trigger
        w = _make_watchdog(sustained_red_threshold_sec=10)
        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        w._instrument_health = {"XAU_USD": (HealthState.RED, "X")}
        w._per_instrument_red_since["XAU_USD"] = now - timedelta(seconds=11)
        w._evaluate_per_instrument_recovery(now, red_count=1)
        self.assertTrue(w._recovery_request_pending)


class TestPartialFlowingState(unittest.TestCase):
    def test_red_count_positive_flips_FLOWING_to_PARTIAL_FLOWING(self):
        w = _make_watchdog()
        self.assertEqual(w._current_stream_state, StreamState.FLOWING)
        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        w._instrument_health = {"XAU_USD": (HealthState.RED, "HERMES_INSTRUMENT_M1_STALE")}
        w._evaluate_per_instrument_recovery(now, red_count=1)
        self.assertEqual(w._current_stream_state, StreamState.PARTIAL_FLOWING)

    def test_red_count_zero_flips_PARTIAL_FLOWING_back_to_FLOWING(self):
        w = _make_watchdog()
        w._current_stream_state = StreamState.PARTIAL_FLOWING
        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        w._instrument_health = {"XAU_USD": (HealthState.GREEN, None)}
        w._evaluate_per_instrument_recovery(now, red_count=0)
        self.assertEqual(w._current_stream_state, StreamState.FLOWING)

    def test_STALE_state_NOT_overridden_by_PARTIAL_FLOWING_logic(self):
        w = _make_watchdog()
        w._current_stream_state = StreamState.STALE
        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        w._instrument_health = {"XAU_USD": (HealthState.RED, "X")}
        w._evaluate_per_instrument_recovery(now, red_count=1)
        self.assertEqual(w._current_stream_state, StreamState.STALE)

    def test_RECOVERING_state_NOT_overridden(self):
        w = _make_watchdog()
        w._current_stream_state = StreamState.RECOVERING
        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        w._instrument_health = {"XAU_USD": (HealthState.RED, "X")}
        w._evaluate_per_instrument_recovery(now, red_count=1)
        self.assertEqual(w._current_stream_state, StreamState.RECOVERING)


class TestRecoveryRequestIdempotency(unittest.TestCase):
    def test_pending_request_does_not_duplicate(self):
        w = _make_watchdog()
        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        w._instrument_health = {"XAU_USD": (HealthState.RED, "X")}
        w._per_instrument_red_since["XAU_USD"] = now - timedelta(seconds=SUSTAINED_RED_THRESHOLD_SEC + 100)
        w._evaluate_per_instrument_recovery(now, red_count=1)
        first_reason = w._recovery_request_reason
        first_at = w._last_recovery_request_at
        w._evaluate_per_instrument_recovery(now + timedelta(seconds=30), red_count=1)
        self.assertEqual(w._recovery_request_reason, first_reason)
        self.assertEqual(w._last_recovery_request_at, first_at)


class TestNoDecisionPathChange(unittest.TestCase):
    def test_no_scoring_methods_added_to_watchdog(self):
        w = _make_watchdog()
        forbidden = ["score", "threshold_neo", "multiplier", "scorer", "decision",
                     "execute_trade", "fire", "dispatch"]
        for attr in dir(w):
            for token in forbidden:
                self.assertNotIn(token.lower(), attr.lower(),
                                 f"unexpected attr {attr} contains forbidden token {token}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
