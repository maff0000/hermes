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
from utils import candle_contract_v1 as cc       # _UTC_MS for adapting a published H4 env -> candle view
from utils import candle_runtime_seam_v1 as seam   # canonical_instrument + canonical config/allowlist/client

H4_PUBLISH_ENABLED_ENV = "HERMES_CANDLE_H4_PUBLISH_ENABLED"
# WO-...-CORE-CANDLE-WICK-HISTORY: H4 has its OWN instrument allowlist, DECOUPLED from the base canonical set
# (HERMES_CANDLE_CANONICAL_INSTRUMENTS). This lets the base M1/M5/M15/H1 latest+history fan out to the full
# enabled instrument set while H4 derivation stays on its governed (currently XAU-only) fixed-22:00-UTC grid.
H4_INSTRUMENTS_ENV = "HERMES_CANDLE_H4_INSTRUMENTS"


def _tf_name(candle):
    tf = getattr(candle, "timeframe", None)
    return tf.name if hasattr(tf, "name") else str(tf)


# --------------------------------------------------------------------------- H4 warm-start hydration helpers
# WO-HELM-HERMES-H4-H1-HYDRATION-WARMSTART-0001. Mirror of the D1 warm-start pattern, one layer down.
def _view_open_raw(c):
    """Raw open timestamp from an H1 child (dict or object); None if absent."""
    return c["timestamp"] if isinstance(c, dict) else getattr(c, "timestamp", None)


def h1_child_is_complete(view):
    """A warm-start-eligible H1 child must be a status-OK, CLOSED, fully-complete H1 on the hour (fail-closed).
    ANY non-OK / incomplete / missing-field / off-hour state makes the child INELIGIBLE (it could never make an
    OK H4). Requires: timeframe==H1, status==OK, is_closed not False, source_count==expected_source_count,
    source_coverage==1.0, gap_state in {None, NONE}, and a valid on-the-hour open."""
    if _tf_name(view) != h4d.H1_TIMEFRAME:
        return False
    if getattr(view, "status", None) != "OK":
        return False
    if getattr(view, "is_closed", None) is False:
        return False
    sc = getattr(view, "source_count", None)
    esc = getattr(view, "expected_source_count", None)
    if sc is None or esc is None or sc != esc:
        return False
    if getattr(view, "source_coverage", None) != 1.0:
        return False
    if getattr(view, "gap_state", "NONE") not in (None, "NONE"):
        return False
    ts = getattr(view, "timestamp", None)
    if ts is None:
        return False
    o = cc.normalise_utc(ts)
    if (o.minute, o.second, o.microsecond) != (0, 0, 0):       # H1 opens strictly on the hour
        return False
    return True


def h1_hydration_reject_reason(child, *, instrument, h4_open, block_end, seen_epochs):
    """RATIFIED H4 warm-start eligibility (fail-loud, deterministic). Returns a rejection reason string, or None
    if the child is an eligible complete-OK H1 member of the CURRENT H4 block. Order: timeframe -> instrument ->
    malformed/non-UTC -> outside-block -> wrong-boundary(off the hour) -> duplicate -> not-OK/incomplete."""
    if _tf_name(child) != h4d.H1_TIMEFRAME:
        return "WRONG_TIMEFRAME"
    inst = seam.canonical_instrument(getattr(child, "instrument", None) if not isinstance(child, dict)
                                     else child.get("instrument"))
    if inst != instrument or inst == "XAUUSD":
        return "INSTRUMENT_NOT_ALLOWLISTED"
    ts = _view_open_raw(child)
    if ts is None:
        return "MALFORMED_NO_TIMESTAMP"
    if isinstance(ts, datetime) and ts.tzinfo is None:
        return "NON_UTC_NAIVE_TIMESTAMP"
    try:
        opened = cc.normalise_utc(ts)
    except Exception:  # noqa: BLE001 - malformed timestamp -> reject loud, never accept
        return "MALFORMED_TIMESTAMP"
    if not (h4_open <= opened < block_end):
        return "OUTSIDE_ACTIVE_H4_BLOCK"
    if (opened.minute, opened.second) != (0, 0):
        return "WRONG_BOUNDARY_OFF_FIXED_GRID"
    if int(opened.timestamp()) in seen_epochs:
        return "DUPLICATE_CHILD"
    if not h1_child_is_complete(child):
        return "NOT_OK_OR_INCOMPLETE_H1"
    return None


