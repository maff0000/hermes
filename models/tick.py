"""
SignalTick - Unified tick model for all broker sources
EPIC-D002: Standalone Signal Service
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import json


class TickSource(Enum):
    """Source of the tick data"""
    OANDA = "oanda"
    IBKR = "ibkr"
    MOCK = "mock"      # Mock data for testing (DELETE after testing)
    REPLAY = "replay"  # For backtesting


@dataclass
class SignalTick:
    """
    Normalized tick data structure.
    All broker adapters convert to this format.
    """
    instrument: str          # OANDA format: XAU_USD
    bid: float
    ask: float
    timestamp: datetime
    source: TickSource

    # Optional fields
    mid: float = field(default=0.0)
    spread: float = field(default=0.0)
    volume: Optional[float] = None

    # Latency tracking
    source_timestamp: Optional[datetime] = None  # When broker sent it
    received_at: Optional[datetime] = None       # When we received it

    def __post_init__(self):
        """Calculate derived fields"""
        if self.mid == 0.0:
            self.mid = (self.bid + self.ask) / 2
        if self.spread == 0.0:
            self.spread = self.ask - self.bid
        if self.received_at is None:
            # UTC_AUDIT_METADATA_OK: tick receipt audit timestamp; not market
            # truth (canonical timestamp is `source_timestamp` from broker).
            # WO-HERMES-UTC-AUDIT-FIX-0001.
            self.received_at = datetime.now(timezone.utc)

    @property
    def latency_ms(self) -> Optional[float]:
        """Latency from source to receipt in milliseconds"""
        if self.source_timestamp and self.received_at:
            # Handle timezone-aware vs naive datetime
            src = self.source_timestamp
            rcv = self.received_at
            if src.tzinfo is not None:
                src = src.replace(tzinfo=None)
            if rcv.tzinfo is not None:
                rcv = rcv.replace(tzinfo=None)
            delta = rcv - src
            return delta.total_seconds() * 1000
        return None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "instrument": self.instrument,
            "bid": self.bid,
            "ask": self.ask,
            "mid": self.mid,
            "spread": self.spread,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source.value,
            "volume": self.volume,
            "latency_ms": self.latency_ms
        }

    def to_json(self) -> str:
        """Serialize to JSON string"""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> 'SignalTick':
        """Create from dictionary"""
        return cls(
            instrument=data["instrument"],
            bid=data["bid"],
            ask=data["ask"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            source=TickSource(data["source"]),
            mid=data.get("mid", 0.0),
            spread=data.get("spread", 0.0),
            volume=data.get("volume")
        )

    def to_redis_hash(self) -> dict:
        """Format for Redis HSET (all strings)"""
        return {
            "instrument": self.instrument,
            "bid": str(self.bid),
            "ask": str(self.ask),
            "mid": str(self.mid),
            "spread": str(self.spread),
            "timestamp": self.timestamp.isoformat(),
            "source": self.source.value,
            # UTC_AUDIT_METADATA_OK: dict serialisation audit timestamp;
            # canonical tick timestamp is `timestamp` field above.
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
