"""HERMES readiness AUTHORITATIVE observers v1.
WO-HELM-HERMES-DEPLOYED-PROVENANCE-SCOPE-READINESS-AND-REPRODUCIBLE-DARK-CONFIG-0001 (continuation).

Three bounded, READ-ONLY observers that replace the previously hard-coded / omitted readiness safety inputs with
real runtime derivations:

  observe_stream               -> actual active pricing-stream count from the live OANDA adapter + its stream task
  observe_order_path           -> order-execution authority presence from registered routes + loaded components + config
  observe_inactive_publication -> whether any NOT_ENABLED registry row has an emitted Advanced-v1 contract (5 families)

INVARIANTS (all three):
  * NEVER mutate: no write, no key creation, no connect/subscribe/reconnect, no worker/stream instantiation.
  * NEVER unbounded: no Redis scan, no wildcard, no history query, no host-process inspection; exact keys only.
  * FAIL-CLOSED: an observation that cannot complete returns an explicit unknown/unobserved marker — it NEVER
    returns the safe value (1 stream / order-absent / zero inactive publications) by default.
No secrets are read or returned.
"""
from __future__ import annotations
from datetime import datetime, timezone

UTC = timezone.utc

# A stream whose last heartbeat is older than this is NOT counted as an active stream (mirrors AdapterHealth.is_healthy).
STREAM_STALE_AFTER_SEC_DEFAULT = 30

# Order-path evidence markers (strict; any hit is fail-closed presence). No ticker/instrument logic.
ORDER_ROUTE_MARKERS = ("/orders", "/order", "/execution", "/execute", "/trade", "/positions/close")
ORDER_STATE_ATTRS = ("order_router", "order_client", "broker_order_client", "execution_service",
                     "execution_engine", "order_queue_consumer", "order_endpoint", "order_manager")
ORDER_ENV_FLAGS = ("HERMES_ORDER_ENABLED", "HERMES_EXECUTION_ENABLED", "HERMES_ORDER_AUTHORITY",
                   "ORDER_ROUTER_ENABLED", "HERMES_TRADING_EXECUTION_ENABLED")


def _iso(dt):
    return None if dt is None else dt.astimezone(UTC).isoformat()


