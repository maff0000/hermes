"""WO-HERMES-OANDA-RECONNECT-LOOP-FIX-IMPLEMENTATION-0001 —
HERMES tradingSignals exceptions module.

Importable without triggering main.py module-level side effects (Redis/DB
config loads, adapter instantiation), so test files can scope-import the
exception types in isolation.
"""


class StreamSilentStallError(Exception):
    """Raised by oanda_stream_task when the OANDA stream async iterator
    stalls silently past the governed staleness threshold
    (hermes_tick_staleness_threshold_sec).

    A silent stall means TCP/HTTP connection is held open but no data is
    yielded — asyncio.wait_for raises asyncio.TimeoutError, which the
    stream task translates to this class so the existing
    'except Exception as e:' reconnect block catches it without
    modification.

    Inherits Exception (not BaseException) so:
      - the outer except-Exception in oanda_stream_task catches it
      - it does NOT propagate past asyncio.CancelledError handling
      - existing reconnect machinery (RECOVERING / proof_window /
        is_recovery_exhausted / bounded retry) activates unchanged.
    """
