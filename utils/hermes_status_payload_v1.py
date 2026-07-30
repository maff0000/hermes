"""
HERMES /status payload builder — WP2 (authoritative-consistent, backward-compatible).
WO-HELM-HERMES-CONTAINER-MVP-WP2-CANONICAL-BUILD-AND-EXTERNALISED-CONFIGURATION-0001.

Pure, side-effect-free helper factored out of main.py so it is testable WITHOUT importing the heavy
FastAPI app (no httpx/redis at import time). main.py imports build_status_payload from here.

Contract:
  - Backward-compatible: keeps service, version, started_at, active_source, instruments, tick_count and
    the adapters block.
  - FIXES the oanda adapters block: the CURRENT state is derived from the AUTHORITATIVE watchdog
    stream_state (NEVER "disconnected" when FLOWING); the misleading legacy fields are DEMOTED to
    explicitly historical (legacy_adapter_state / historical_cumulative_error_count /
    historical_last_error + historical_last_error_is_current=False).
  - ADDS: build_identity, config_version, authoritative_health, authoritative_stream_state,
    consumer_live, runtime_mode, runner_count, freshness_summary, sql_state, redis_state,
    last_recovery_utc. NO secret values ever.
"""

from env_config import get_env
from utils.hermes_build_identity_v1 import build_identity


def map_stream_to_current_state(authoritative_stream_state):
    """Map the authoritative stream_state to a 'current state' word that NEVER says 'disconnected'
    when the stream is FLOWING. Falls back to a lowercased echo, else 'unknown'."""
    if authoritative_stream_state is None:
        return "unknown"
    s = str(authoritative_stream_state).upper()
    if s == "FLOWING":
        return "connected"
    return authoritative_stream_state.lower() if isinstance(authoritative_stream_state, str) else "unknown"


def summarise_freshness(snap):
    """Bounded per-instrument last_tick summary from the authoritative snapshot (no secrets)."""
    instruments = (snap or {}).get("instruments") or {}
    summary = {}
    for inst, info in instruments.items():
        if isinstance(info, dict):
            summary[inst] = {
                "last_tick_utc": info.get("last_tick_utc"),
                "health_state": info.get("health_state"),
            }
    return summary


def mask_account(acct):
    """Mask an OANDA account id to its first 6 chars (governed masked form). Never the full value."""
    if not acct:
        return None
    s = str(acct)
    return (s[:6] + "…") if len(s) > 6 else s


def build_status_payload(state) -> dict:
    """Pure, testable /status payload builder. NO secret values."""
    snap = state.watchdog.get_health_snapshot() if getattr(state, "watchdog", None) else {}
    authoritative_stream_state = snap.get("stream_state") if snap else None
    authoritative_health = snap.get("health_state") if snap else None
    is_flowing = authoritative_stream_state in ("FLOWING",)

    adapters = {}
    if getattr(state, "oanda_adapter", None):
        legacy = state.oanda_adapter.health.to_dict()  # state, is_healthy, last_tick_at, tick_count, error_count, last_error
        adapters["oanda"] = {
            # CURRENT state derived from the AUTHORITATIVE watchdog — NOT the legacy "disconnected".
            "state": map_stream_to_current_state(authoritative_stream_state),
            "current_stream_state": authoritative_stream_state,
            "is_flowing": is_flowing,
            "last_tick_at": legacy.get("last_tick_at"),
            # Legacy fields DEMOTED to explicitly historical (never presented as current).
            "legacy_adapter_state": legacy.get("state"),
            "historical_cumulative_error_count": legacy.get("error_count"),
            "historical_last_error": legacy.get("last_error"),
            "historical_last_error_is_current": False,
        }
    if getattr(state, "ibkr_adapter", None):
        adapters["ibkr"] = state.ibkr_adapter.health.to_dict()

    # Runner count from the publisher supervisor if resolvable, else None (no I/O).
    runner_count = None
    sup = getattr(state, "publisher_supervisor", None)
    if sup is not None:
        try:
            st = sup.status()
            if isinstance(st, dict) and isinstance(st.get("runners"), list):
                runner_count = len(st["runners"])
        except Exception:
            runner_count = None

    # Best-effort SQL/Redis state from ENV presence ONLY — no new connections, no creds exposed.
    sql_state = "configured" if get_env("DB_HOST") else "unknown"
    redis_state = "configured" if get_env("REDIS_HOST") else "unknown"

    bid = build_identity()

    return {
        # ---- backward-compatible fields (unchanged names) ----
        "service": "signal-service",
        "version": "0.1.0",
        "started_at": state.started_at.isoformat() if getattr(state, "started_at", None) else None,
        "active_source": state.active_source.value if getattr(state, "active_source", None) else None,
        "instruments": state.config.instruments if getattr(state, "config", None) else [],
        "adapters": adapters,
        "tick_count": len(getattr(state, "latest_ticks", {}) or {}),
        # ---- WP2 authoritative + identity additions (no secrets) ----
        "build_identity": bid,
        "config_version": bid.get("config_version"),
        "authoritative_health": authoritative_health,
        "authoritative_stream_state": authoritative_stream_state,
        "consumer_live": get_env("CONSUMER_LIVE", default="false"),
        "runtime_mode": get_env("RUN_ENV", default="STAGING"),
        "runner_count": runner_count,
        "freshness_summary": summarise_freshness(snap),
        "sql_state": sql_state,
        "redis_state": redis_state,
        "last_recovery_utc": snap.get("last_recovery_utc") if snap else None,
    }
