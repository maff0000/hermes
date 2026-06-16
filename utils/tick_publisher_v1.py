"""HERMES Redis tick publisher v1 — INERT (no-write) build.
WO-HELM-HERMES-REDIS-TICK-PUBLISHER-INERT-BUILD-0001.

Builds intended Redis SET *write plans* for the merged HERMES tick contract
(utils.tick_contract_v1), routed ONLY to an in-memory no-write sink. This module performs NO
Redis/DB/network/filesystem-socket I/O and instantiates NO live Redis client. Live publishing
remains gated: a separate, explicitly-authorised shadow/live WO must build that path.

HERMES owns RAW market-tick truth ONLY. This publisher never emits interpretive consumer truth,
never writes consumer-shaped keys, and never imports consumer or legacy code. The contract `seq`
stays null in v1; a stored-row seq on the input is rejected (never propagated). ALL timestamps UTC.

Write-plan shape (per-instrument hot-path):
    {"operation": "SET", "key": "hermes:ticks:XAU_USD:latest:v1",
     "value": { contract envelope }, "ex_seconds": 10, "write_mode": "INERT_NO_WRITE"}
Aggregate (discovery catalog only):
    {"operation": "SET", "key": "hermes:ticks:latest:v1",
     "value": { aggregate envelope }, "ex_seconds": 10, "write_mode": "INERT_NO_WRITE"}
"""
from __future__ import annotations
from datetime import datetime, timezone

from utils import tick_contract_v1 as tc

# Only inert/no-write mode is permitted in this WO.
WRITE_MODE_INERT = "INERT_NO_WRITE"
WRITE_MODE_LIVE = "LIVE"            # named for the future gated WO; NOT permitted here
OPERATION_SET = "SET"
NAMESPACE = "hermes"
# hosts that may never be treated as a production publish target (fail-loud guard)
_NON_PROD_HOSTS = ("localhost", "127.0.0.1", "::1", "")


# --------------------------------------------------------------------------- config / fail-loud
class InertPublisherConfig:
    """Explicit, no-hidden-defaults publisher config. Only INERT mode constructs here. Every value
    must be passed explicitly; there is NO default host/port/db and NO implicit enable."""

    def __init__(self, *, publisher_enabled, namespace, contract_version,
                 redis_ex_seconds, payload_ttl_seconds, write_mode):
        if publisher_enabled is None or not isinstance(publisher_enabled, bool):
            raise ValueError("GOV-PUB-CFG-001: publisher_enabled must be an explicit bool (fail-loud)")
        if namespace != NAMESPACE:
            raise ValueError(f"GOV-PUB-CFG-002: namespace must be '{NAMESPACE}' (got {namespace!r})")
        if contract_version != tc.CONTRACT_VERSION:
            raise ValueError(f"GOV-PUB-CFG-003: contract_version must be {tc.CONTRACT_VERSION}")
        if redis_ex_seconds != tc.REDIS_EX_SECONDS:
            raise ValueError(f"GOV-PUB-CFG-004: redis_ex_seconds must be {tc.REDIS_EX_SECONDS}")
        if payload_ttl_seconds != tc.TTL_SECONDS:
            raise ValueError(f"GOV-PUB-CFG-005: payload ttl_seconds must be {tc.TTL_SECONDS}")
        if write_mode != WRITE_MODE_INERT:
            raise ValueError("GOV-PUB-CFG-006: only INERT_NO_WRITE permitted in this WO "
                             "(live publish is a separate authorised WO)")
        self.publisher_enabled = publisher_enabled
        self.namespace = namespace
        self.contract_version = contract_version
        self.redis_ex_seconds = redis_ex_seconds
        self.payload_ttl_seconds = payload_ttl_seconds
        self.write_mode = write_mode


def validate_live_readiness(*, live_target, live_authorised, treat_as_production):
    """Design/test-level proof that a FUTURE live publisher cannot run on hidden defaults.
    Raises unless an explicit non-default, non-localhost-as-production target is authorised.
    This WO never calls it for a real write; it exists to lock the gate for the next WO."""
    if not live_authorised:
        raise ValueError("GOV-PUB-LIVE-001: live mode must be explicitly authorised (fail-loud)")
    if not isinstance(live_target, dict):
        raise ValueError("GOV-PUB-LIVE-002: live_target must be explicitly configured — "
                         "no default host/port/db fallback")
    for field in ("host", "port", "db"):
        if live_target.get(field) in (None, ""):
            raise ValueError(f"GOV-PUB-LIVE-002: live_target.{field} must be explicit "
                             "(no default host/port/db fallback)")
    if treat_as_production and str(live_target.get("host")).lower() in _NON_PROD_HOSTS:
        raise ValueError("GOV-PUB-LIVE-003: localhost/loopback may not be a production publish target")
    return True


