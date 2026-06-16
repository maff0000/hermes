"""HERMES Redis tick publisher v1 — SHADOW activation (JSON serialisation hard gate + real writer).
WO-HELM-HERMES-REDIS-TICK-PUBLISHER-SHADOW-ACTIVATE-0001.

This is the controlled point where bytes may leave the process to Redis — but ONLY to authorised
hermes:shadow:* keys, and ONLY as an explicitly JSON-serialised value. A real Redis client is NEVER
handed a Python dict. Canonical live keys (hermes:ticks:*) can never be written here.

Hard gate (binding from PR #24 close-out):
  * the envelope is serialised with json.dumps BEFORE any client.set
  * the value passed to the client is str/bytes, never a dict
  * serialise -> deserialise round-trips and the result validates against tick_contract_v1

Reuses the merged shadow plan builders (utils.tick_shadow_publisher_v1) for key transform + payload
validation. ALL timestamps UTC. HERMES owns raw market truth only.
"""
from __future__ import annotations
import json
from datetime import datetime

from utils import tick_contract_v1 as tc
from utils import tick_shadow_publisher_v1 as sh


def serialize_envelope(envelope) -> str:
    """Explicit JSON serialisation of a tick-contract envelope. Fail loud if not a dict. UTC ms
    timestamps are already strings, so they survive serialisation unchanged."""
    if not isinstance(envelope, dict):
        raise ValueError("GOV-PUB-SHADOW-SER-001: only a dict envelope may be serialised")
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))


def deserialize_envelope(blob) -> dict:
    """Parse a JSON str/bytes back to a dict and re-validate it against the contract. Fail loud."""
    if isinstance(blob, bytes):
        blob = blob.decode("utf-8")
    if not isinstance(blob, str):
        raise ValueError("GOV-PUB-SHADOW-SER-002: Redis value must be str/bytes JSON (never a dict)")
    envelope = json.loads(blob)
    tc.validate_tick_contract(envelope)
    return envelope


class SerializingShadowWriter:
    """The REAL-client shadow write path. Builds a validated shadow plan, JSON-serialises the
    envelope, asserts the value is str/bytes (never a dict), then SET EX 10 to an authorised shadow
    key on an INJECTED real Redis client. This supersedes ShadowTickPublisher (dict-to-client) for
    any real client — a real client must only ever be wired here."""

    def __init__(self, *, config, redis_client):
        if not isinstance(config, sh.ShadowPublisherConfig):
            raise ValueError("GOV-PUB-SHADOW-ACT-001: ShadowPublisherConfig required")
        if redis_client is None:
            raise ValueError("GOV-PUB-SHADOW-ACT-002: redis_client must be supplied explicitly")
        self.config = config
        self.redis_client = redis_client

    def _write(self, plan):
        sh.assert_shadow_key(plan["key"])                       # refuse any live key
        if plan["write_mode"] != sh.WRITE_MODE_SHADOW:
            raise ValueError("GOV-PUB-SHADOW-ACT-003: only SHADOW_NO_LIVE plans may be written")
        json_value = serialize_envelope(plan["value"])          # dict -> JSON str
        if not isinstance(json_value, (str, bytes)):            # the hard gate
            raise ValueError("GOV-PUB-SHADOW-ACT-004: Redis value must be JSON str/bytes, never a dict")
        redis_result = self.redis_client.set(plan["key"], json_value, ex=plan["ex_seconds"])
        v = plan["value"]
        return {"key": plan["key"], "ex_seconds": plan["ex_seconds"], "write_mode": plan["write_mode"],
                "serialised": True, "value_type": type(json_value).__name__,
                "payload_validated": True, "redis_result": redis_result,
                "generated_at_utc": v["generated_at_utc"], "valid_until_utc": v["valid_until_utc"],
                "freshness_state": v["freshness_state"], "status": v["status"],
                "reason_codes": v["reason_codes"]}

    def publish_tick(self, raw_tick, *, generated_at_utc: datetime, instrument_registry):
        plan = sh.build_shadow_plan(raw_tick, generated_at_utc=generated_at_utc,
                                    instrument_registry=instrument_registry, config=self.config)
        return self._write(plan)

    def publish_aggregate(self, instruments, *, generated_at_utc: datetime):
        plan = sh.build_aggregate_shadow_plan(instruments, generated_at_utc=generated_at_utc,
                                              config=self.config)
        return self._write(plan)
