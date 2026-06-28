"""HERMES governed H4 canonical publish wiring (derived from H1).
WO-HELM-HERMES-GOLD-H4-CANONICAL-PUBLISH-WIRE-0001.

Observes completed H1 candles, rolls NY-5PM-aligned (fixed 22:00 UTC) H4 buckets, and on bucket roll-over
derives the sealed bucket via `candle_h4_derivation_v1.derive_h4` and routes the governed H4 envelope
through the existing canonical writer (`SerializingCandleCanonicalWriter`). The writer's
`assert_canonical_key` (publish grid now includes H4) + `validate_candle_contract` are the governed gate.

DERIVED FROM H1 ONLY — no direct `candles_H4`, no Proteus, no M15 fallback, no synthesis. XAU_USD only.
Completeness is honest: a sealed 4/4 bucket publishes status OK; a sealed <4 bucket publishes
SOURCE_INCOMPLETE (never OK) with honest source_coverage/gap_state. Gated by
`HERMES_CANDLE_H4_PUBLISH_ENABLED` (default false) ON TOP OF the canonical publish controls — so deploying
this code does NOT auto-activate H4. No Redis I/O at import; disabled -> no-op.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from utils import candle_publisher_v1 as cp
from utils import candle_h4_derivation_v1 as h4d
from utils import candle_runtime_seam_v1 as seam   # canonical_instrument + canonical config/allowlist/client

H4_PUBLISH_ENABLED_ENV = "HERMES_CANDLE_H4_PUBLISH_ENABLED"


def _tf_name(candle):
    tf = getattr(candle, "timeframe", None)
    return tf.name if hasattr(tf, "name") else str(tf)


class CanonicalH4Producer:
    """Live derived-H4 producer. Feed it completed H1 candles via `on_h1_close`; it seals a bucket when
    the next bucket's first H1 arrives (so the published H4 is always the most recently CLOSED bucket)."""

    def __init__(self, writer, allowed_instruments, history_forward_writer=None):
        if not isinstance(writer, cp.SerializingCandleCanonicalWriter):
            raise ValueError("GOV-CANDLE-H4-WIRE-001: CanonicalH4Producer requires a SerializingCandleCanonicalWriter")
        allowed = frozenset(allowed_instruments or ())
        if not allowed:
            raise ValueError("GOV-CANDLE-H4-WIRE-002: non-empty instrument allowlist required (fail-closed)")
        self.writer = writer
        self.allowed_instruments = allowed
        # Optional governed forward-history writer. None (default) -> H4 latest-only. When attached + enabled,
        # ONLY a COMPLETE 4/4 sealed bucket (status OK) is appended to history; warmup/SOURCE_INCOMPLETE never
        # enters history. A history fault is surfaced and never breaks the H4 latest publish.
        self.history_forward_writer = history_forward_writer
        self.enabled = True
        self.metrics = {"h4_published_ok": 0, "h4_published_incomplete": 0, "h4_skipped_not_allowlisted": 0,
                        "h4_skipped_non_h1": 0, "h4_emit_fail": 0, "h4_buckets_sealed": 0,
                        "h4_history_forward_written": 0, "h4_history_forward_skipped": 0,
                        "h4_history_forward_fail": 0}
        self._buf = {}        # instrument -> {bucket_open_epoch: [h1_candle, ...]}
        self._current = {}    # instrument -> current (open) bucket_open_epoch

    def on_h1_close(self, h1_candle, **_):
        """Process a completed H1 candle. Returns a dict describing whether a prior bucket was published."""
        if h1_candle is None:
            return {"published": False, "reason": "NO_CANDLE"}
        if _tf_name(h1_candle) != h4d.H1_TIMEFRAME:        # only H1 drives H4
            self.metrics["h4_skipped_non_h1"] += 1
            return {"published": False, "reason": "NOT_H1", "timeframe": _tf_name(h1_candle)}
        inst = seam.canonical_instrument(getattr(h1_candle, "instrument", None))
        if inst not in self.allowed_instruments:
            self.metrics["h4_skipped_not_allowlisted"] += 1
            return {"published": False, "reason": "INSTRUMENT_NOT_ALLOWLISTED", "instrument": inst}
        bo_ep = int(h4d.h4_bucket_open(h1_candle.timestamp).timestamp())
        prev = self._current.get(inst)
        result = {"published": False, "reason": "BUFFERED", "bucket_open_epoch": bo_ep}
        if prev is not None and prev != bo_ep:
            result = self._seal_and_publish(inst, prev)     # a new bucket started -> seal+publish the previous
        self._buf.setdefault(inst, {}).setdefault(bo_ep, []).append(h1_candle)
        self._current[inst] = bo_ep
        return result

    def _seal_and_publish(self, instrument, bucket_epoch):
        children = self._buf.get(instrument, {}).pop(bucket_epoch, [])
        self.metrics["h4_buckets_sealed"] += 1
        bo = datetime.fromtimestamp(bucket_epoch, tz=timezone.utc)
        try:
            env, meta = h4d.derive_h4(
                instrument=instrument, h4_open=bo, h1_children=children,
                generated_at_utc=bo + timedelta(seconds=h4d.H4_SECONDS), is_closed=True)
            res = self.writer.publish(env)                  # assert_canonical_key (H4 grid) + validate + SET EX
        except Exception as exc:  # noqa: BLE001 - surfaced (counter + return), never silently swallowed
            self.metrics["h4_emit_fail"] += 1
            return {"published": False, "reason": "H4_EMIT_FAIL", "error": repr(exc),
                    "bucket_open_epoch": bucket_epoch}
        d = env["data"]
        history = {"attempted": False, "reason": "H4_INCOMPLETE_NOT_HISTORY"}
        if env["status"] == "OK":
            self.metrics["h4_published_ok"] += 1
            history = self._forward_history(env)            # ONLY complete 4/4 enters history
        else:
            self.metrics["h4_published_incomplete"] += 1    # honest SOURCE_INCOMPLETE (never OK)
        return {"published": True, "key": res["key"], "status": env["status"],
                "source_count": d["source_count"], "source_coverage": d["source_coverage"],
                "gap_state": d["gap_state"], "bucket_open_epoch": bucket_epoch, "history_forward": history}

    def _forward_history(self, env):
        """Append a sealed COMPLETE H4 bucket to governed history. No-op unless an enabled writer is attached.
        The writer re-enforces closed+status-OK+target guards; warmup/incomplete is skipped there too. A fault
        is counted and never breaks the H4 latest publish (already done)."""
        hw = self.history_forward_writer
        if hw is None or not getattr(hw, "enabled", False):
            return {"attempted": False, "reason": "HISTORY_FORWARD_DISABLED"}
        try:
            res = hw.on_h4_sealed(env, inserted_at_utc=datetime.now(timezone.utc))
        except Exception as exc:  # noqa: BLE001 - surfaced via counter, never breaks latest
            self.metrics["h4_history_forward_fail"] += 1
            return {"attempted": True, "wrote": False, "reason": "H4_HISTORY_FORWARD_FAIL", "error": repr(exc)}
        if res.get("wrote"):
            self.metrics["h4_history_forward_written"] += 1
        else:
            self.metrics["h4_history_forward_skipped"] += 1
        return {"attempted": True, **res}

    def status(self):
        hw = self.history_forward_writer
        return {"enabled": True,
                "history_forward_enabled": bool(hw is not None and getattr(hw, "enabled", False)),
                **self.metrics}


