"""HERMES runtime candle-forward emit seam — INERT / disabled-by-default.

WO-HELM-HERMES-CANDLE-FORWARD-RUNTIME-WIRE-INERT-0001.

Makes the HERMES runtime structurally aware of the governed candle-forward producer/publisher foundation
(utils/candle_contract_v1, utils/candle_publisher_v1) WITHOUT writing anything. Default DISABLED ->
DisabledCandleEmitter (no sink, no Redis/SQL write, no warning spam, normal tick path unaffected).

Master gate: HERMES_CANDLE_FORWARD_ENABLED (default false).
When enabled, an explicit forward sink mode is REQUIRED (HERMES_CANDLE_FORWARD_SINK) with NO hidden default:
  - 'none' / 'inert'  -> governed NoWriteCandleSink (no-write seam; this WO)
  - anything else (e.g. shadow/canonical/live) -> FAIL LOUD (no write path is permitted in this WO).
No Proteus fallback, no canonical writer, no stale-SQL fallback, no shadow runtime write. UTC only.
HERMES owns market truth and stays standalone (no legacy-monolith import, no shared code).
"""
from utils import candle_publisher_v1 as cp

FAULT_NO_SINK = "GOV-CANDLE-FWD-SEAM-001"          # enabled without explicit sink mode
FAULT_WRITE_FORBIDDEN = "GOV-CANDLE-FWD-SEAM-002"  # a write sink requested in an inert WO
ALLOWED_INERT_SINKS = ("none", "inert")            # this WO: no-write only


def _disabled_config():
    # All publish/shadow flags explicitly False; no endpoints required (nothing can write).
    return cp.CandlePublisherConfig(
        publish_enabled=False, publish_authorised=False,
        shadow_publish_enabled=False, shadow_authorised=False,
        namespace="hermes", contract_version="v1")


class InertCandleForwardSeam:
    """Structurally-wired but inert. Holds a governed no-write sink; emit() never writes."""

    def __init__(self, sink):
        self.sink = sink
        self.enabled = False  # inert: never writes in this WO
        self.write_mode = cp.WRITE_MODE_INERT

    def emit(self, envelope=None, **_):
        # No-op: builds nothing live, writes nothing. Returns an explicit inert marker.
        return {"emitted": False, "wrote": False, "reason": "CANDLE_FORWARD_INERT"}

    def status(self):
        return {"enabled": False, "write_mode": self.write_mode, "sink": type(self.sink).__name__}


def build_candle_forward_seam_from_env():
    """Boot factory for the runtime candle-forward seam. Default DISABLED no-op; fail-loud when enabled
    without an explicit (no-write) sink mode. NEVER returns a writing emitter in this WO."""
    from env_config import get_env, get_env_bool  # lazy; HERMES-owned config only
    if not get_env_bool("HERMES_CANDLE_FORWARD_ENABLED", False):
        return cp.DisabledCandleEmitter(_disabled_config())
    # Enabled: explicit sink mode REQUIRED (no hidden default -> fail loud if missing).
    sink_mode = get_env("HERMES_CANDLE_FORWARD_SINK", required=True)
    sink_mode = (sink_mode or "").strip().lower()
    if sink_mode not in ALLOWED_INERT_SINKS:
        raise ValueError(
            f"{FAULT_WRITE_FORBIDDEN}: candle-forward sink '{sink_mode}' is forbidden in the inert wire WO "
            f"(allowed: {ALLOWED_INERT_SINKS}). No shadow/canonical/live write; no Proteus or stale-SQL fallback.")
    # inert no-write sink (the governed NoWriteCandleSink never holds a live client)
    return InertCandleForwardSeam(cp.NoWriteCandleSink())
