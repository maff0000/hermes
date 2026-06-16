"""HERMES runtime shadow tick-emit observability.
WO-HELM-HERMES-REDIS-TICK-PUBLISHER-RUNTIME-SHADOW-ENABLE-DEV-0001.

Process-level counters + structured status + a warning rate-limit + a governed recovery probe for
the runtime shadow tick-emit path. No external metrics backend dependency; everything is in-memory
and evidence-friendly. The recovery probe touches ONLY the hermes:shadow:* namespace — it can never
write a canonical hermes:ticks:* key. ALL timestamps UTC.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

from utils import tick_shadow_publisher_v1 as sh

# Required reason tags (governed, stable strings for logs/evidence).
REASON_EMIT_OK = "SHADOW_TICK_EMIT_OK"
REASON_EMIT_FAIL = "SHADOW_TICK_EMIT_FAIL"
REASON_BOOT_FAIL = "SHADOW_TICK_BOOT_FAIL"
REASON_PROBE_OK = "SHADOW_TICK_RECOVERY_PROBE_OK"
REASON_PROBE_FAIL = "SHADOW_TICK_RECOVERY_PROBE_FAIL"
REASON_RATE_LIMITED = "SHADOW_TICK_RATE_LIMITED_WARNING"

# Dedicated recovery-probe key — shadow namespace ONLY (asserted before any write).
RECOVERY_PROBE_KEY = "hermes:shadow:__recovery_probe__:v1"

DEFAULT_WARN_MIN_INTERVAL_SECONDS = 30


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat() if isinstance(dt, datetime) else None


class ShadowEmitMetrics:
    """In-memory counters for the runtime shadow emit path, with a warning rate-limit that suppresses
    log spam on persistent failure while keeping failure visible (suppressed count is surfaced)."""

    def __init__(self, warn_min_interval_seconds=DEFAULT_WARN_MIN_INTERVAL_SECONDS):
        self.attempted = 0
        self.succeeded = 0
        self.failed = 0
        self.last_success_utc = None
        self.last_failure_utc = None
        self.last_failure_reason = None
        self._warn_min = warn_min_interval_seconds
        self._last_warn_utc = None
        self._suppressed_since_last_warn = 0

    def record_attempt(self):
        self.attempted += 1

    def record_success(self, now):
        self.succeeded += 1
        self.last_success_utc = now

    def record_failure(self, now, reason):
        self.failed += 1
        self.last_failure_utc = now
        self.last_failure_reason = reason

    def should_emit_warning(self, now):
        """Rate-limit gate. Returns (should_warn, suppressed_count). Emits at most one warning per
        warn_min_interval; while suppressed, counts how many were withheld so persistent failure is
        still visible when the next warning fires."""
        if self._last_warn_utc is None or (now - self._last_warn_utc).total_seconds() >= self._warn_min:
            withheld = self._suppressed_since_last_warn
            self._last_warn_utc = now
            self._suppressed_since_last_warn = 0
            return True, withheld
        self._suppressed_since_last_warn += 1
        return False, self._suppressed_since_last_warn

    def status(self):
        return {"attempted": self.attempted, "succeeded": self.succeeded, "failed": self.failed,
                "last_success_utc": _iso(self.last_success_utc),
                "last_failure_utc": _iso(self.last_failure_utc),
                "last_failure_reason": self.last_failure_reason,
                "warn_min_interval_seconds": self._warn_min,
                "suppressed_warnings_pending": self._suppressed_since_last_warn}


def run_recovery_probe(*, redis_client, ex_seconds=10, now=None):
    """Governed recovery probe for the configured shadow route. Writes/reads/deletes a JSON value on
    a dedicated hermes:shadow:* probe key ONLY (never a canonical live key). Returns
    (ok, reason_tag, detail). The value is always a JSON string — never a dict."""
    sh.assert_shadow_key(RECOVERY_PROBE_KEY)   # fail-loud guard: shadow-prefixed, not a live key
    stamp = _iso(now) if now is not None else "check"
    payload = json.dumps({"probe": "SHADOW_TICK_RECOVERY", "stamp": stamp})
    try:
        redis_client.set(RECOVERY_PROBE_KEY, payload, ex=ex_seconds)
        got = redis_client.get(RECOVERY_PROBE_KEY)
        ok = got is not None
        try:
            redis_client.delete(RECOVERY_PROBE_KEY)
        except Exception:  # noqa: BLE001 - best-effort cleanup; TTL expiry backstops
            pass
        if ok:
            return True, REASON_PROBE_OK, f"probe key {RECOVERY_PROBE_KEY} write/read/delete OK"
        return False, REASON_PROBE_FAIL, "probe key not readable after write"
    except Exception as exc:  # noqa: BLE001 - probe surfaces any route fault as PROBE_FAIL
        return False, REASON_PROBE_FAIL, repr(exc)
