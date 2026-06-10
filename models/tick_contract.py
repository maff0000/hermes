"""HERMES-owned raw-tick market-truth contract (WO-HELM-HERMES-TICK-CONTRACT-PUBLISH-0001).

Consumer-agnostic. Any authorised consumer (including a future structure_engine
ingest-consumer) may read this contract. NO Falcon/HELIOS/ARES-shaped fields.
UTC-only timestamps. Decimal-safe serialisation.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

# Default/first contract version. Authoritative value is the governed config
# key `tick_contract_version` (hermes_config); this constant documents v1 only.
CONTRACT_VERSION_V1 = "hermes.tick.v1"


def _iso_utc(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class HermesTickContract:
    instrument: str
    source: str
    source_ts_utc: datetime
    received_at_utc: datetime
    bid: Decimal
    ask: Decimal
    seq: int
    contract_version: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source_ts_utc"] = _iso_utc(self.source_ts_utc)
        d["received_at_utc"] = _iso_utc(self.received_at_utc)
        d["bid"] = str(self.bid)
        d["ask"] = str(self.ask)
        d["seq"] = int(self.seq)
        return d

    def to_redis_fields(self, published_at_utc: datetime) -> dict:
        """Flat string fields for XADD, with producer freshness + owner metadata."""
        d = self.to_dict()
        d["published_at_utc"] = _iso_utc(published_at_utc)   # producer freshness
        d["owner"] = "hermes"                                # producer/source metadata
        return {k: str(v) for k, v in d.items()}