class DisabledH4Producer:
    """No-op H4 producer (default). Writes nothing; constructs no Redis client."""
    enabled = False

    def on_h1_close(self, *a, **k):
        return {"published": False, "reason": "H4_PUBLISH_DISABLED"}

    def status(self):
        return {"enabled": False}


def build_h4_producer_from_env():
    """Boot factory. Returns DisabledH4Producer (no-op) UNLESS canonical forwarding is on (SINK=canonical)
    AND HERMES_CANDLE_H4_PUBLISH_ENABLED=true. Gated by the SAME canonical controls (publish enabled+
    authorised, XAU_USD allowlist, explicit bus target). Missing governed config when H4 is enabled FAILS
    LOUD (no hidden defaults). Deploying this code with canonical live but H4 flag unset keeps H4 dark."""
    from env_config import get_env, get_env_bool, get_env_int   # lazy; HERMES-owned config only
    if not get_env_bool("HERMES_CANDLE_FORWARD_ENABLED", False):
        return DisabledH4Producer()
    if (get_env("HERMES_CANDLE_FORWARD_SINK", default="") or "").strip().lower() != seam.CANONICAL_SINK:
        return DisabledH4Producer()
    if not get_env_bool(H4_PUBLISH_ENABLED_ENV, False):
        return DisabledH4Producer()
    config = seam._canonical_config_from_env(get_env, get_env_bool, get_env_int)
    config.assert_canonical_allowed()                          # publish_enabled AND authorised (fail-loud)
    allowed = seam.parse_canonical_allowlist(get_env(seam.CANONICAL_ALLOWLIST_ENV, required=True))  # fail-closed
    client = seam._real_canonical_redis_client(config)
    writer = cp.SerializingCandleCanonicalWriter(config=config, redis_client=client)
    # Governed forward-history writer: default DISABLED (own gates), so attaching it changes nothing unless
    # HERMES_CANDLE_HISTORY_FORWARD_ENABLED + AUTHORISED + allowlists are set. Lazy import avoids a cycle.
    from utils import candle_history_forward_writer_v1 as hfw   # lazy; only on the H4 canonical path
    history_writer = hfw.build_history_forward_writer_from_env()
    return CanonicalH4Producer(writer, allowed_instruments=allowed, history_forward_writer=history_writer)
