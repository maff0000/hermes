"""
HERMES Watchdog — Runtime Staleness Detection and Health Truth
WO-HERMES-STREAM-WATCHDOG-0001
WO-HERMES-STREAM-WATCHDOG-0001A (remediation: in-memory hot state, cadence-driven persistence)

Eliminates zombie-stream states by continuously evaluating data flow
and persisting authoritative health truth.

Architecture (0001A):
- record_tick() updates IN-MEMORY state only (no DB per tick)
- watchdog loop evaluates in-memory state for stale detection
- persistence loop writes health snapshots on cadence + state transitions
- DB connection reused (not opened per call)
- logger injected from service (not standalone)

Fault codes:
    HERMES_STREAM_STALE_TICK       — no fresh tick beyond threshold during market hours
    HERMES_STREAM_STALE_CANDLE     — no M1 candle progression beyond threshold during market hours
    HERMES_RECOVERY_FALSE_CONNECT  — reconnect claimed success but no data flow observed
    HERMES_RECOVERY_EXHAUSTED      — max recovery attempts reached, fatal exit required
    HERMES_HEALTH_STUB_FORBIDDEN   — transitional guard: old static /health stub must not exist
"""
import asyncio
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

import pymysql
import pymysql.cursors


# ============================================================
# Fault codes — stable contract
# ============================================================

class FaultCode:
    STALE_TICK = "HERMES_STREAM_STALE_TICK"
    STALE_CANDLE = "HERMES_STREAM_STALE_CANDLE"
    FALSE_CONNECT = "HERMES_RECOVERY_FALSE_CONNECT"
    RECOVERY_EXHAUSTED = "HERMES_RECOVERY_EXHAUSTED"
    HEALTH_STUB_FORBIDDEN = "HERMES_HEALTH_STUB_FORBIDDEN"
    INCIDENT_WRITE_FAILED = "HERMES_INCIDENT_WRITE_FAILED"
    INSTRUMENT_TICK_STALE = "HERMES_INSTRUMENT_TICK_STALE"
    INSTRUMENT_M1_STALE = "HERMES_INSTRUMENT_M1_STALE"
    INSTRUMENT_HEALTHY = "HERMES_INSTRUMENT_HEALTHY"
    MARKET_CLOSED = "MARKET_CLOSED" 


# ============================================================
# Stream / health / recovery state enums (match DB ENUMs)
# ============================================================

class StreamState:
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED_UNPROVEN = "CONNECTED_UNPROVEN"
    FLOWING = "FLOWING"
    # WO-HERMES-SIGNAL-SERVICE-DEV-PER-INSTRUMENT-RESUBSCRIBE-REPAIR-0001:
    # Global stream is delivering ticks for at least some instruments, but one
    # or more configured instruments are currently RED. Consumers may treat
    # this as AMBER for backwards compatibility.
    PARTIAL_FLOWING = "PARTIAL_FLOWING"
    STALE = "STALE"
    RECOVERING = "RECOVERING"
    FAILED = "FAILED"
    # WO-HELM-HERMES-DEV-STARTUP-RECOVERY-RESILIENCE-AND-HEALTH-TRUTHFIX-0001:
    # First-class "no successful stream connection has EVER been established this
    # process lifetime" state. Set by the lifespan when the initial OANDA connect
    # fails; the governed recovery loop then drives NEVER_CONNECTED -> RECOVERING
    # -> CONNECTED_UNPROVEN -> FLOWING. Distinct from DISCONNECTED (which also
    # covers post-connection loss) so consumers can see a boot that never flowed.
    # DB enum widened by migrations/026_stream_state_never_connected.sql.
    NEVER_CONNECTED = "NEVER_CONNECTED"


# WO-HERMES-PER-INSTRUMENT-RECOVERY-CONFIG-PROMOTION-0001 (supersedes RESUBSCRIBE-REPAIR-0001):
# Per-instrument recovery thresholds have moved to governed hermes_config rows.
# Loaded into self._config by main.py via get_hermes_config() at lifespan init.
# Fail-loud: if any of these keys are missing from self._config the watchdog
# raises KeyError on first per-instrument evaluation cycle. No silent defaults.
# Required config keys:
#   per_instrument_sustained_red_threshold_sec  (int; live value 300)
#   per_instrument_recovery_cooldown_sec        (int; live value 600)
#   per_instrument_max_recovery_attempts_per_hour (int; live value 3)
# Migration: /srv-dev/tradingSignals/migrations/012_per_instrument_recovery_config.sql


class HealthState:
    GREEN = "GREEN"
    AMBER = "AMBER"
    RED = "RED"


