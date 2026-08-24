"""
HERMES startup dependency gate — WO-HELM-HERMES-DEV-STARTUP-RECOVERY-RESILIENCE-AND-HEALTH-TRUTHFIX-0001.

After the 2026-08-20 Dell host reboot, hermes-signal booted before MariaDB accepted
connections: the first unprotected DB read inside the FastAPI lifespan raised pymysql
2013 and aborted startup. The container was restarted into a second boot where a
transient DNS failure made the one-shot OANDA connect fail and the service settled
into a permanent streamless state. This module owns the FIRST half of that fix: a
bounded, observable wait-for-DB gate that runs before any lifespan DB access.

Design:
  - `next_delay` / `backoff_schedule` are PURE — the bounded-backoff arithmetic is
    directly testable with no I/O.
  - `wait_for_db_ready` performs the bounded wait. Every collaborator (connect,
    sleep, clock) is injectable so tests exercise the real retry/timeout logic
    against fakes — no mocked-success-only paths.
  - On success: returns the number of attempts used (>=1).
  - On exhaustion: raises DbStartupTimeout. The caller lets this propagate so the
    process exits VISIBLY (container restart policy owns the next attempt). We never
    pretend to be healthy without a database.

Startup phases (surfaced via /health `startup_phase` and structured logs):
  STARTING -> WAITING_FOR_DB -> CONNECTING_OANDA -> STARTED

Config (GOV-ENV-001 external env contract — same keys DEV and PROD, values external):
  DB_STARTUP_WAIT_TIMEOUT_SECONDS   total budget for the gate      (default 180)
  DB_STARTUP_RETRY_INITIAL_DELAY    first retry delay, seconds     (default 1)
  DB_STARTUP_RETRY_MAX_DELAY        backoff ceiling, seconds       (default 15)
  DB_STARTUP_RETRY_BACKOFF_MULTIPLIER  exponential multiplier      (default 2)

No secrets are ever logged: only host/port/attempt/delay metadata, never
credentials and never the DSN.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone


# Startup phase vocabulary — a STARTUP-ONLY marker. Post-startup runtime truth
# remains the watchdog stream/health state (single truth surface, never duplicated).
class StartupPhase:
    STARTING = "STARTING"
    WAITING_FOR_DB = "WAITING_FOR_DB"
    CONNECTING_OANDA = "CONNECTING_OANDA"
    STARTED = "STARTED"


class DbStartupTimeout(RuntimeError):
    """DB never became ready inside the governed startup budget. Fail VISIBLY."""


@dataclass(frozen=True)
class StartupGateConfig:
    timeout_seconds: float = 180.0
    initial_delay: float = 1.0
    max_delay: float = 15.0
    multiplier: float = 2.0

    @classmethod
    def from_env(cls) -> "StartupGateConfig":
        # GOV-ENV-001: externally supplied values; identical keys in DEV and PROD.
        from env_config import get_env_int
        return cls(
            timeout_seconds=float(get_env_int("DB_STARTUP_WAIT_TIMEOUT_SECONDS", 180)),
            initial_delay=float(get_env_int("DB_STARTUP_RETRY_INITIAL_DELAY", 1)),
            max_delay=float(get_env_int("DB_STARTUP_RETRY_MAX_DELAY", 15)),
            multiplier=float(get_env_int("DB_STARTUP_RETRY_BACKOFF_MULTIPLIER", 2)),
        )


def next_delay(current: float, cfg: StartupGateConfig) -> float:
    """Pure bounded exponential backoff step: grows by cfg.multiplier, capped at cfg.max_delay."""
    if current <= 0:
        return min(max(cfg.initial_delay, 0.1), cfg.max_delay)
    return min(current * cfg.multiplier, cfg.max_delay)


def backoff_schedule(cfg: StartupGateConfig, n: int) -> list:
    """Pure: the first n delays the gate would sleep. Monotone non-decreasing, ceiling-capped."""
    out, d = [], 0.0
    for _ in range(n):
        d = next_delay(d, cfg)
        out.append(d)
    return out


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _default_probe(db_config: dict, connect_timeout: float = 5.0) -> None:
    """One bounded liveness probe: connect + SELECT 1 + close. Raises on any failure."""
    import pymysql
    conn = pymysql.connect(
        host=db_config["host"], port=db_config["port"], user=db_config["user"],
        password=db_config["password"], database=db_config["database"],
        connect_timeout=connect_timeout,
    )
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
    finally:
        conn.close()


async def wait_for_db_ready(db_config: dict, cfg: StartupGateConfig, logger,
                            probe_fn=None, sleep_fn=None, clock=None) -> int:
    """Bounded wait for the HERMES database to accept queries.

    Returns the attempt count on success (>=1). Raises DbStartupTimeout when the
    governed budget is exhausted — the caller must let that propagate (visible
    failure; container restart policy owns the next boot). Never a tight loop:
    sleeps follow bounded exponential backoff.
    """
    import asyncio
    probe = probe_fn or _default_probe
    sleep = sleep_fn or asyncio.sleep
    now = clock or time.monotonic

    started = now()
    attempt = 0
    delay = 0.0
    last_error = None
    while True:
        attempt += 1
        try:
            probe(db_config)
            logger.info(
                "[STARTUP_GATE] DB_READY attempt=%d elapsed_s=%.1f utc=%s",
                attempt, now() - started, _utc_now_iso(),
            )
            return attempt
        except Exception as exc:  # noqa: BLE001 — any connect/query failure means "not ready yet"
            last_error = exc
            elapsed = now() - started
            if elapsed >= cfg.timeout_seconds:
                logger.critical(
                    "[STARTUP_GATE] DB_STARTUP_TIMEOUT attempts=%d elapsed_s=%.1f budget_s=%.1f "
                    "host=%s port=%s last_error=%r utc=%s — failing VISIBLY (no false health)",
                    attempt, elapsed, cfg.timeout_seconds,
                    db_config.get("host"), db_config.get("port"), exc, _utc_now_iso(),
                )
                raise DbStartupTimeout(
                    f"database not ready after {attempt} attempts / {elapsed:.1f}s "
                    f"(budget {cfg.timeout_seconds:.0f}s): {exc!r}"
                ) from exc
            delay = next_delay(delay, cfg)
            # Never overshoot the budget by a full ceiling sleep.
            delay = min(delay, max(cfg.timeout_seconds - elapsed, 0.1))
            logger.warning(
                "[STARTUP_GATE] WAITING_FOR_DB attempt=%d elapsed_s=%.1f retry_in_s=%.1f "
                "host=%s port=%s error=%r utc=%s",
                attempt, elapsed, delay, db_config.get("host"), db_config.get("port"),
                exc, _utc_now_iso(),
            )
            await sleep(delay)
