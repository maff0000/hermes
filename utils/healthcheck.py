"""
Healthcheck Reporter for HERMES
EPIC-D002: tradingSignals
GOV-ENV-001: Environment-aware configuration

Periodically reports health status to IRIS.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, TYPE_CHECKING

try:
    from utils.iris_client import send_healthcheck, send_alert, send_audit
except ImportError:
    # IRIS client not available — stub functions so HERMES runs independently
    def send_healthcheck(**kwargs): return False
    def send_alert(**kwargs): return False
    def send_audit(**kwargs): return False

if TYPE_CHECKING:
    from main import ServiceState

logger = logging.getLogger("signal-service.healthcheck")

# Default interval (can be overridden via .env)
DEFAULT_HEALTHCHECK_INTERVAL = 60  # seconds


class HealthcheckReporter:
    """
    Collects metrics from HERMES and reports to IRIS.

    Metrics tracked:
    - instruments_active: Number of instruments receiving ticks
    - ticks_last_minute: Tick count in last 60s
    - candles_written_last_minute: Candles saved in last 60s
    - signals_computed_last_minute: Signals computed in last 60s
    - uptime_seconds: Service uptime
    - redis_connected: Redis connection status
    - db_connected: Database connection status
    """

    def __init__(self, state: "ServiceState", interval: int = DEFAULT_HEALTHCHECK_INTERVAL):
        """
        Initialize healthcheck reporter.

        Args:
            state: ServiceState from main.py
            interval: Seconds between healthchecks
        """
        self.state = state
        self.interval = interval
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Rolling counters (reset each healthcheck)
        self.ticks_count = 0
        self.candles_count = 0
        self.signals_count = 0

        # Last healthcheck time
        self._last_check = None

    def record_tick(self):
        """Call this when a tick is processed."""
        self.ticks_count += 1

    def record_candle(self):
        """Call this when a candle is written."""
        self.candles_count += 1

    def record_signal(self):
        """Call this when a signal is computed."""
        self.signals_count += 1

    def _check_redis(self) -> bool:
        """Check Redis connection."""
        try:
            if self.state.redis_publisher and self.state.redis_publisher.is_connected:
                return True
            return False
        except Exception:
            return False

    def _check_db(self) -> bool:
        """Check database connection."""
        import pymysql
        from env_config import get_db_config

        try:
            db_cfg = get_db_config()
            conn = pymysql.connect(
                host=db_cfg['host'],
                port=db_cfg['port'],
                user=db_cfg['user'],
                password=db_cfg['password'],
                database=db_cfg['database'],
                connect_timeout=2
            )
            conn.ping()
            conn.close()
            return True
        except Exception:
            return False

    def _is_market_open(self) -> bool:
        """
        Check if market is open using trading_windows table.
        Gold (XAU_USD) trades 24h Mon-Fri, closed weekends.
        """
        import pymysql
        from env_config import get_db_config

        now = datetime.now(timezone.utc)
        weekday = now.weekday()

        # Weekend: Saturday (5) and Sunday (6) - market closed
        if weekday >= 5:
            return False

        # Friday after 22:00 UTC - market closes for weekend
        if weekday == 4 and now.hour >= 22:
            return False

        # Sunday before 22:00 UTC - market not yet open (handled by weekday >= 5)

        # Check trading_windows table for current session
        try:
            db_cfg = get_db_config()
            conn = pymysql.connect(
                host=db_cfg['host'],
                port=db_cfg['port'],
                user=db_cfg['user'],
                password=db_cfg['password'],
                database=db_cfg['database'],
                connect_timeout=2
            )
            with conn.cursor() as cur:
                # Check if current time falls within any active trading window
                cur.execute("""
                    SELECT session_name FROM trading_windows
                    WHERE is_active = 1
                    AND (
                        (start_time_utc <= end_time_utc AND TIME(%s) BETWEEN start_time_utc AND end_time_utc)
                        OR (start_time_utc > end_time_utc AND (TIME(%s) >= start_time_utc OR TIME(%s) <= end_time_utc))
                    )
                    LIMIT 1
                """, (now.strftime('%H:%M:%S'), now.strftime('%H:%M:%S'), now.strftime('%H:%M:%S')))
                result = cur.fetchone()
            conn.close()

            # If we found an active session, market is open
            return result is not None

        except Exception as e:
            logger.warning(f"Failed to check trading_windows: {e}, using weekday check")
            # Fallback: weekday = open (already checked weekend above)
            return True

    def _determine_status(self, metrics: Dict[str, Any]) -> tuple:
        """
        Determine health status based on metrics.

        Returns:
            Tuple of (status, message)
        """
        market_open = self._is_market_open()

        # If market is closed, skip tick/instrument checks
        if not market_open:
            # Only check infrastructure during market closure
            issues = []
            if not metrics['redis_connected']:
                issues.append("Redis disconnected")
            if not metrics['db_connected']:
                issues.append("Database disconnected")

            if issues:
                return "degraded", f"Market closed; {'; '.join(issues)}"
            return "healthy", "Market closed (weekend)"

        issues = []

        # Check tick freshness (only when market open)
        if metrics['instruments_active'] == 0:
            return "down", "No instruments receiving ticks"

        # Check connections
        if not metrics['redis_connected']:
            issues.append("Redis disconnected")
        if not metrics['db_connected']:
            issues.append("Database disconnected")

        # Check tick flow (if we've been running > 2 minutes)
        if metrics['uptime_seconds'] > 120 and metrics['ticks_last_minute'] == 0:
            issues.append("No ticks in last minute")

        if issues:
            if "disconnected" in str(issues) or "No instruments" in str(issues):
                return "degraded", "; ".join(issues)
            return "degraded", "; ".join(issues)

        # All good
        instrument_count = metrics['instruments_active']
        return "healthy", f"Streaming {instrument_count} instruments"

    def collect_metrics(self) -> Dict[str, Any]:
        """Collect current metrics."""
        now = datetime.now(timezone.utc)

        # Calculate uptime
        uptime = 0
        if self.state.started_at:
            uptime = (now.replace(tzinfo=None) - self.state.started_at).total_seconds()

        # Get current counts and reset
        ticks = self.ticks_count
        candles = self.candles_count
        signals = self.signals_count
        self.ticks_count = 0
        self.candles_count = 0
        self.signals_count = 0

        return {
            "instruments_active": len(self.state.latest_ticks),
            "ticks_last_minute": ticks,
            "candles_written_last_minute": candles,
            "signals_computed_last_minute": signals,
            "uptime_seconds": int(uptime),
            "redis_connected": self._check_redis(),
            "db_connected": self._check_db()
        }

    async def send_healthcheck(self) -> bool:
        """Collect metrics and send healthcheck to IRIS."""
        try:
            metrics = self.collect_metrics()
            status, message = self._determine_status(metrics)

            # IRIS disabled — table tradingReport.iris_messages does not exist
            # TODO: Re-enable when IRIS is rebuilt
            # success = send_healthcheck(
            #     status=status,
            #     message=message,
            #     metrics=metrics
            # )
            success = True  # Stub — IRIS disabled

            if success:
                logger.debug(f"Healthcheck collected: {status} - {message}")
            # else:
            #     logger.warning("Failed to send healthcheck to IRIS")

            self._last_check = datetime.now(timezone.utc)
            return success

        except Exception as e:
            logger.error(f"Healthcheck error: {e}")
            return False

    async def _run_loop(self):
        """Background loop that sends periodic healthchecks."""
        logger.info(f"Healthcheck reporter started (interval={self.interval}s)")

        # Send startup audit
        send_audit(
            action="service_started",
            message=f"HERMES started, streaming {len(self.state.config.instruments)} instruments"
        )

        while self._running:
            try:
                await self.send_healthcheck()
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Healthcheck loop error: {e}")
                await asyncio.sleep(self.interval)

        # Send shutdown audit
        send_audit(
            action="service_stopped",
            message="HERMES shutting down"
        )

        logger.info("Healthcheck reporter stopped")

    def start(self):
        """Start background healthcheck task."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        """Stop background healthcheck task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


# Singleton instance
_reporter: Optional[HealthcheckReporter] = None


def init_healthcheck(state: "ServiceState", interval: int = None) -> HealthcheckReporter:
    """Initialize the healthcheck reporter singleton."""
    global _reporter
    from env_config import get_env_int

    if interval is None:
        interval = get_env_int("HEALTHCHECK_INTERVAL", DEFAULT_HEALTHCHECK_INTERVAL)

    _reporter = HealthcheckReporter(state, interval)
    return _reporter


def get_reporter() -> Optional[HealthcheckReporter]:
    """Get the healthcheck reporter instance."""
    return _reporter


def record_tick():
    """Record a tick (convenience function)."""
    if _reporter:
        _reporter.record_tick()


def record_candle():
    """Record a candle write (convenience function)."""
    if _reporter:
        _reporter.record_candle()


def record_signal():
    """Record a signal computation (convenience function)."""
    if _reporter:
        _reporter.record_signal()
