"""
HERMES Collection Agent Contract — Source Adapter Interface
WO-HERMES-COLLECTION-AGENT-B
Epic: EPIC-HERMES-SIGNAL-SOURCE-RESILIENCE-001

Defines the interface that any source adapter must implement to submit
candidate M1 candles to the Canonical M1 Engine (WO-C).

Design rules:
- Agents submit candidates PER INSTRUMENT, independently
- No batch/grouped submission that ties instrument fates together
- Agent health is reported PER INSTRUMENT, not as monolith
- Contract explicitly forbids cross-instrument success dependency
- Engine handles dedupe — agents do NOT need to dedupe
- Agents produce candidates; engine decides acceptance

Fault codes (defined in WO-A, used here for typing):
- HERMES_CANONICAL_M1_REJECTED_DUPLICATE
- HERMES_CANONICAL_M1_REJECTED_LATE
- HERMES_CANONICAL_M1_REJECTED_INVALID
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import AsyncIterator, Dict, List, Optional


# ============================================================
# Data contract — CandidateM1
# ============================================================

@dataclass
class CandidateM1:
    """
    A candidate M1 candle submitted by a collection agent.

    This is NOT canonical truth. It is a proposal. The Canonical M1
    Engine (WO-C) decides whether to accept or reject it.

    Each candidate is for ONE instrument and ONE minute bucket.
    No multi-instrument batching at the data contract level.
    """
    instrument: str                     # e.g. "XAU_USD"
    minute_bucket_utc: datetime         # Truncated to :00 UTC
    open: float
    high: float
    low: float
    close: float
    volume: int                         # Tick count or broker volume
    source_id: str                      # Stable source identifier (e.g. "oanda_stream")
    source_timestamp_utc: Optional[datetime] = None  # Original source timestamp if available
    # arrival_utc is set by the engine, not the agent

    def is_valid(self) -> bool:
        """Minimal validity check. Engine does full validation."""
        return (
            bool(self.instrument)
            and self.minute_bucket_utc is not None
            and self.minute_bucket_utc.second == 0
            and self.minute_bucket_utc.microsecond == 0
            and self.open > 0
            and self.high > 0
            and self.low > 0
            and self.close > 0
            and self.high >= self.low
            and self.high >= self.open
            and self.high >= self.close
            and self.low <= self.open
            and self.low <= self.close
            and bool(self.source_id)
        )

    def to_dict(self) -> dict:
        return {
            'instrument': self.instrument,
            'minute_bucket_utc': self.minute_bucket_utc.isoformat(),
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'close': self.close,
            'volume': self.volume,
            'source_id': self.source_id,
            'source_timestamp_utc': self.source_timestamp_utc.isoformat() if self.source_timestamp_utc else None,
        }


# ============================================================
# Accept/reject result from engine
# ============================================================

class AcceptStatus(Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED_DUPLICATE = "REJECTED_DUPLICATE"
    REJECTED_LATE = "REJECTED_LATE"
    REJECTED_INVALID = "REJECTED_INVALID"


@dataclass
class AcceptResult:
    """Result from the Canonical M1 Engine after candidate submission."""
    status: AcceptStatus
    instrument: str
    minute_bucket_utc: datetime
    source_id: str
    fault_code: Optional[str] = None
    detail: Optional[str] = None


# ============================================================
# Source health — per instrument
# ============================================================

class SourceState(Enum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


@dataclass
class InstrumentSourceHealth:
    """Health status for a SINGLE instrument from a SINGLE source.
    Per-instrument. No monolith health."""
    instrument: str
    source_id: str
    state: SourceState
    last_candidate_utc: Optional[datetime] = None
    candidates_submitted: int = 0
    candidates_accepted: int = 0
    candidates_rejected: int = 0
    last_error: Optional[str] = None


# ============================================================
# Collection agent interface
# ============================================================

class CollectionAgent(ABC):
    """
    Abstract interface for M1 candidate sources.

    ISOLATION RULES (non-negotiable):
    - submit_candidate() operates on ONE instrument only
    - No transaction, lock, or queue may group instruments
    - One instrument's failure must NOT block another
    - health() returns per-instrument status
    - stream_candidates() yields per-instrument candidates independently

    Agents produce candidates. The engine decides acceptance.
    Agents do NOT dedupe — the engine handles that.
    """

    @abstractmethod
    async def connect(self) -> bool:
        """
        Establish connection to the source.
        Returns True if the source is reachable.
        This does NOT mean all instruments are healthy.
        """
        pass

    @abstractmethod
    async def disconnect(self):
        """Close connection to the source."""
        pass

    @abstractmethod
    async def stream_candidates(self) -> AsyncIterator[CandidateM1]:
        """
        Stream candidate M1 candles as they complete.

        Yields one CandidateM1 at a time, per instrument, as each
        minute bucket closes. No batching across instruments.

        The agent is responsible for:
        - Aggregating raw ticks/data into M1 OHLCV
        - Truncating timestamp to minute boundary
        - Setting source_id correctly
        - Yielding complete candles only

        The agent is NOT responsible for:
        - Deduplication (engine handles)
        - Acceptance window checks (engine handles)
        - Provenance persistence (engine handles)
        """
        pass

    @abstractmethod
    async def fetch_m1_range(
        self,
        instrument: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> List[CandidateM1]:
        """
        Fetch historical M1 candles for recovery/backfill.

        Returns a list of CandidateM1 for the given window.
        These will be submitted to the engine with ingest_mode=REPAIR or BACKFILL.

        Per-instrument. One call = one instrument.
        """
        pass

    @abstractmethod
    def health(self) -> Dict[str, InstrumentSourceHealth]:
        """
        Return health status PER INSTRUMENT.

        Returns: {instrument: InstrumentSourceHealth}

        Must NOT return a single monolith status.
        Each instrument's health is independent.
        """
        pass

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Stable identifier for this source (e.g. 'oanda_stream')."""
        pass

    @property
    @abstractmethod
    def supported_instruments(self) -> List[str]:
        """List of instruments this agent can provide data for."""
        pass


# ============================================================
# Source policy reader
# ============================================================

class SourcePolicyReader:
    """Reads per-instrument source policy from hermes_source_policy table."""

    def __init__(self, db_config: dict):
        self._db_config = db_config
        self._cache = None
        self._cache_time = None
        self._cache_ttl = 300  # 5 min

    def _get_conn(self):
        import pymysql
        return pymysql.connect(**self._db_config, autocommit=True, connect_timeout=5)

    def get_policy(self, instrument: str) -> Optional[dict]:
        """Get source policy for a specific instrument. No inheritance."""
        policies = self._load_all()
        return policies.get(instrument)

    def get_all_policies(self) -> Dict[str, dict]:
        """Get all instrument policies."""
        return self._load_all()

    def _load_all(self) -> Dict[str, dict]:
        """Load and cache all policies."""
        import time
        now = time.monotonic()
        if self._cache and self._cache_time and (now - self._cache_time) < self._cache_ttl:
            return self._cache

        import pymysql.cursors
        conn = self._get_conn()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT instrument, primary_source, fallback_sources_json, "
                "acceptance_window_sec, is_enabled FROM hermes_source_policy"
            )
            rows = cur.fetchall()
        conn.close()

        self._cache = {r['instrument']: r for r in rows}
        self._cache_time = now
        return self._cache
