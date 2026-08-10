"""HERMES scope-aware readiness ROUTER v1.
WO-HELM-HERMES-DEPLOYED-PROVENANCE-SCOPE-READINESS-AND-REPRODUCIBLE-DARK-CONFIG-0001 (continuation).

Exposes GET /readiness as a dedicated FastAPI APIRouter so the route can be exercised at the real ASGI/HTTP layer
without importing the full production boot (main.py). The SINGLE definition of the route + assembly lives here; the
production app and the route-level tests both `include_router(router)` and both call the identical `assemble()`.

Dependency injection (FastAPI-native, no hand-rolled global mutable state): the route depends on `readiness_sources`,
which raises until overridden. Production overrides it via `app.dependency_overrides[readiness_sources]` with sources
bound to the live service state; tests override it with sources bound to fakes. Because `assemble()` runs the real
observers and the real `build_readiness_report()` for BOTH, the three formerly hard-coded/omitted safety inputs are
proven through the actual route.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from utils import hermes_readiness_surface_v1 as rs
from utils import hermes_readiness_observers_v1 as obs

router = APIRouter()


def readiness_sources():
    """Injectable readiness-sources provider. Unwired by default (fail-closed); the app MUST override it.

    A `sources` object is duck-typed and exposes these zero-arg callables:
      now()                    -> aware datetime
      build_identity()         -> build-identity dict
      load_registry()          -> (registry_dict, not_enabled_list, indicator_timeframes)  (may raise)
      load_master_mode_pilot() -> (master_enabled, publisher_mode, pilot_scope_set)          (may raise)
      load_calendar()          -> calendar-provenance dict                                   (may raise)
      core_snapshot()          -> (core_health, db_ok, redis_ok)
      stream_handles()         -> iterable of (adapter, adapter_task) pricing-stream pairs, projected from the
                                  authoritative runtime stream state (the same watchdog truth /health reports)
      last_xau_tick()          -> authoritative last REAL XAU tick UTC (aware datetime) or None   [optional]
      order_handles()          -> (route_paths, state_attrs_present, env)
      redis_client()           -> redis client or None
      seven_new_count()        -> int

    Returns None by default (UNWIRED): the application MUST override it. An unwired app fails closed to RED/503
    rather than crashing.
    """
    return None


def assemble(sources) -> dict:
    """Run the authoritative observers on the injected handles and build the readiness report. Shared by production
    and tests so the derivation is proven in-route. Every loader is fail-closed: a failure yields an unsafe-looking
    marker that drives RED, never a silent safe value."""
    now = sources.now()

    # registry (authoritative; a load failure is a fault, and leaves no inactive set to probe)
    try:
        registry, not_enabled, indicator_tfs = sources.load_registry()
    except Exception:  # noqa: BLE001
        registry, not_enabled, indicator_tfs = {"loaded": False}, [], ()

    # effective master / mode / pilot scope (fail-closed to an unsafe-looking tuple -> RED)
    try:
        master, mode, pilot = sources.load_master_mode_pilot()
    except Exception:  # noqa: BLE001
        master, mode, pilot = True, "__UNRESOLVED__", set()

    try:
        calprov = sources.load_calendar()
    except Exception:  # noqa: BLE001
        calprov = {}

    core, db_ok, redis_ok = sources.core_snapshot()

    # --- the three corrected authoritative inputs ---
    stream = obs.observe_stream(streams=sources.stream_handles(), now=now)

    # authoritative last real XAU tick — from the SAME runtime truth /health reports (never a heartbeat/synthetic).
    # Optional on the sources contract; absent -> None (observability field only, does not gate readiness).
    _last_xau = getattr(sources, "last_xau_tick", None)
    last_xau_tick_utc = _last_xau() if callable(_last_xau) else None

    route_paths, state_attrs, env = sources.order_handles()
    order = obs.observe_order_path(route_paths=route_paths, state_attrs_present=state_attrs, env=env)

    inactive = obs.observe_inactive_publication(
        inactive_instruments=not_enabled, redis_client=sources.redis_client(), indicator_timeframes=indicator_tfs)

    return rs.build_readiness_report(
        now=now, build_identity=sources.build_identity(), registry=registry,
        master_enabled=master, publisher_mode=mode, pilot_scope=pilot, calendar_provenance=calprov,
        core_health=core, db_ok=db_ok, redis_ok=redis_ok,
        stream_count=stream["count"], stream_unknown=stream["unknown"], stream_health=stream["health"],
        configured_stream_count=stream["configured_count"], active_stream_count=stream["active_count"],
        last_stream_heartbeat_utc=stream["last_heartbeat_utc"], last_xau_tick_utc=last_xau_tick_utc,
        consumer_live=(env or {}).get("CONSUMER_LIVE", "false"),
        order_path_present=order["present"], order_path_state=order["state"],
        backfill_execution=(env or {}).get("HERMES_BACKFILL_EXECUTION_ENABLED", "false"),
        seven_new_published_count=sources.seven_new_count(),
        inactive_published_count=inactive["published_count"], inactive_observation_ok=inactive["observed_ok"])


@router.get("/readiness")
async def readiness(sources=Depends(readiness_sources)):
    """External, read-only, scope-aware Advanced-v1 readiness. 200 for GREEN/AMBER, 503 for RED. No secrets, no
    mutation. A bounded deterministic error contract is returned instead of a stack trace on evaluation failure."""
    if sources is None:                        # unwired application -> fail-closed, not a 500 crash
        return JSONResponse(status_code=503, content={
            "identity": {"service": "HERMES", "readiness_contract_version": rs.READINESS_CONTRACT_VERSION},
            "readiness": {"liveness": "OK", "overall": rs.RED, "deployment_authority_compliance": rs.RED,
                          "fault_codes": ["RDY-PROVIDER-UNWIRED"]}})
    try:
        report = assemble(sources)
    except Exception:  # noqa: BLE001 — never leak a traceback; fail-closed to RED
        return JSONResponse(status_code=503, content={
            "identity": {"service": "HERMES", "readiness_contract_version": rs.READINESS_CONTRACT_VERSION},
            "readiness": {"liveness": "OK", "overall": rs.RED, "deployment_authority_compliance": rs.RED,
                          "fault_codes": ["RDY-EVALUATION-ERROR"]}})
    code = 200 if report["readiness"]["overall"] != rs.RED else 503
    return JSONResponse(status_code=code, content=report)