class RecoveryState:
    IDLE = "IDLE"
    IN_PROGRESS = "IN_PROGRESS"
    PROOF_WINDOW = "PROOF_WINDOW"
    FAILED = "FAILED"
    EXHAUSTED = "EXHAUSTED"


class DataFlowState:
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    DEAD = "DEAD"
    UNKNOWN = "UNKNOWN"


# ============================================================
# Health persistence — connection-reusing DB operations
# ============================================================

class HealthPersistence:
    """Reads/writes hermes_service_health and hermes_incidents.
    Reuses a single DB connection (reconnects on failure)."""

    def __init__(self, db_config: dict, service_name: str = "hermes",
                 environment: str = "DEV", logger=None):
        self._db_config = db_config
        self._service_name = service_name
        self._environment = environment
        self._conn = None
        self._logger = logger

    def _log(self, level, msg):
        if self._logger:
            getattr(self._logger, level)(msg)

    def _get_conn(self):
        """Get or reuse DB connection. Reconnect if stale."""
        try:
            if self._conn and self._conn.open:
                self._conn.ping(reconnect=True)
                return self._conn
        except Exception:
            self._conn = None

        try:
            self._conn = pymysql.connect(
                host=self._db_config['host'],
                port=self._db_config['port'],
                user=self._db_config['user'],
                password=self._db_config['password'],
                database=self._db_config['database'],
                autocommit=True,
                connect_timeout=5,
            )
            return self._conn
        except Exception as e:
            self._log('error', f"DB connection failed: {e}")
            self._conn = None
            raise

    def update_health(self, **kwargs):
        """Update the single health row. Only non-None kwargs are written."""
        sets = []
        params = []
        for col, val in kwargs.items():
            if val is not None:
                sets.append(f"{col} = %s")
                params.append(val)
        if not sets:
            return

        params.extend([self._service_name, self._environment])
        sql = f"UPDATE hermes_service_health SET {', '.join(sets)} WHERE service_name = %s AND environment = %s"

        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(sql, params)
        except Exception as e:
            self._log('error', f"Failed to update health state: {e}")
            self._conn = None  # Force reconnect next time

    def read_health(self) -> Optional[Dict[str, Any]]:
        try:
            conn = self._get_conn()
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(
                    "SELECT * FROM hermes_service_health WHERE service_name = %s AND environment = %s",
                    (self._service_name, self._environment)
                )
                return cur.fetchone()
        except Exception as e:
            self._log('error', f"Failed to read health state: {e}")
            self._conn = None
            return None

    def open_incident(self, severity, fault_code, fault_summary, diagnostic_json=None):
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO hermes_incidents
                    (service_name, environment, opened_at, severity, fault_code,
                     fault_summary, diagnostic_json, status, description, llm_reasoning)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, %s)""",
                    (
                        self._service_name, self._environment,
                        datetime.now(timezone.utc), severity, fault_code,
                        fault_summary, diagnostic_json,
                        f"Incident auto-opened by watchdog: {fault_code}",
                        json.dumps({"rationale": f"Watchdog detected {fault_code}", "source": "WO-0001A"}),
                    )
                )
                incident_id = cur.lastrowid
            self._log('warning', f"Incident #{incident_id} opened: [{fault_code}] {fault_summary}")
            return incident_id
        except Exception as e:
            self._log('error', f"[{FaultCode.INCIDENT_WRITE_FAILED}] Failed to open incident: {e}")
            self._conn = None
            return None

    def close_incident(self, incident_id, resolution_json=None):
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE hermes_incidents SET closed_at = %s, status = 'RESOLVED', resolution_json = %s
                    WHERE incident_id = %s AND status IN ('OPEN', 'RESOLVING')""",
                    (datetime.now(timezone.utc), resolution_json, incident_id)
                )
            self._log('info', f"Incident #{incident_id} resolved.")
        except Exception as e:
            self._log('error', f"Failed to close incident #{incident_id}: {e}")
            self._conn = None

    def get_open_incidents(self) -> list:
        try:
            conn = self._get_conn()
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(
                    """SELECT incident_id, fault_code, fault_summary, opened_at, severity
                    FROM hermes_incidents
                    WHERE service_name = %s AND environment = %s AND status IN ('OPEN', 'RESOLVING')
                    ORDER BY opened_at DESC""",
                    (self._service_name, self._environment)
                )
                return cur.fetchall()
        except Exception as e:
            self._log('error', f"Failed to read open incidents: {e}")
            self._conn = None
            return []


# ============================================================
# Watchdog — the runtime evaluator
# ============================================================