# --------------------------------------------------------------------------- no-write sink
class NoWriteTickPublishSink:
    """Captures intended write plans in memory for tests/evidence. Connects to NOTHING — no Redis,
    no DB, no network, no filesystem runtime socket, no live service."""

    def __init__(self):
        self.captured = []

    def publish(self, plan):
        if not isinstance(plan, dict) or plan.get("write_mode") != WRITE_MODE_INERT:
            raise ValueError("GOV-PUB-SINK-001: no-write sink accepts INERT_NO_WRITE plans only")
        if plan.get("operation") != OPERATION_SET:
            raise ValueError("GOV-PUB-SINK-002: only SET plans supported")
        self.captured.append(plan)
        return plan

    def keys(self):
        return [p["key"] for p in self.captured]


# Alias names requested by the WO; both are the same inert in-memory capturing sink.
CapturingTickPublishSink = NoWriteTickPublishSink
InMemoryTickPublishSink = NoWriteTickPublishSink


def build_publish_sink(mode):
    """Factory. Only the inert no-write sink is constructible in this WO. A live writer/sink is
    NOT available here — requesting one fails loud (no live Redis client is instantiated)."""
    if mode == "inert":
        return NoWriteTickPublishSink()
    raise ValueError("GOV-PUB-SINK-003: live publish sink is not constructible in this WO "
                     "(inert no-write only)")


# --------------------------------------------------------------------------- write-plan builders
def _plan(key, value, config):
    return {"operation": OPERATION_SET, "key": key, "value": value,
            "ex_seconds": config.redis_ex_seconds, "write_mode": config.write_mode}


def _coerce_utc(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        s = value[:-1] if value.endswith("Z") else value
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    raise ValueError("GOV-PUB-TICK-002: source_received_at_utc must be UTC datetime or ISO string")


def build_tick_write_plan(raw_tick, *, generated_at_utc, instrument_registry, config):
    """Build the per-instrument INERT write plan from a raw tick dict.

    raw_tick: {instrument, bid, ask, source?, source_received_at_utc, source_warning?}
    - unsupported instrument → fail loud unless the registry marks it active
    - a stored-row `seq` on the input is REJECTED (never propagated into the contract)
    - absent source tick → UNAVAILABLE envelope (contract semantics)
    - malformed price → fail loud (contract GOV-TICK-CONTRACT-003)
    """
    if config.write_mode != WRITE_MODE_INERT:
        raise ValueError("GOV-PUB-TICK-006: refusing non-inert write_mode")
    if not isinstance(raw_tick, dict):
        raise ValueError("GOV-PUB-TICK-001: raw_tick must be a dict (fail-loud)")
    instrument = raw_tick.get("instrument")
    if instrument not in set(instrument_registry):
        raise ValueError(f"GOV-PUB-TICK-003: instrument {instrument!r} not active in registry (fail-loud)")
    if "seq" in raw_tick:
        raise ValueError("GOV-PUB-TICK-004: stored-row seq must not be propagated into the contract "
                         "(seq stays null; not a completeness proof)")
    bid, ask = raw_tick.get("bid"), raw_tick.get("ask")
    received = raw_tick.get("source_received_at_utc")
    if bid is None and ask is None and received is None:
        envelope = tc.build_unavailable(instrument=instrument, generated_at_utc=generated_at_utc,
                                        reason_codes=["NO_VALID_SOURCE_TICK"])
    else:
        envelope = tc.build_tick_contract(
            instrument=instrument, source_received_at_utc=_coerce_utc(received),
            generated_at_utc=generated_at_utc, bid=bid, ask=ask,
            source=raw_tick.get("source", "oanda"),
            source_warning=bool(raw_tick.get("source_warning", False)))
    tc.validate_tick_contract(envelope)
    return _plan(tc.canonical_key(instrument), envelope, config)


def build_aggregate_write_plan(instruments, *, generated_at_utc, config):
    """Build the aggregate discovery (catalog) INERT write plan. Discovery only — no price."""
    if config.write_mode != WRITE_MODE_INERT:
        raise ValueError("GOV-PUB-TICK-006: refusing non-inert write_mode")
    envelope = tc.build_aggregate_discovery(instruments=instruments, generated_at_utc=generated_at_utc)
    tc.validate_tick_contract(envelope)
    return _plan(tc.aggregate_key(), envelope, config)