class _SealedH4View:
    """Adapter: presents a freshly PUBLISHED H4 envelope as an H4 candle-like object for the D1 producer.
    Carries the canonical H4 OHLCV + open time + instrument AND the H4 COMPLETENESS/provenance (status,
    is_closed, source_count, expected_source_count, source_coverage, gap_state, source_timeframe) so the D1
    producer can enforce that a D1-OK is built only from six status-OK COMPLETE H4 children. Never a
    re-derivation, never a raw H1, never the candles_D1 table."""
    timeframe = "H4"

    def __init__(self, env):
        d = env["data"]
        self.instrument = d["instrument"]
        self.timestamp = datetime.strptime(d["timestamp_utc"][:-1], cc._UTC_MS).replace(tzinfo=timezone.utc)
        self.open, self.high, self.low = d["open"], d["high"], d["low"]
        self.close, self.volume = d["close"], d["volume"]
        # H4 completeness/provenance (so the D1 producer can fail-closed on any non-OK / incomplete child)
        self.status = env.get("status")
        self.is_closed = d.get("is_closed")
        self.source_count = d.get("source_count")
        self.expected_source_count = d.get("expected_source_count")
        self.source_coverage = d.get("source_coverage")
        self.gap_state = d.get("gap_state")
        self.source_timeframe = d.get("source_timeframe")