class HermesWatchdog:
    """
    Runtime staleness watchdog for HERMES.

    WO-0001A architecture:
    - In-memory hot state for tick/candle timestamps (updated every tick, zero DB cost)
    - Watchdog evaluation loop reads in-memory state (cadence-driven)
    - DB persistence on state transitions + periodic heartbeat
    """

    def __init__(self, service_state, persistence: HealthPersistence,
                 config: dict, market_hours_checker, logger=None, market_truth_checker=None):
        self._state = service_state
        self._persist = persistence
        self._config = config
        self._is_market_open = market_hours_checker
        # WO-HELM-HERMES-MARKET-HOURS-AWARE-STREAM-HEALTH-...-0001: optional governed DST-aware per-instrument truth checker
        # (utils.hermes_market_hours_health_v1.DstAwareMarketHours). When None, the legacy SQL MarketHoursPolicy is used
        # (zero behaviour change). When injected, it supersedes the fixed-UTC per-instrument schedule so a scheduled
        # market-closed instrument (e.g. the XAU_USD daily NY rollover break, DST-correct) is not treated as a stale fault.
        self._market_truth_checker = market_truth_checker
        self._logger = logger

        # ---- Per-instrument in-memory state (WO-0010) ----
        self._instrument_last_tick = {}      # {instrument: datetime}
        self._instrument_last_m1 = {}        # {instrument: datetime}
        self._instrument_health = {}         # {instrument: (health_state, reason_code)}
        self._instruments = []               # populated on first tick

        # ---- Per-instrument recovery trigger state ----
        # WO-HERMES-SIGNAL-SERVICE-DEV-PER-INSTRUMENT-RESUBSCRIBE-REPAIR-0001:
        # Track sustained RED per instrument; raise a recovery request that the
        # stream task consumes between ticks. OANDA v20 has no per-instrument
        # resubscribe; the request triggers a full-stream reconnect.
        self._per_instrument_red_since = {}       # {instrument: datetime first RED}
        self._recovery_request_pending = False
        self._recovery_request_reason = None
        self._last_recovery_request_at = None
        self._recovery_attempts_window = []       # list[datetime]; trimmed to last hour

        # ---- In-memory hot state (updated by record_* calls, zero DB cost) ----
        self._last_tick_utc = None
        self._last_stream_msg_utc = None
        self._last_candle_m1_utc = None
        self._last_signal_utc = None

        # ---- State machine (in-memory, persisted on change) ----
        self._current_stream_state = StreamState.DISCONNECTED
        self._current_health_state = HealthState.RED
        self._current_recovery_state = RecoveryState.IDLE
        self._current_data_flow_state = DataFlowState.UNKNOWN
        self._current_fault_code = None
        self._active_incident_id = None
        self._running = False
        self._task = None

        # Proof window
        self._proof_window_start = None
        self._proof_window_duration = config.get('recovery_proof_window_sec', 30)

        # Recovery tracking
        self._recovery_attempt_no = 0
        self._max_recovery_attempts = config.get('max_recovery_attempts', 5)

        # Persistence heartbeat tracking
        self._last_persist_time = 0.0  # monotonic
        self._persist_interval = config.get('watchdog_interval_sec', 15)
        self._state_dirty = True  # Force initial persist

        # Fatal exit callback
        self._fatal_exit_callback = None

    def _log(self, level, msg):
        if self._logger:
            getattr(self._logger, level)(msg)

    # ----------------------------------------------------------
    # Public interface — called from stream task (HOT PATH)
    # ----------------------------------------------------------

    def set_stream_state(self, new_state: str):
        """Update stream state. In-memory + marks dirty for next persist."""
        old = self._current_stream_state
        if old != new_state:
            self._current_stream_state = new_state
            self._state_dirty = True
            self._log('info', f"Stream state: {old} -> {new_state}")

    def record_tick(self, tick_utc: datetime, instrument: str = None):
        """Called on every tick. In-memory only — zero DB cost."""
        self._last_tick_utc = tick_utc
        self._last_stream_msg_utc = tick_utc

        # WO-0010: per-instrument tick tracking
        if instrument:
            self._instrument_last_tick[instrument] = tick_utc
            if instrument not in self._instruments:
                self._instruments.append(instrument)

        # If unproven or stale, promote to flowing
        if self._current_stream_state in (StreamState.CONNECTED_UNPROVEN, StreamState.STALE):
            self._promote_to_flowing()

    def record_stream_message(self, msg_utc: datetime):
        """Called on heartbeat. In-memory only."""
        self._last_stream_msg_utc = msg_utc

    def record_candle_m1(self, candle_utc: datetime, instrument: str = None):
        """Called on M1 candle complete. In-memory only."""
        self._last_candle_m1_utc = candle_utc

        # WO-0010: per-instrument M1 tracking
        if instrument:
            self._instrument_last_m1[instrument] = candle_utc

    def record_signal(self, signal_utc: datetime):
        """Called on signal publish. In-memory only."""
        self._last_signal_utc = signal_utc

    def enter_proof_window(self):
        """Called after reconnect. Starts proof timer."""
        self._proof_window_start = datetime.now(timezone.utc)
        self.set_stream_state(StreamState.CONNECTED_UNPROVEN)
        self._set_recovery_state(RecoveryState.PROOF_WINDOW)
        self._log('info', f"Proof window opened ({self._proof_window_duration}s)")

    def record_recovery_attempt(self):
        self._recovery_attempt_no += 1
        self._state_dirty = True

    def reset_recovery(self):
        self._recovery_attempt_no = 0
        self._set_recovery_state(RecoveryState.IDLE)
        self._current_fault_code = None
        self._state_dirty = True

        if self._active_incident_id:
            self._persist.close_incident(
                self._active_incident_id,
                '{"resolution": "Data flow restored after recovery"}'
            )
            self._active_incident_id = None

    def set_fatal_exit_callback(self, callback):
        self._fatal_exit_callback = callback

    @property
    def is_recovery_exhausted(self) -> bool:
        return (self._max_recovery_attempts > 0
                and self._recovery_attempt_no >= self._max_recovery_attempts)

    @property
    def recovery_attempt_no(self) -> int:
        return self._recovery_attempt_no

    @property
    def stream_state(self) -> str:
        return self._current_stream_state

    @property
    def health_state(self) -> str:
        return self._current_health_state

    @property
    def fault_code(self) -> Optional[str]:
        return self._current_fault_code

    def get_health_snapshot(self) -> dict:
        """Build health snapshot from IN-MEMORY state (fast, no DB read for hot path)."""
        now = datetime.now(timezone.utc)

        tick_age_s = None
        if self._last_tick_utc:
            lt = self._last_tick_utc
            if hasattr(lt, 'tzinfo') and lt.tzinfo is None:
                lt = lt.replace(tzinfo=timezone.utc)
            tick_age_s = round((now - lt).total_seconds(), 1)

        candle_age_s = None
        if self._last_candle_m1_utc:
            lc = self._last_candle_m1_utc
            if hasattr(lc, 'tzinfo') and lc.tzinfo is None:
                lc = lc.replace(tzinfo=timezone.utc)
            candle_age_s = round((now - lc).total_seconds(), 1)

        market_open, market_reason = self._is_market_open(now.replace(tzinfo=None))

        # Gap truth (lazy load, not on every tick)
        unresolved_gap_count = 0
        latest_gap_summary = None
        try:
            from utils.gap_scanner import GapLedger
            gap_ledger = GapLedger(self._persist._db_config)
            unresolved_gap_count = gap_ledger.get_unresolved_count()
            latest_gap = gap_ledger.get_latest_gap_summary()
            if latest_gap:
                latest_gap_summary = {
                    "gap_id": latest_gap.get("gap_id"),
                    "artifact_type": latest_gap.get("artifact_type"),
                    "instrument": latest_gap.get("instrument"),
                    "timeframe": latest_gap.get("timeframe"),
                    "gap_start_utc": str(latest_gap.get("gap_start_utc")),
                    "gap_end_utc": str(latest_gap.get("gap_end_utc")),
                    "missing_count": latest_gap.get("missing_count"),
                    "status": latest_gap.get("status"),
                }
        except Exception:
            pass  # Gap scanner may not be available

        open_incidents = self._persist.get_open_incidents()

        return {
            "health_state": self._current_health_state,
            "stream_state": self._current_stream_state,
            "data_flow_state": self._current_data_flow_state,
            "recovery_state": self._current_recovery_state,
            "fault_code": self._current_fault_code,
            "last_tick_utc": str(self._last_tick_utc) if self._last_tick_utc else None,
            "tick_age_seconds": tick_age_s,
            "last_candle_m1_utc": str(self._last_candle_m1_utc) if self._last_candle_m1_utc else None,
            "candle_age_seconds": candle_age_s,
            "last_signal_utc": str(self._last_signal_utc) if self._last_signal_utc else None,
            "recovery_attempt_no": self._recovery_attempt_no,
            "market_open": market_open,
            "market_reason": market_reason,
            "open_incidents": [
                {"incident_id": i['incident_id'], "fault_code": i['fault_code'],
                 "severity": i['severity'], "opened_at": str(i['opened_at'])}
                for i in open_incidents
            ],
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "unresolved_gap_count": unresolved_gap_count,
            "latest_gap_summary": latest_gap_summary,
            "instruments": {
                inst: {
                    "health_state": health,
                    "reason_code": reason,
                    "last_tick_utc": str(self._instrument_last_tick.get(inst)) if self._instrument_last_tick.get(inst) else None,
                    "last_m1_persisted_utc": str(self._instrument_last_m1.get(inst)) if self._instrument_last_m1.get(inst) else None,
                }
                for inst, (health, reason) in self._instrument_health.items()
            },
        }

    # ----------------------------------------------------------
    # Watchdog loop
    # ----------------------------------------------------------

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self):
        interval = self._config.get('watchdog_interval_sec', 15)
        tick_threshold = self._config.get('tick_staleness_threshold_sec', 120)
        candle_threshold = self._config.get('candle_staleness_threshold_sec', 180)

        self._log('info',
            f"Watchdog started: interval={interval}s, "
            f"tick_threshold={tick_threshold}s, candle_threshold={candle_threshold}s, "
            f"proof_window={self._proof_window_duration}s, "
            f"max_recovery={self._max_recovery_attempts}")

        while self._running:
            try:
                await self._evaluate(tick_threshold, candle_threshold)
                self._evaluate_instruments(tick_threshold, candle_threshold)
                self._persist_if_needed()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log('error', f"Watchdog evaluation error: {e}")
                await asyncio.sleep(interval)

        # Final persist on shutdown
        self._persist_state()
        self._log('info', "Watchdog stopped.")

    def _evaluate_instruments(self, tick_threshold, candle_threshold):
        """WO-0010: Evaluate per-instrument health from in-memory state."""
        now = datetime.now(timezone.utc)

        # Check market hours for each tracked instrument.
        # WO-...-MARKET-HOURS-...-0001: prefer the injected governed DST-aware truth checker (per-instrument, NY-tz,
        # daily-break aware). Fall back to the legacy fixed-UTC SQL MarketHoursPolicy only when it is not injected.
        mh_policy = self._market_truth_checker
        if mh_policy is None:
            try:
                from utils.market_hours_policy import MarketHoursPolicy
                mh_policy = MarketHoursPolicy(self._persist._db_config)
            except Exception:
                mh_policy = None

        degraded_count = 0
        red_count = 0

        for instrument in self._instruments:
            truth_expected = True
            reason = None

            # Market hours check
            if mh_policy:
                try:
                    truth_expected, mh_reason = mh_policy.is_truth_expected(instrument, now)
                    if not truth_expected:
                        reason = mh_reason
                except Exception:
                    pass

            if not truth_expected:
                self._instrument_health[instrument] = (HealthState.AMBER, reason or FaultCode.MARKET_CLOSED)
                degraded_count += 1
                continue

            # Tick freshness
            last_tick = self._instrument_last_tick.get(instrument)
            if last_tick:
                lt = last_tick.replace(tzinfo=timezone.utc) if last_tick.tzinfo is None else last_tick
                tick_age = (now - lt).total_seconds()
                if tick_age > tick_threshold:
                    self._instrument_health[instrument] = (HealthState.RED, FaultCode.INSTRUMENT_TICK_STALE)
                    red_count += 1
                    continue

            # M1 persistence freshness
            last_m1 = self._instrument_last_m1.get(instrument)
            if last_m1:
                lm = last_m1.replace(tzinfo=timezone.utc) if last_m1.tzinfo is None else last_m1
                m1_age = (now - lm).total_seconds()
                if m1_age > candle_threshold:
                    self._instrument_health[instrument] = (HealthState.RED, FaultCode.INSTRUMENT_M1_STALE)
                    red_count += 1
                    continue

            # If we have tick data but no M1 yet (just started), AMBER
            if last_tick and not last_m1:
                self._instrument_health[instrument] = (HealthState.AMBER, "AWAITING_FIRST_M1")
                degraded_count += 1
                continue

            # Healthy
            self._instrument_health[instrument] = (HealthState.GREEN, None)

        # WO-0010: Derive global health from instrument states
        # Rule: global GREEN only if ALL truth-expected instruments are GREEN
        #        global RED if any truth-expected instrument is RED
        #        global AMBER otherwise
        if red_count > 0:
            # Do NOT override global to GREEN if any instrument is RED
            if self._current_health_state == HealthState.GREEN:
                self._set_health(HealthState.AMBER, DataFlowState.STALE, None)
                self._state_dirty = True

        # WO-HERMES-SIGNAL-SERVICE-DEV-PER-INSTRUMENT-RESUBSCRIBE-REPAIR-0001:
        # Track sustained per-instrument RED + raise recovery requests + flip
        # global stream state to PARTIAL_FLOWING when applicable.
        self._evaluate_per_instrument_recovery(now, red_count)

        # Persist per-instrument health to DB (cadence-driven, not per-tick)
        self._persist_instrument_health(now)

    def _evaluate_per_instrument_recovery(self, now, red_count):
        """Track sustained per-instrument RED + request stream recovery if needed.

        Per WO-HERMES-SIGNAL-SERVICE-DEV-PER-INSTRUMENT-RESUBSCRIBE-REPAIR-0001.
        OANDA v20 has no per-instrument resubscribe (confirmed via adapter
        inspection); this method requests a full-stream reconnect by raising
        an in-memory flag that the streaming loop consumes via
        ``consume_recovery_request()``. The existing reconnect path then runs.

        Cooldown + max-attempts cap prevent reconnect storms. Cross-instrument
        isolation: a single instrument RED does not falsely mark healthy
        instruments. PARTIAL_FLOWING global state is set when red_count > 0
        and the global stream is otherwise FLOWING.
        """
        # Track RED-start timestamps; clear when instrument recovers.
        for inst, (health, _reason) in self._instrument_health.items():
            if health == HealthState.RED:
                if inst not in self._per_instrument_red_since:
                    self._per_instrument_red_since[inst] = now
            else:
                self._per_instrument_red_since.pop(inst, None)

        # PARTIAL_FLOWING state derivation. Only flip from/to FLOWING — leave
        # STALE/RECOVERING/FAILED untouched so the existing recovery machinery
        # is not interfered with.
        if red_count > 0 and self._current_stream_state == StreamState.FLOWING:
            self.set_stream_state(StreamState.PARTIAL_FLOWING)
        elif red_count == 0 and self._current_stream_state == StreamState.PARTIAL_FLOWING:
            self.set_stream_state(StreamState.FLOWING)

        # If a recovery request is already pending and unconsumed by the stream
        # task, do not re-raise; just wait.
        if self._recovery_request_pending:
            return

        # WO-HERMES-PER-INSTRUMENT-RECOVERY-CONFIG-PROMOTION-0001:
        # Read thresholds from governed hermes_config (loaded into self._config
        # at lifespan init). Fail-loud: missing key raises KeyError; the
        # outer service watchdog will surface that loudly via journal.
        sustained_red_threshold_sec = self._config['per_instrument_sustained_red_threshold_sec']
        recovery_cooldown_sec = self._config['per_instrument_recovery_cooldown_sec']
        max_recovery_attempts_per_hour = self._config['per_instrument_max_recovery_attempts_per_hour']

        # Identify instruments with sustained RED past the hysteresis threshold.
        sustained = [
            (inst, (now - since).total_seconds())
            for inst, since in self._per_instrument_red_since.items()
            if (now - since).total_seconds() >= sustained_red_threshold_sec
        ]
        if not sustained:
            return

        # Cooldown: do not re-request within recovery_cooldown_sec.
        if self._last_recovery_request_at is not None:
            since_last = (now - self._last_recovery_request_at).total_seconds()
            if since_last < recovery_cooldown_sec:
                return

        # Trim attempts window to last hour; enforce max-attempts cap.
        cutoff = now - timedelta(seconds=3600)
        self._recovery_attempts_window = [
            t for t in self._recovery_attempts_window if t >= cutoff
        ]
        if len(self._recovery_attempts_window) >= max_recovery_attempts_per_hour:
            self._log(
                'warning',
                f"Per-instrument recovery SUPPRESSED: max "
                f"{max_recovery_attempts_per_hour} attempts/hour "
                f"reached. Sustained RED instruments: {[i for i, _ in sustained]}"
            )
            return

        # Raise the recovery request.
        inst, age_s = sustained[0]
        reason = (
            f"sustained_red instrument={inst} age_s={age_s:.0f} "
            f"red_count={red_count} state={self._current_stream_state}"
        )
        self._recovery_request_pending = True
        self._recovery_request_reason = reason
        self._last_recovery_request_at = now
        self._recovery_attempts_window.append(now)
        self._log('warning', f"Per-instrument recovery request raised: {reason}")

    def consume_recovery_request(self):
        """Atomically consume a pending per-instrument recovery request.

        Called by main.py oanda_stream_task between ticks. Returns the reason
        string if a recovery was requested (and clears the flag), else None.
        Per WO-HERMES-SIGNAL-SERVICE-DEV-PER-INSTRUMENT-RESUBSCRIBE-REPAIR-0001.
        """
        if not self._recovery_request_pending:
            return None
        reason = self._recovery_request_reason
        self._recovery_request_pending = False
        self._recovery_request_reason = None
        return reason

    def _persist_instrument_health(self, now):
        """Write per-instrument health to hermes_instrument_health table."""
        if not self._instrument_health:
            return

        try:
            conn = self._persist._get_conn()
            with conn.cursor() as cur:
                for instrument, (health, reason) in self._instrument_health.items():
                    last_tick = self._instrument_last_tick.get(instrument)
                    last_m1 = self._instrument_last_m1.get(instrument)

                    tick_age = None
                    if last_tick:
                        lt = last_tick.replace(tzinfo=timezone.utc) if last_tick.tzinfo is None else last_tick
                        tick_age = round((now - lt).total_seconds(), 1)

                    m1_age = None
                    if last_m1:
                        lm = last_m1.replace(tzinfo=timezone.utc) if last_m1.tzinfo is None else last_m1
                        m1_age = round((now - lm).total_seconds(), 1)

                    truth_expected = 1 if health != HealthState.AMBER or reason not in (FaultCode.MARKET_CLOSED, "MARKET_CLOSED") else 0

                    cur.execute(
                        """INSERT INTO hermes_instrument_health
                        (instrument, truth_expected, last_tick_utc, last_m1_persisted_utc,
                         tick_age_seconds, m1_age_seconds, health_state, reason_code,
                         updated_at_utc, description, llm_reasoning)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            truth_expected=VALUES(truth_expected),
                            last_tick_utc=VALUES(last_tick_utc),
                            last_m1_persisted_utc=VALUES(last_m1_persisted_utc),
                            tick_age_seconds=VALUES(tick_age_seconds),
                            m1_age_seconds=VALUES(m1_age_seconds),
                            health_state=VALUES(health_state),
                            reason_code=VALUES(reason_code),
                            updated_at_utc=VALUES(updated_at_utc)""",
                        (instrument, truth_expected, last_tick, last_m1,
                         tick_age, m1_age, health, reason, now,
                         f"Per-instrument health: {instrument}",
                         '{"rationale":"WO-0010 per-instrument watchdog","source":"watchdog"}')
                    )
        except Exception as e:
            self._log('error', f"Failed to persist instrument health: {e}")

    def _persist_if_needed(self):
        """Write health to DB if dirty or heartbeat interval elapsed."""
        now = time.monotonic()
        should_persist = self._state_dirty or (now - self._last_persist_time >= self._persist_interval)

        if should_persist:
            self._persist_state()
            self._state_dirty = False
            self._last_persist_time = now

    def _persist_state(self):
        """Write current in-memory state to DB (single call)."""
        self._persist.update_health(
            last_tick_utc=self._last_tick_utc,
            last_stream_msg_utc=self._last_stream_msg_utc,
            last_candle_m1_utc=self._last_candle_m1_utc,
            last_signal_utc=self._last_signal_utc,
            stream_state=self._current_stream_state,
            data_flow_state=self._current_data_flow_state,
            health_state=self._current_health_state,
            recovery_state=self._current_recovery_state,
            fault_code=self._current_fault_code,
            recovery_attempt_no=self._recovery_attempt_no,
        )

    async def _evaluate(self, tick_threshold, candle_threshold):
        """Single evaluation cycle using IN-MEMORY state."""
        now = datetime.now(timezone.utc)
        now_naive = now.replace(tzinfo=None)
        market_open, _ = self._is_market_open(now_naive)

        # Check proof window expiry
        if self._current_stream_state == StreamState.CONNECTED_UNPROVEN:
            if self._proof_window_start:
                elapsed = (now - self._proof_window_start).total_seconds()
                if elapsed > self._proof_window_duration:
                    await self._fault(
                        FaultCode.FALSE_CONNECT,
                        f"Proof window expired after {elapsed:.0f}s with no fresh tick",
                        "CRITICAL",
                    )
                    self.set_stream_state(StreamState.STALE)
                    self._set_recovery_state(RecoveryState.FAILED)
                    return

        # If FLOWING, check for staleness
        if self._current_stream_state == StreamState.FLOWING:
            # Tick staleness (from in-memory)
            if self._last_tick_utc and market_open:
                lt = self._last_tick_utc
                if hasattr(lt, 'tzinfo') and lt.tzinfo is None:
                    lt = lt.replace(tzinfo=timezone.utc)
                tick_age = (now - lt).total_seconds()
                if tick_age > tick_threshold:
                    await self._fault(
                        FaultCode.STALE_TICK,
                        f"No tick for {tick_age:.0f}s (threshold: {tick_threshold}s) during market hours",
                        "CRITICAL",
                    )
                    self.set_stream_state(StreamState.STALE)

                    # HERMES-RESILIENCE-001: Auto-trigger recovery on STALE
                    # Weekend-to-weekday transitions leave the stream in FLOWING
                    # with a 42-hour-old last tick. Instead of staying STALE,
                    # transition to RECOVERING so the reconnect loop picks it up.
                    if tick_age > 3600:  # > 1 hour stale = likely weekend gap
                        logger.warning(
                            f"[HERMES_AUTO_RECOVER] Tick age {tick_age:.0f}s suggests "
                            f"weekend gap. Transitioning to RECOVERING for auto-reconnect."
                        )
                        self.set_stream_state(StreamState.RECOVERING)
                        self.record_recovery_attempt()

                    return

            # Candle M1 staleness (from in-memory)
            if self._last_candle_m1_utc and market_open:
                lc = self._last_candle_m1_utc
                if hasattr(lc, 'tzinfo') and lc.tzinfo is None:
                    lc = lc.replace(tzinfo=timezone.utc)
                candle_age = (now - lc).total_seconds()
                if candle_age > candle_threshold:
                    await self._fault(
                        FaultCode.STALE_CANDLE,
                        f"No M1 candle for {candle_age:.0f}s (threshold: {candle_threshold}s)",
                        "CRITICAL",
                    )
                    if self._current_stream_state == StreamState.FLOWING:
                        self.set_stream_state(StreamState.STALE)
                    return

        # If FLOWING and not stale during market hours → GREEN
        if self._current_stream_state == StreamState.FLOWING and market_open:
            if self._current_health_state != HealthState.GREEN:
                self._set_health(HealthState.GREEN, DataFlowState.ACTIVE, None)

        # Market closed — AMBER, not RED
        if not market_open and self._current_stream_state in (StreamState.FLOWING, StreamState.CONNECTED_UNPROVEN):
            if self._current_health_state == HealthState.RED:
                self._set_health(HealthState.AMBER, DataFlowState.STALE, None)

        # Recovery exhaustion
        if self.is_recovery_exhausted and self._current_recovery_state != RecoveryState.EXHAUSTED:
            await self._handle_exhaustion()

    # ----------------------------------------------------------
    # State transitions
    # ----------------------------------------------------------

    def _promote_to_flowing(self):
        self._log('info', "Data flow proven — stream promoted to FLOWING")
        self.set_stream_state(StreamState.FLOWING)
        self._set_health(HealthState.GREEN, DataFlowState.ACTIVE, None)
        self.reset_recovery()
        self._proof_window_start = None
        # Force immediate persist on promotion
        self._state_dirty = True

    def _set_health(self, health, data_flow, fault_code):
        changed = (self._current_health_state != health or
                   self._current_data_flow_state != data_flow or
                   self._current_fault_code != fault_code)
        self._current_health_state = health
        self._current_data_flow_state = data_flow
        self._current_fault_code = fault_code
        if changed:
            self._state_dirty = True

    def _set_recovery_state(self, state):
        if self._current_recovery_state != state:
            self._current_recovery_state = state
            self._state_dirty = True

    async def _fault(self, fault_code, summary, severity):
        self._log('critical', f"[{fault_code}] {summary}")
        self._current_fault_code = fault_code
        self._set_health(
            HealthState.RED,
            DataFlowState.DEAD if fault_code in (FaultCode.FALSE_CONNECT, FaultCode.RECOVERY_EXHAUSTED) else DataFlowState.STALE,
            fault_code,
        )

        diagnostic = json.dumps({
            "fault_code": fault_code, "summary": summary,
            "stream_state": self._current_stream_state,
            "recovery_attempt_no": self._recovery_attempt_no,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        })

        # Force immediate persist on fault
        self._state_dirty = True
        self._persist_state()
        self._persist.update_health(fault_detail_json=diagnostic)

        if not self._active_incident_id:
            self._active_incident_id = self._persist.open_incident(
                severity=severity, fault_code=fault_code,
                fault_summary=summary, diagnostic_json=diagnostic,
            )

    async def _handle_exhaustion(self):
        self._log('critical',
            f"[{FaultCode.RECOVERY_EXHAUSTED}] "
            f"Recovery exhausted after {self._recovery_attempt_no} attempts. Fatal exit path.")

        self._set_recovery_state(RecoveryState.EXHAUSTED)
        diagnostic = json.dumps({
            "fault_code": FaultCode.RECOVERY_EXHAUSTED,
            "recovery_attempts": self._recovery_attempt_no,
            "max_attempts": self._max_recovery_attempts,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        })

        self._set_health(HealthState.RED, DataFlowState.DEAD, FaultCode.RECOVERY_EXHAUSTED)
        self._persist_state()
        self._persist.update_health(fault_detail_json=diagnostic)

        self._persist.open_incident(
            severity="FATAL", fault_code=FaultCode.RECOVERY_EXHAUSTED,
            fault_summary=f"Recovery exhausted after {self._recovery_attempt_no} attempts. Fatal exit.",
            diagnostic_json=diagnostic,
        )

        if self._fatal_exit_callback:
            self._log('critical', "Executing fatal exit callback...")
            await self._fatal_exit_callback()
        else:
            self._log('critical', "No fatal exit callback set. Exiting with code 1.")
            sys.exit(1)
