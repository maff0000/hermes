"""HERMES tick Redis-Stream publisher (WO-HELM-HERMES-TICK-CONTRACT-PUBLISH-0001).

Publishes the consumer-agnostic tick contract to:
  - Redis Stream  hermes:ticks:<instrument>        (XADD, approx MAXLEN trim)
  - latest key    hermes:ticks:latest:<instrument> (SET, TTL for staleness)
Each entry carries published_at_utc (freshness), contract_version, owner=hermes.

Gated by governed config tick_stream_enabled (fail-loud if missing). NOT run
live by this WO. redis_client + config_get + now_utc are injected for testability.
"""
from __future__ import annotations
import json
from typing import Callable

STREAM_KEY_FMT = "hermes:ticks:{instrument}"
LATEST_KEY_FMT = "hermes:ticks:latest:{instrument}"


class TickStreamPublisher:
    def __init__(self, redis_client, config_get: Callable[[str, str], object], now_utc: Callable):
        self._r = redis_client
        self._cfg = config_get          # config_get(key, value_type) -> value (fail-loud)
        self._now = now_utc

    def enabled(self) -> bool:
        return bool(self._cfg("tick_stream_enabled", "bool"))

    def stream_key(self, instrument: str) -> str:
        return STREAM_KEY_FMT.format(instrument=instrument)

    def latest_key(self, instrument: str) -> str:
        return LATEST_KEY_FMT.format(instrument=instrument)

    def publish(self, contract) -> bool:
        """Publish one contract. Returns False (no-op) when stream disabled."""
        if not self.enabled():
            return False
        maxlen = int(self._cfg("tick_stream_maxlen", "int"))
        ttl = int(self._cfg("tick_latest_ttl_seconds", "int"))
        fields = contract.to_redis_fields(self._now())
        skey = self.stream_key(contract.instrument)
        lkey = self.latest_key(contract.instrument)
        # approximate trim keeps XADD O(1); SQL ticks table remains durable record
        self._r.xadd(skey, fields, maxlen=maxlen, approximate=True)
        self._r.set(lkey, json.dumps(fields), ex=ttl)
        return True