def _truthy(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------- authoritative runtime -> readiness stream projection
# The ONE governed pricing stream's authoritative live state is owned by the watchdog — the SAME runtime truth
# /health reports (stream_state promoted to FLOWING only on a REAL price tick, never on a heartbeat; last_tick_utc
# advanced only on a real tick). Readiness must observe THAT authority, not a second, independently-maintained
# truth. The base pricing adapter's own AdapterHealth.state is a lower-level connection flag that is NOT maintained
# CONNECTED while the runtime streams via adapter.stream(), and its last_tick_at is refreshed by heartbeats — so it
# reads DISCONNECTED with a fresh heartbeat even during genuine flow. These helpers PROJECT the watchdog health
# snapshot into the read-only (adapter, last_tick) shape observe_stream already consumes, so the governed active
# semantics in _classify_one_stream are applied to the authoritative state. No new stream, no parallel heartbeat,
# no redefinition of "active" (a projected stream is ACTIVE only when the watchdog says FLOWING AND the authoritative
# last real tick is fresh). Read-only; holds no connection; performs no I/O.
_STREAM_FLOWING_STATES = ("FLOWING", "PARTIAL_FLOWING")
_STREAM_RECONNECTING_STATES = ("CONNECTED_UNPROVEN", "RECOVERING")
_STREAM_DOWN_STATES = ("DISCONNECTED", "STALE")


class _ProjectedStreamHealth:
    """Read-only AdapterHealth-shaped view (state value + last real tick) derived from the watchdog."""
    __slots__ = ("state", "last_tick_at")

    def __init__(self, state, last_tick_at):
        self.state = state                 # plain lowercase lifecycle string; _classify reads .value-or-str
        self.last_tick_at = last_tick_at


class ProjectedStream:
    """Read-only projection of the single authoritative pricing stream. Carries only the observed state; it holds
    no connection and performs no work, so observe_stream cannot mutate or drive any live component through it."""
    __slots__ = ("health",)

    def __init__(self, health):
        self.health = health


def _parse_snapshot_dt(v):
    """Parse a watchdog-snapshot timestamp (aware datetime or ISO/str) to an aware UTC datetime, else None."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except Exception:  # noqa: BLE001
        return None


def project_watchdog_stream(snapshot):
    """Map an authoritative watchdog health snapshot -> a read-only ProjectedStream for observe_stream, or None when
    no authoritative stream state is available (the caller then supplies no slot and readiness fails closed).

    Maps the governed watchdog stream_state to adapter-lifecycle vocabulary WITHOUT relaxing any threshold:
      FLOWING / PARTIAL_FLOWING           -> "connected"     (real ticks flowing)
      CONNECTED_UNPROVEN / RECOVERING     -> "reconnecting"  (not yet active)
      DISCONNECTED / STALE                -> "disconnected"  (down / no fresh flow, e.g. market-closed weekend)
      anything else / absent              -> None            (indeterminate -> fail-closed)
    last_tick_at is the watchdog's authoritative last REAL tick (never a heartbeat/candle/wall-clock value); the
    unchanged _classify_one_stream still applies the freshness gate, so a stale FLOWING can never read ACTIVE."""
    if not snapshot:
        return None
    raw = str(snapshot.get("stream_state") or "").upper()
    if raw in _STREAM_FLOWING_STATES:
        state = "connected"
    elif raw in _STREAM_RECONNECTING_STATES:
        state = "reconnecting"
    elif raw in _STREAM_DOWN_STATES:
        state = "disconnected"
    else:
        return None
    return ProjectedStream(_ProjectedStreamHealth(state, _parse_snapshot_dt(snapshot.get("last_tick_utc"))))


def authoritative_last_instrument_tick_utc(snapshot, instrument):
    """The authoritative last REAL tick UTC for `instrument` from the watchdog snapshot's per-instrument tick truth
    (advanced only by record_tick on a genuine price tick), or None. Never a heartbeat, candle, or synthetic value."""
    if not snapshot or not instrument:
        return None
    inst = (snapshot.get("instruments") or {}).get(instrument) or {}
    return _parse_snapshot_dt(inst.get("last_tick_utc"))


# ------------------------------------------------------------------ stream
def _classify_one_stream(adapter, adapter_task, now, stale_after_sec):
    """Classify a single pricing-stream adapter+task. Returns (active:0|1, health, last_tick_dt, unknown). Read-only."""
    if adapter is None:
        return 0, "ABSENT", None, False
    try:
        health = getattr(adapter, "health", None)
        st = getattr(health, "state", None)
        state_val = str(getattr(st, "value", st)).lower()
        last = getattr(health, "last_tick_at", None)
        task_running = bool(adapter_task is not None and not adapter_task.done())
        hb_age = (now - last).total_seconds() if last is not None else None
        fresh = hb_age is not None and hb_age < stale_after_sec
        if not task_running:
            return 0, "STOPPED", last, False
        if state_val == "connected" and fresh:
            return 1, "ACTIVE", last, False
        if state_val == "connected" and last is None:
            return 0, "STARTING", last, False
        if state_val == "connected" and not fresh:
            return 0, "STALE", last, False
        if state_val in ("connecting", "reconnecting"):
            return 0, "STARTING", last, False
        if state_val in ("disconnected", "failed"):
            return 0, "DISCONNECTED", last, False
        return 0, "UNKNOWN", last, True          # unrecognised state -> indeterminate, fail-closed
    except Exception:  # noqa: BLE001
        return 0, "UNKNOWN", None, True


def observe_stream(*, streams, now, stale_after_sec=STREAM_STALE_AFTER_SEC_DEFAULT):
    """Authoritative pricing-stream observation over ALL stream slots. `streams` is an iterable of
    (adapter, adapter_task) pairs (e.g. the OANDA and IBKR pricing adapters). Reads ONLY each adapter.health
    (state + last_tick_at) and whether its task is running — never connects/subscribes/instantiates.

    Detects the one-stream invariant AND a rogue SECOND active stream. Returns dict:
      configured_count   number of stream slots provided
      active_count       observed active streams (0/1/2/…); None when any slot is indeterminate
      count              int for the readiness helper (active_count, or -1 when unknown so it can never read as 1)
      health             ACTIVE (exactly one) | STOPPED (none) | MULTIPLE (>1) | UNKNOWN
      last_heartbeat_utc most-recent tick across slots (ISO-8601) or None
      unknown            True iff any slot is indeterminate (fail-closed)
    """
    pairs = list(streams or [])
    configured = len(pairs)
    active = 0
    unknown = False
    last_hb = None
    per = []
    for adapter, task in pairs:
        a, h, last, unk = _classify_one_stream(adapter, task, now, stale_after_sec)
        per.append(h)
        active += a
        unknown = unknown or unk
        if last is not None and (last_hb is None or last > last_hb):
            last_hb = last
    if unknown:
        return {"configured_count": configured, "active_count": None, "count": -1,
                "health": "UNKNOWN", "last_heartbeat_utc": _iso(last_hb), "unknown": True}
    health = "ACTIVE" if active == 1 else ("STOPPED" if active == 0 else "MULTIPLE")
    if configured == 1:
        health = per[0]                       # richer single-slot health when there is exactly one slot
    return {"configured_count": configured, "active_count": active, "count": active,
            "health": health, "last_heartbeat_utc": _iso(last_hb), "unknown": False}


# ------------------------------------------------------------------ order path
def observe_order_path(*, route_paths, state_attrs_present, env):
    """Authoritative order-execution-path observation. Read-only; inspects only injected snapshots:
      route_paths          iterable of registered app route path strings
      state_attrs_present  set of service-state attribute names that are present AND non-None
      env                  mapping of the relevant environment (already filtered by the caller)

    Returns dict: state (ABSENT|PRESENT_DISABLED|PRESENT_ENABLED|UNKNOWN), present(bool), evidence(sorted list).
    HERMES is a market-data/signal service with NO order authority; ABSENT is the authorised state. Any route,
    component, or enabling flag flips it to PRESENT_*, fail-closed.
    """
    evidence = []
    try:
        for p in route_paths or ():
            pl = str(p).lower()
            if any(m in pl for m in ORDER_ROUTE_MARKERS):
                evidence.append("route:" + str(p))
        for a in ORDER_STATE_ATTRS:
            if a in (state_attrs_present or set()):
                evidence.append("component:" + a)
        enabled = [f for f in ORDER_ENV_FLAGS if _truthy((env or {}).get(f))]
        if enabled:
            evidence.append("env_enabled:" + ",".join(sorted(enabled)))
    except Exception:  # noqa: BLE001 — unobservable -> UNKNOWN, fail-closed (never ABSENT)
        return {"state": "UNKNOWN", "present": True, "evidence": ["order-path-observation-failed"]}

    if not evidence:
        return {"state": "ABSENT", "present": False, "evidence": []}
    state = "PRESENT_ENABLED" if any(e.startswith("env_enabled:") for e in evidence) else "PRESENT_DISABLED"
    return {"state": state, "present": True, "evidence": sorted(evidence)}


# ------------------------------------------------------------------ inactive publication
def observe_inactive_publication(*, inactive_instruments, redis_client, indicator_timeframes=()):
    """Bounded, read-only detection of any Advanced-v1 publication for a NOT_ENABLED instrument across the five
    families (tick, indicators, gaps, backfill_status, feed_health). Uses EXACT canonical keys from the shared key
    factory only — no scan, no wildcard, no alias, no key creation. An existing canonical key is a CURRENT
    publication (all Advanced-v1 contracts carry a TTL, so an expired key is already gone).

    Returns dict: published_count, published_instruments(sorted), checks(exact exists() calls made), observed_ok.
    observed_ok is False when the check could not complete (no client / Redis error) -> readiness must fail-closed
    rather than report zero.
    """
    if redis_client is None:
        return {"published_count": 0, "published_instruments": [], "checks": 0, "observed_ok": False}
    # import the canonical key factory lazily (keeps this module import-light and testable)
    from utils.hermes_advanced_v1_selection_v1 import (tick_latest_key, indicator_key, gaps_key, backfill_status_key)
    from utils.hermes_feed_health_v1 import feed_health_key

    tfs = tuple(indicator_timeframes or ())
    published, checks = [], 0
    try:
        for inst in sorted(set(inactive_instruments or ())):
            keys = [tick_latest_key(inst), gaps_key(inst), backfill_status_key(inst), feed_health_key(inst)]
            keys += [indicator_key(inst, tf) for tf in tfs]
            for k in keys:
                checks += 1
                if redis_client.exists(k):
                    published.append(inst)
                    break                          # one family is enough; stop probing this instrument (bounded)
    except Exception:  # noqa: BLE001 — unobservable -> fail-closed, never report zero as safe
        return {"published_count": 0, "published_instruments": [], "checks": checks, "observed_ok": False}
    return {"published_count": len(published), "published_instruments": sorted(published),
            "checks": checks, "observed_ok": True}
