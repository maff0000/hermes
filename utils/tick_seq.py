"""HERMES-owned per-instrument monotonic tick sequence generator (D-SEQ).

HERMES is the single writer of tradingSignals.ticks post-cutover, so a
process-local per-instrument counter seeded from the current DB max(seq) is
both monotonic and safe under live write concurrency (one writer process).
A threading.Lock guards concurrent emit within the process. Deterministic for
backfill: assign seq by ORDER BY (source_ts_utc/timestamp, id) per instrument.

This module does NOT connect to anything by itself — the seed function is
injected, keeping it pure and unit-testable.
"""
from __future__ import annotations
import threading
from typing import Callable, Dict


class TickSeqGenerator:
    def __init__(self, seed_fn: Callable[[str], int]):
        """seed_fn(instrument) -> current max seq for that instrument (0 if none)."""
        self._seed_fn = seed_fn
        self._next: Dict[str, int] = {}
        self._lock = threading.Lock()

    def next(self, instrument: str) -> int:
        with self._lock:
            if instrument not in self._next:
                seed = int(self._seed_fn(instrument) or 0)
                if seed < 0:
                    raise ValueError(f"GOV-SEQ-001: negative seed seq for {instrument!r} (fail-loud)")
                self._next[instrument] = seed + 1
            value = self._next[instrument]
            self._next[instrument] = value + 1
            return value

    def peek(self, instrument: str) -> int:
        with self._lock:
            return self._next.get(instrument, None)
