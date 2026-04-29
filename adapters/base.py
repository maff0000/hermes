"""
Base adapter interface for broker connections
EPIC-D002: Standalone Signal Service
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import AsyncIterator, Callable, List, Optional
import asyncio
import logging

from models.tick import SignalTick, TickSource


class AdapterState(Enum):
    """Connection state of an adapter"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


@dataclass
class AdapterHealth:
    """Health status of an adapter"""
    state: AdapterState
    last_tick_at: Optional[datetime] = None
    tick_count: int = 0
    error_count: int = 0
    last_error: Optional[str] = None
    avg_latency_ms: Optional[float] = None

    @property
    def is_healthy(self) -> bool:
        """Adapter is healthy if connected and receiving ticks"""
        if self.state != AdapterState.CONNECTED:
            return False
        if self.last_tick_at is None:
            return False
        # Stale if no tick in 30 seconds
        age = (datetime.now(timezone.utc) - self.last_tick_at).total_seconds()
        return age < 30

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "is_healthy": self.is_healthy,
            "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
            "tick_count": self.tick_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "avg_latency_ms": self.avg_latency_ms
        }


class BaseAdapter(ABC):
    """
    Abstract base class for broker streaming adapters.
    Implementations: OANDAAdapter, IBKRAdapter
    """

    def __init__(self, source: TickSource, instruments: List[str]):
        self.source = source
        self.instruments = instruments
        self.logger = logging.getLogger(f"adapter.{source.value}")

        # Health tracking
        self._health = AdapterHealth(state=AdapterState.DISCONNECTED)
        self._latency_samples: List[float] = []

        # Callbacks
        self._on_tick: Optional[Callable[[SignalTick], None]] = None
        self._on_state_change: Optional[Callable[[AdapterState], None]] = None

    @property
    def health(self) -> AdapterHealth:
        """Current health status"""
        # Update average latency
        if self._latency_samples:
            self._health.avg_latency_ms = sum(self._latency_samples) / len(self._latency_samples)
        return self._health

    def set_tick_callback(self, callback: Callable[[SignalTick], None]):
        """Set callback for received ticks"""
        self._on_tick = callback

    def set_state_callback(self, callback: Callable[[AdapterState], None]):
        """Set callback for state changes"""
        self._on_state_change = callback

    def _set_state(self, state: AdapterState):
        """Update state and notify"""
        old_state = self._health.state
        self._health.state = state
        if old_state != state:
            self.logger.info(f"State: {old_state.value} -> {state.value}")
            if self._on_state_change:
                self._on_state_change(state)

    def _record_tick(self, tick: SignalTick):
        """Record tick for health tracking and dispatch"""
        self._health.last_tick_at = datetime.now(timezone.utc)
        self._health.tick_count += 1

        # Track latency (keep last 100 samples)
        if tick.latency_ms is not None:
            self._latency_samples.append(tick.latency_ms)
            if len(self._latency_samples) > 100:
                self._latency_samples.pop(0)

        # Dispatch to callback
        if self._on_tick:
            self._on_tick(tick)

    def _record_error(self, error: str):
        """Record error for health tracking"""
        self._health.error_count += 1
        self._health.last_error = error
        self.logger.error(f"Adapter error: {error}")

    @abstractmethod
    async def connect(self) -> bool:
        """
        Establish connection to broker.
        Returns True if successful.
        """
        pass

    @abstractmethod
    async def disconnect(self):
        """Disconnect from broker"""
        pass

    @abstractmethod
    async def stream(self) -> AsyncIterator[SignalTick]:
        """
        Stream ticks from broker.
        Yields SignalTick objects.
        Should handle reconnection internally.
        """
        pass

    @abstractmethod
    async def get_current_price(self, instrument: str) -> Optional[SignalTick]:
        """
        Get current price for instrument (one-shot).
        Used for initialization or fallback.
        """
        pass

    async def run(self):
        """
        Main run loop - connect and stream with auto-reconnect.
        """
        backoff = 1
        max_backoff = 60

        while True:
            try:
                self._set_state(AdapterState.CONNECTING)

                if await self.connect():
                    self._set_state(AdapterState.CONNECTED)
                    backoff = 1  # Reset on successful connect

                    async for tick in self.stream():
                        self._record_tick(tick)
                else:
                    self._record_error("Connection failed")

            except asyncio.CancelledError:
                self.logger.info("Adapter cancelled, shutting down")
                await self.disconnect()
                break

            except Exception as e:
                self._record_error(str(e))
                self._set_state(AdapterState.RECONNECTING)

            # Backoff before reconnect
            self.logger.info(f"Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)