class CanonicalH4Producer:
    """Live derived-H4 producer. Feed it completed H1 candles via `on_h1_close`; it seals a bucket the
    moment its 4th genuine H1 child arrives (WO-HELM-HERMES-H4-COMPLETION-DRIVEN-SEAL-0001 — a completed
    H4 candle must be publishable as soon as its data is genuinely complete, not ~1h later when the next
    bucket's first H1 happens to arrive). The rollover check is PRESERVED as a fallback: if a bucket never
    reaches 4/4 (a genuine gap) it still seals — honestly incomplete — the moment the next bucket starts,
    exactly as before. `_sealed_bucket_epoch` guards both paths against re-deriving/re-publishing the same
    bucket twice in one process lifetime (duplicate H1 close, or a rollover check arriving after completion
    already sealed it) — without it, re-deriving from an already-emptied buffer would silently downgrade an
    already-published complete candle to a bogus empty one."""

    def __init__(self, writer, allowed_instruments, history_forward_writer=None, d1_producer=None,
                 durable_sql_writer=None):
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
        # Optional governed D1 producer. None (default) -> no D1. When attached + enabled, EACH sealed H4 is
        # offered to it (it publishes a D1 latest only when a full 6xH4 day exists). A D1 fault is surfaced
        # and never breaks the H4 latest publish (already done) — H4 is authoritative.
        self.d1_producer = d1_producer
        # WO-HELM-HERMES-DEV-DARWIN-DURABLE-CANONICAL-HISTORICAL-AUTHORITY-0001 — optional governed durable
        # SQL writer. None (default) -> DisabledDurableSqlWriter is substituted (no-op, own gate). When
        # attached + enabled, EACH complete sealed H4 (status OK) is persisted into the SAME durable
        # canonical_candles_h4 table the historical backfill writes, closing the backfill/live seam for
        # DARWIN. A durable-SQL fault is surfaced via its own metrics and NEVER breaks the H4 latest publish.
        if durable_sql_writer is None:
            from utils import candle_durable_sql_writer_v1 as dsw
            durable_sql_writer = dsw.DisabledDurableSqlWriter()
        self.durable_sql_writer = durable_sql_writer
        self.enabled = True
        self.metrics = {"h4_published_ok": 0, "h4_published_incomplete": 0, "h4_skipped_not_allowlisted": 0,
                        "h4_skipped_non_h1": 0, "h4_emit_fail": 0, "h4_buckets_sealed": 0,
                        "h4_history_forward_written": 0, "h4_history_forward_skipped": 0,
                        "h4_history_forward_fail": 0, "d1_hook_offered": 0, "d1_hook_published": 0,
                        "d1_hook_skipped": 0, "d1_hook_fail": 0,
                        # WO-HELM-HERMES-H4-H1-HYDRATION-WARMSTART-0001 — warm-start hydration counters
                        "h4_warmstart_attempts": 0, "h4_warmstart_children_loaded": 0,
                        "h4_warmstart_children_rejected": 0}
        self._buf = {}        # instrument -> {bucket_open_epoch: [h1_candle, ...]}
        self._current = {}    # instrument -> current (open) bucket_open_epoch
        self._sealed_bucket_epoch = {}   # instrument -> most recently sealed bucket_epoch (idempotency guard)

    def on_h1_close(self, h1_candle, **_):
        """Process a completed H1 candle. Returns a dict describing whether a bucket was published.
        WO-HELM-HERMES-H4-COMPLETION-DRIVEN-SEAL-0001: seals the bucket the H1 candle belongs to
        IMMEDIATELY once that bucket holds its full 4/4 expected children — it no longer waits for the
        next bucket's first H1 to arrive to discover completeness. The rollover check below is PRESERVED
        unchanged as the fallback for a bucket that never reaches 4/4 (a genuine gap): it still seals,
        honestly incomplete, the moment the next bucket starts. `_seal_and_publish` is idempotent per
        bucket_epoch, so whichever path reaches a bucket first is authoritative and the other becomes a
        safe no-op."""
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
        bucket = self._buf.setdefault(inst, {}).setdefault(bo_ep, [])
        bucket.append(h1_candle)
        self._current[inst] = bo_ep
        if len(bucket) >= h4d.H4_EXPECTED_CHILDREN:
            result = self._seal_and_publish(inst, bo_ep)    # complete -> seal now, don't wait for rollover
        return result

    def hydrate(self, children, *, now, instrument="XAU_USD"):
        """WARM-START the in-memory H1 buffer for the CURRENT (unsealed) H4 block from already-existing H1
        children, so a restart mid-H4-bucket no longer loses the bucket's already-closed H1 children (the trap
        that sealed the 2026-07-01 06:00 H4 at 2/4). PURE: seeds memory only — NO Redis I/O, NO publication;
        hydration itself never seals or publishes anything. What happens next (WO-HELM-HERMES-H4-COMPLETION-
        DRIVEN-SEAL-0001): a subsequent LIVE H1 child may immediately seal the hydrated bucket the moment it
        completes the genuine 4/4 set — no roll-over needed. Roll-over remains the fallback ONLY for a
        bucket that never reaches 4/4 (a genuine gap): it still seals then, honestly incomplete. If hydration
        alone already delivered the full 4/4, the bucket seals as soon as the next bucket's roll-over is
        observed (the same existing roll-over path, since hydration never triggers a seal itself). Deterministic
        + IDEMPOTENT: REPLACES the current block buffer. Bounded by the 4-child block. Returns an R2D2 report."""
        self.metrics["h4_warmstart_attempts"] += 1
        inst = seam.canonical_instrument(instrument)
        if inst not in self.allowed_instruments:
            return {"attempted": True, "succeeded": False, "reason": "INSTRUMENT_NOT_ALLOWLISTED",
                    "instrument": inst, "buffer_length": 0}
        h4_open = h4d.h4_bucket_open(now)
        block_end = h4_open + timedelta(seconds=h4d.H4_SECONDS)
        h4_open_epoch = int(h4_open.timestamp())
        ordered = sorted(children, key=lambda c: (cc.normalise_utc(_view_open_raw(c)).timestamp()
                                                  if _view_open_raw(c) is not None else float("inf")))
        accepted, rejected, seen = [], [], set()
        for c in ordered:
            reason = h1_hydration_reject_reason(c, instrument=inst, h4_open=h4_open, block_end=block_end,
                                                seen_epochs=seen)
            raw = _view_open_raw(c)
            try:
                ep = int(cc.normalise_utc(raw).timestamp()) if raw is not None else None
            except Exception:  # noqa: BLE001
                ep = None
            if reason is not None:
                rejected.append({"open_epoch": ep, "reason": reason})
                continue
            seen.add(ep)
            accepted.append(c)
        # IDEMPOTENT seed: replace the current block buffer (never append onto a stale buffer)
        if accepted:
            self._buf.setdefault(inst, {})[h4_open_epoch] = list(accepted)
        else:
            self._buf.setdefault(inst, {}).pop(h4_open_epoch, None)
        self._current[inst] = h4_open_epoch
        self.metrics["h4_warmstart_children_loaded"] = len(accepted)
        self.metrics["h4_warmstart_children_rejected"] = len(rejected)
        complete = len(accepted) == h4d.H4_EXPECTED_CHILDREN
        return {
            "attempted": True, "succeeded": True, "instrument": inst,
            "h4_block_start_utc": cc._fmt(h4_open), "h4_block_end_utc": cc._fmt(block_end),
            "h4_block_open_epoch": h4_open_epoch,
            "candidate_count": len(children), "accepted_count": len(accepted),
            "accepted_child_open_epochs": [int(cc.normalise_utc(_view_open_raw(c)).timestamp()) for c in accepted],
            "rejected_count": len(rejected), "rejected": rejected,
            "buffer_length": len(accepted),
            "remaining_children_required": max(0, h4d.H4_EXPECTED_CHILDREN - len(accepted)),
            "h4_complete_4of4": complete,
            "h4_published_by_hydration": False,         # NEVER — hydration publishes nothing
            "h4_status_after_hydration": "READY_PENDING_LIVE_ROLLOVER" if complete else "AWAITING_LIVE_H1",
            "publication_note": (
                "complete 4/4 present -> producer READY; existing H4 semantics seal ONLY on the next LIVE H1 "
                "roll-over (no retroactive/fake seal)"
                if complete else
                "partial/empty -> buffer seeded with eligible H1; awaiting live H1 closes; H4 seals live"),
        }

    def _seal_and_publish(self, instrument, bucket_epoch):
        # WO-HELM-HERMES-H4-COMPLETION-DRIVEN-SEAL-0001: idempotency guard. A bucket can now be reached
        # twice in one process lifetime — completion-driven sealing on the 4th child, then a later rollover
        # check for the same (now-emptied) bucket, or a duplicate/replayed H1 close. Without this guard the
        # second call would pop an EMPTY buffer, derive a bogus 0-child envelope, and overwrite the genuine
        # already-published candle. Once a bucket_epoch has been sealed (any status), it is never re-derived
        # or re-published again by this producer instance.
        if self._sealed_bucket_epoch.get(instrument) == bucket_epoch:
            return {"published": False, "reason": "ALREADY_SEALED", "bucket_open_epoch": bucket_epoch}
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
        self._sealed_bucket_epoch[instrument] = bucket_epoch
        d = env["data"]
        history = {"attempted": False, "reason": "H4_INCOMPLETE_NOT_HISTORY"}
        if env["status"] == "OK":
            self.metrics["h4_published_ok"] += 1
            history = self._forward_history(env)            # ONLY complete 4/4 enters history
        else:
            self.metrics["h4_published_incomplete"] += 1    # honest SOURCE_INCOMPLETE (never OK)
        # Architect review correction: H4's OWN durable-SQL persistence is attempted BEFORE this H4 is
        # offered to the D1 producer. Durable D1 must never get ahead of durable H4 truth — ordering this
        # H4's durable write first (on top of D1's own explicit source-completeness guard in
        # candle_durable_sql_writer_v1._d1_source_guard, which is the real closer of the race) means that
        # by the time D1 looks, this H4's durable attempt has already happened.
        durable = self._forward_durable_sql(env)            # ONLY complete OK enters the durable SQL authority
        d1 = self._offer_to_d1(env)                         # EACH sealed H4 offered to the D1 producer
        return {"published": True, "key": res["key"], "status": env["status"],
                "source_count": d["source_count"], "source_coverage": d["source_coverage"],
                "gap_state": d["gap_state"], "bucket_open_epoch": bucket_epoch,
                "history_forward": history, "d1_hook": d1, "durable_sql": durable}

    def _offer_to_d1(self, env):
        """Offer a freshly SEALED+published H4 to the governed D1 producer (each sealed H4 is offered; the D1
        producer publishes a D1 latest ONLY when a full 6xH4 day exists). No-op unless a D1 producer is
        attached + enabled. A D1 fault is counted + returned and NEVER undoes/blocks the H4 latest write
        (already done) — fail-loud-visible, H4 authoritative. Never writes D1 history (D1 latest only)."""
        dp = self.d1_producer
        if dp is None or not getattr(dp, "enabled", False):
            return {"attempted": False, "reason": "D1_HOOK_DISABLED"}
        self.metrics["d1_hook_offered"] += 1
        try:
            res = dp.on_h4_close(_SealedH4View(env))
        except Exception as exc:  # noqa: BLE001 - surfaced via counter, never breaks H4 latest
            self.metrics["d1_hook_fail"] += 1
            return {"attempted": True, "published": False, "reason": "D1_HOOK_FAIL", "error": repr(exc)}
        if res.get("published"):
            self.metrics["d1_hook_published"] += 1
        else:
            self.metrics["d1_hook_skipped"] += 1
        return {"attempted": True, **res}

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

    def _forward_durable_sql(self, env):
        """Persist a sealed complete H4 (status OK only) into the durable canonical_candles_h4 table —
        the LIVE half of the backfill/live continuity guarantee. No-op unless an enabled writer is attached
        (its own gate). Fault-isolated: NEVER raises, never blocks/undoes the H4 latest publish already
        completed above."""
        if env.get("status") != "OK":
            return {"attempted": False, "reason": "H4_INCOMPLETE_NOT_DURABLE"}
        dsw = self.durable_sql_writer
        if dsw is None or not getattr(dsw, "enabled", False):
            return {"attempted": False, "reason": "DURABLE_SQL_PERSIST_DISABLED"}
        try:
            return dsw.on_sealed(env)
        except Exception as exc:  # noqa: BLE001 - belt-and-braces; DurableSqlWriter itself never raises
            return {"attempted": True, "wrote": False, "reason": "DURABLE_SQL_UNEXPECTED_FAIL", "error": repr(exc)}

    def status(self):
        hw = self.history_forward_writer
        dp = self.d1_producer
        dsw = self.durable_sql_writer
        return {"enabled": True,
                "history_forward_enabled": bool(hw is not None and getattr(hw, "enabled", False)),
                "d1_hook_enabled": bool(dp is not None and getattr(dp, "enabled", False)),
                "durable_sql_enabled": bool(dsw is not None and getattr(dsw, "enabled", False)),
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
    allowed = seam.parse_canonical_allowlist(get_env(H4_INSTRUMENTS_ENV, required=True))  # fail-closed; H4 allowlist is decoupled from base canonical set
    client = seam._real_canonical_redis_client(config)
    writer = cp.SerializingCandleCanonicalWriter(config=config, redis_client=client)
    # Governed forward-history writer: default DISABLED (own gates), so attaching it changes nothing unless
    # HERMES_CANDLE_HISTORY_FORWARD_ENABLED + AUTHORISED + allowlists are set. Lazy import avoids a cycle.
    from utils import candle_history_forward_writer_v1 as hfw   # lazy; only on the H4 canonical path
    history_writer = hfw.build_history_forward_writer_from_env()
    # Governed D1 producer: default DISABLED (own gates HERMES_CANDLE_D1_PUBLISH_*). Building it here gives the
    # H4 seal path a runtime caller; with D1 flags unset it is a DisabledD1Producer -> the seal hook is a no-op.
    # Enabling D1 without authorisation / allowlist / source=H4 FAILS LOUD via build_d1_producer_from_env.
    from utils import candle_d1_publish_wire_v1 as d1w   # lazy; only on the H4 canonical path
    d1_producer = d1w.build_d1_producer_from_env()
    # Governed durable SQL writer: default DISABLED (own gate HERMES_CANDLE_H4_DURABLE_SQL_PERSIST_*).
    # WO-HELM-HERMES-DEV-DARWIN-DURABLE-CANONICAL-HISTORICAL-AUTHORITY-0001 — closes the backfill/live seam.
    from utils import candle_durable_sql_writer_v1 as dsw   # lazy; only on the H4 canonical path
    durable_sql_writer = dsw.build_h4_durable_sql_writer_from_env()
    return CanonicalH4Producer(writer, allowed_instruments=allowed, history_forward_writer=history_writer,
                               d1_producer=d1_producer, durable_sql_writer=durable_sql_writer)
