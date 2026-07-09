"""HERMES governed D1 canonical publish wiring (derived from 6xH4, fixed 22:00 UTC NY-5PM day).
WO-HELM-HERMES-GOLD-D1-CANONICAL-PUBLISH-WIRE-0001.

Observes SEALED H4 candles, groups six of them into a fixed 22:00Z->22:00Z D1 bucket, and on bucket
roll-over derives the sealed bucket via `candle_d1_derivation_v1.derive_d1` and routes the governed D1
envelope through the existing canonical writer (`SerializingCandleCanonicalWriter`). The writer's
`assert_canonical_key` (publish grid now includes D1) + `validate_candle_contract` are the governed gate;
the DIRECT seam still refuses D1, so this is the ONLY path that ever publishes a D1 latest key.

DERIVED FROM H4 ONLY — no direct `candles_D1` (midnight table), no 24xH1 production shortcut, no synthesis.
XAU_USD only. SAFEST publication rule: a D1 latest is published ONLY for a COMPLETE, closed 6/6 bucket
(status OK); a sealed <6 bucket is NEVER published (not even as SOURCE_INCOMPLETE) — honestly skipped.
Gated by `HERMES_CANDLE_D1_PUBLISH_ENABLED` (default false) ON TOP OF the canonical publish controls — so
deploying this code does NOT auto-activate D1. No Redis I/O at import; disabled -> no-op. No D1 history here.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from utils import candle_publisher_v1 as cp
from utils import candle_d1_derivation_v1 as d1d
from utils import candle_contract_v1 as cc       # normalise_utc for the H4-grid open check
from utils import candle_runtime_seam_v1 as seam   # canonical_instrument + canonical config/allowlist/client
from utils import candle_d1_history_v1 as d1h    # WO-…-D1-CANDLE-HISTORY-SERIES-0001 — dark-by-default D1 history writer

D1_PUBLISH_ENABLED_ENV = "HERMES_CANDLE_D1_PUBLISH_ENABLED"
D1_PUBLISH_AUTHORISED_ENV = "HERMES_CANDLE_D1_PUBLISH_AUTHORISED"
D1_INSTRUMENTS_ENV = "HERMES_CANDLE_D1_INSTRUMENTS"
D1_SOURCE_TIMEFRAME_ENV = "HERMES_CANDLE_D1_SOURCE_TIMEFRAME"
D1_REQUIRED_SOURCE_TIMEFRAME = "H4"        # D1 production source is 6xH4 ONLY (never 24xH1, never candles_D1)


def _tf_name(candle):
    tf = getattr(candle, "timeframe", None)
    return tf.name if hasattr(tf, "name") else str(tf)


def h4_child_is_complete(view):
    """RATIFIED D1 completeness rule: a D1-eligible H4 child must be a status-OK, CLOSED, fully-complete H4 on
    the fixed NY-5PM grid. Fail-closed — ANY non-OK / incomplete / missing-field state makes the child
    INELIGIBLE (it can never count toward a status-OK D1). Specifically requires:
      timeframe == H4, status == OK, is_closed (where available) is not False,
      source_count == expected_source_count, source_coverage == 1.0, gap_state in {None, NONE},
      and a valid fixed-grid open hour (22/02/06/10/14/18 UTC)."""
    if _tf_name(view) != d1d.H4_TIMEFRAME:
        return False
    if getattr(view, "status", None) != "OK":
        return False
    if getattr(view, "is_closed", None) is False:          # OK status already implies closed; belt-and-braces
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
    if ts is None or cc.normalise_utc(ts).hour not in d1d.D1_CHILD_H4_OPEN_HOURS_UTC:
        return False
    return True


def _view_open_raw(c):
    """Raw open timestamp from a child (dict or object); None if absent."""
    return c["timestamp"] if isinstance(c, dict) else getattr(c, "timestamp", None)


def hydration_reject_reason(child, *, instrument, d1_open, block_end, seen_epochs):
    """RATIFIED warm-start eligibility (fail-loud, deterministic). Returns a rejection reason string, or None
    if the child is an eligible D1 H4 member of the CURRENT block. Order: timeframe -> instrument -> malformed/
    non-UTC -> outside-block -> wrong-boundary(off fixed grid) -> duplicate -> not-OK/incomplete. No synthesis,
    no gap-laundering: anything not provably a complete status-OK on-grid H4 of THIS block is rejected."""
    if _tf_name(child) != d1d.H4_TIMEFRAME:
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
    if not (d1_open <= opened < block_end):
        return "OUTSIDE_ACTIVE_D1_BLOCK"
    if opened.hour not in d1d.D1_CHILD_H4_OPEN_HOURS_UTC or (opened.minute, opened.second) != (0, 0):
        return "WRONG_BOUNDARY_OFF_FIXED_GRID"
    if int(opened.timestamp()) in seen_epochs:
        return "DUPLICATE_CHILD"
    if not h4_child_is_complete(child):
        return "NOT_OK_OR_INCOMPLETE_H4"
    return None


def parse_d1_instruments(raw):
    """Explicit, fail-closed D1 instrument allowlist. Missing/empty -> raise. The alias XAUUSD and any
    non-XAU id -> raise (this lane accepts the canonical id XAU_USD ONLY; no alias config). Returns a
    frozenset. (A broker-alias INPUT candle is still canonicalised at publish time so no XAUUSD key is
    ever emitted — but the allowlist config must be the canonical id.)"""
    if raw is None or not str(raw).strip():
        raise ValueError(f"GOV-CANDLE-D1-WIRE-005: {D1_INSTRUMENTS_ENV} is required and non-empty "
                         "when D1 publication is enabled (fail-closed, no default fan-out)")
    items = [x.strip() for x in str(raw).split(",") if x.strip()]
    if not items:
        raise ValueError(f"GOV-CANDLE-D1-WIRE-005: {D1_INSTRUMENTS_ENV} is empty (fail-closed)")
    canon = set()
    for inst in items:
        if inst == "XAUUSD" or seam.canonical_instrument(inst) != "XAU_USD" or inst != "XAU_USD":
            raise ValueError(f"GOV-CANDLE-D1-WIRE-006: instrument {inst!r} not allowed "
                             "(D1 lane is canonical XAU_USD only; no XAUUSD alias / non-XAU in config)")
        canon.add("XAU_USD")
    return frozenset(canon)


def assert_d1_source_timeframe(source_timeframe):
    """The D1 production source MUST be H4 (6xH4). Anything else (e.g. H1/24xH1, M*, candles_D1) -> fail loud."""
    if source_timeframe != D1_REQUIRED_SOURCE_TIMEFRAME:
        raise ValueError(f"GOV-CANDLE-D1-WIRE-003: D1 source timeframe must be {D1_REQUIRED_SOURCE_TIMEFRAME!r} "
                         f"(6xH4 only; no 24xH1, no direct candles_D1), got {source_timeframe!r}")
    return True


class CanonicalD1Producer:
    """Live derived-D1 producer. Feed it SEALED H4 candles via `on_h4_close`; it seals a D1 bucket when the
    next bucket's first H4 arrives (so the published D1 is always the most recently CLOSED 6/6 bucket).
    Publishes ONLY a complete 6/6 status-OK D1; a sealed <6 bucket is honestly skipped, never published."""

    def __init__(self, writer, allowed_instruments, source_timeframe=D1_REQUIRED_SOURCE_TIMEFRAME,
                 d1_history_writer=None):
        if not isinstance(writer, cp.SerializingCandleCanonicalWriter):
            raise ValueError("GOV-CANDLE-D1-WIRE-001: CanonicalD1Producer requires a SerializingCandleCanonicalWriter")
        allowed = frozenset(allowed_instruments or ())
        if not allowed:
            raise ValueError("GOV-CANDLE-D1-WIRE-002: non-empty instrument allowlist required (fail-closed)")
        for inst in allowed:
            if seam.canonical_instrument(inst) != "XAU_USD" or inst == "XAUUSD":
                raise ValueError(f"GOV-CANDLE-D1-WIRE-006: instrument {inst!r} not allowed (XAU_USD only)")
        assert_d1_source_timeframe(source_timeframe)
        self.writer = writer
        # WO-…-D1-CANDLE-HISTORY-SERIES-0001 — forward D1 history writer, DARK by default (DisabledD1HistoryWriter
        # is a no-op with no client). Additive-only: it snapshots the sealed 6/6 D1 latest AFTER a successful
        # publish and can NEVER affect the D1 latest seal/publish path (fault-isolated in on_d1_sealed).
        self._d1_history_writer = d1_history_writer or d1h.DisabledD1HistoryWriter()
        self.allowed_instruments = allowed
        self.source_timeframe = source_timeframe
        self.enabled = True
        self.metrics = {"d1_published_ok": 0, "d1_skipped_incomplete": 0, "d1_skipped_incomplete_child": 0,
                        "d1_skipped_not_allowlisted": 0, "d1_skipped_non_h4": 0, "d1_emit_fail": 0,
                        "d1_buckets_sealed": 0,
                        # WO-HELM-HERMES-D1-H4-HYDRATION-WARMSTART-0001 — warm-start hydration counters
                        "d1_warmstart_attempts": 0, "d1_warmstart_children_loaded": 0,
                        "d1_warmstart_children_rejected": 0}
        self._buf = {}        # instrument -> {d1_bucket_open_epoch: [COMPLETE-OK h4_candle, ...]}
        self._current = {}    # instrument -> current (open) d1_bucket_open_epoch

    def on_h4_close(self, h4_candle, **_):
        """Process a SEALED H4 candle. Buffers it as a D1 child ONLY if it is a status-OK COMPLETE H4
        (h4_child_is_complete); a non-OK/incomplete H4 is counted + skipped (never a D1-OK child). Returns a
        dict describing whether a prior D1 bucket was published and whether THIS child was accepted."""
        if h4_candle is None:
            return {"published": False, "reason": "NO_CANDLE"}
        if _tf_name(h4_candle) != d1d.H4_TIMEFRAME:        # only H4 drives D1 (never H1/24xH1, never candles_D1)
            self.metrics["d1_skipped_non_h4"] += 1
            return {"published": False, "reason": "NOT_H4", "timeframe": _tf_name(h4_candle)}
        inst = seam.canonical_instrument(getattr(h4_candle, "instrument", None))
        if inst not in self.allowed_instruments:
            self.metrics["d1_skipped_not_allowlisted"] += 1
            return {"published": False, "reason": "INSTRUMENT_NOT_ALLOWLISTED", "instrument": inst}
        bo_ep = int(d1d.d1_bucket_open(h4_candle.timestamp).timestamp())
        prev = self._current.get(inst)
        rolled = None
        if prev is not None and prev != bo_ep:
            rolled = self._seal_and_publish(inst, prev)     # a new D1 day started -> seal+publish the previous
        accepted = h4_child_is_complete(h4_candle)          # RATIFIED rule: only complete status-OK H4 counts
        if accepted:
            self._buf.setdefault(inst, {}).setdefault(bo_ep, []).append(h4_candle)
        else:
            self.metrics["d1_skipped_incomplete_child"] += 1
        self._current[inst] = bo_ep
        if rolled is not None:
            return {**rolled, "child_accepted": accepted}
        return {"published": False, "bucket_open_epoch": bo_ep, "child_accepted": accepted,
                "reason": "BUFFERED" if accepted else "INCOMPLETE_CHILD_SKIPPED",
                "child_status": getattr(h4_candle, "status", None)}

    def hydrate(self, children, *, now, instrument="XAU_USD"):
        """WARM-START the in-memory buffer for the CURRENT (unsealed) D1 block from already-existing H4 children,
        so a restart no longer loses the day's already-sealed H4 children. PURE: seeds memory only — NO Redis I/O,
        NO publication (the current block is published ONLY by a later LIVE roll-over via on_h4_close, never here,
        so D1 stays AMBER until a genuine live seal). Deterministic + IDEMPOTENT: REPLACES the current block
        buffer (re-running with the same inputs yields the same state). Bounded by the 6-child block. Returns a
        detailed report for R2D2 (block start/end, candidates, accepted, rejected+reasons, buffer state, ready)."""
        self.metrics["d1_warmstart_attempts"] += 1
        inst = seam.canonical_instrument(instrument)
        if inst not in self.allowed_instruments:
            return {"attempted": True, "succeeded": False, "reason": "INSTRUMENT_NOT_ALLOWLISTED",
                    "instrument": inst, "buffer_length": 0}
        d1_open = d1d.d1_bucket_open(now)
        d1d.assert_d1_open_anchor(d1_open)              # fail-loud: 22:00:00 UTC fixed (no UTC-midnight/DST)
        block_end = d1_open + timedelta(seconds=d1d.D1_SECONDS)
        d1_open_epoch = int(d1_open.timestamp())
        ordered = sorted(children, key=lambda c: (cc.normalise_utc(_view_open_raw(c)).timestamp()
                                                  if _view_open_raw(c) is not None else float("inf")))
        accepted, rejected, seen = [], [], set()
        for c in ordered:
            reason = hydration_reject_reason(c, instrument=inst, d1_open=d1_open, block_end=block_end,
                                             seen_epochs=seen)
            raw = _view_open_raw(c)
            ep = None
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
            self._buf.setdefault(inst, {})[d1_open_epoch] = list(accepted)
        else:
            self._buf.setdefault(inst, {}).pop(d1_open_epoch, None)
        self._current[inst] = d1_open_epoch
        self.metrics["d1_warmstart_children_loaded"] = len(accepted)
        self.metrics["d1_warmstart_children_rejected"] = len(rejected)
        complete = len(accepted) == d1d.D1_EXPECTED_CHILDREN
        return {
            "attempted": True, "succeeded": True, "instrument": inst,
            "d1_block_start_utc": cc._fmt(d1_open), "d1_block_end_utc": cc._fmt(block_end),
            "d1_block_open_epoch": d1_open_epoch,
            "candidate_count": len(children),
            "accepted_count": len(accepted),
            "accepted_child_open_epochs": [int(cc.normalise_utc(_view_open_raw(c)).timestamp()) for c in accepted],
            "rejected_count": len(rejected), "rejected": rejected,
            "buffer_length": len(accepted),
            "remaining_children_required": max(0, d1d.D1_EXPECTED_CHILDREN - len(accepted)),
            "d1_complete_6of6": complete,
            "d1_published_by_hydration": False,         # NEVER — hydration publishes nothing
            "d1_remains_gated_amber": True,
            "d1_status_after_hydration": "READY_PENDING_LIVE_ROLLOVER" if complete else "AMBER_AWAITING_LIVE_H4",
            "publication_note": (
                "complete 6/6 present -> producer READY; existing D1 semantics publish ONLY on the next LIVE H4 "
                "roll-over (no retroactive/fake seal) -> D1 stays AMBER until that genuine live seal"
                if complete else
                "partial/empty -> buffer seeded with eligible children; awaiting live H4 closes; D1 AMBER/pending"),
        }

    def _seal_and_publish(self, instrument, bucket_epoch):
        children = self._buf.get(instrument, {}).pop(bucket_epoch, [])
        self.metrics["d1_buckets_sealed"] += 1
        d1_open = datetime.fromtimestamp(bucket_epoch, tz=timezone.utc)
        # Defense-in-depth: a D1-OK requires EXACTLY six status-OK COMPLETE H4 children. Incomplete children
        # were never buffered, so a short day has <6 here; re-assert every buffered child is still complete-OK.
        if len(children) != d1d.D1_EXPECTED_CHILDREN or any(not h4_child_is_complete(c) for c in children):
            self.metrics["d1_skipped_incomplete"] += 1
            return {"published": False, "reason": "D1_INCOMPLETE_NOT_PUBLISHED",
                    "complete_child_count": len(children), "expected": d1d.D1_EXPECTED_CHILDREN,
                    "bucket_open_epoch": bucket_epoch}
        try:
            env, meta = d1d.derive_d1(
                instrument=instrument, d1_open=d1_open, h4_children=children,
                generated_at_utc=d1_open + timedelta(seconds=d1d.D1_SECONDS), is_closed=True)
        except Exception as exc:  # noqa: BLE001 - surfaced (counter + return), never silently swallowed
            self.metrics["d1_emit_fail"] += 1
            return {"published": False, "reason": "D1_DERIVE_FAIL", "error": repr(exc),
                    "bucket_open_epoch": bucket_epoch}
        d = env["data"]
        # SAFEST rule: publish ONLY a complete closed 6/6 (status OK). A sealed <6 bucket is never published.
        if env["status"] != "OK":
            self.metrics["d1_skipped_incomplete"] += 1
            return {"published": False, "reason": "D1_INCOMPLETE_NOT_PUBLISHED", "status": env["status"],
                    "source_count": d["source_count"], "source_coverage": d["source_coverage"],
                    "gap_state": d["gap_state"], "bucket_open_epoch": bucket_epoch}
        try:
            res = self.writer.publish(env)                  # assert_canonical_key (D1 grid) + validate + SET EX
        except Exception as exc:  # noqa: BLE001
            self.metrics["d1_emit_fail"] += 1
            return {"published": False, "reason": "D1_EMIT_FAIL", "error": repr(exc),
                    "bucket_open_epoch": bucket_epoch}
        self.metrics["d1_published_ok"] += 1
        # WO-…-D1-CANDLE-HISTORY-SERIES-0001 — forward-append the just-published SEALED 6/6 D1 into the D1 history
        # series. DARK by default (no-op writer) so deploy without the D1-history gate does NOT auto-activate it;
        # fault-isolated (on_d1_sealed never raises) so a history fault can never disrupt the D1 latest above.
        self._d1_history_writer.on_d1_sealed(env)
        return {"published": True, "key": res["key"], "status": env["status"],
                "source_count": d["source_count"], "source_coverage": d["source_coverage"],
                "gap_state": d["gap_state"], "bucket_open_epoch": bucket_epoch}

    def status(self):
        return {"enabled": True, "source_timeframe": self.source_timeframe, **self.metrics}


class DisabledD1Producer:
    """No-op D1 producer (default). Publishes nothing; constructs no Redis client."""
    enabled = False

    def on_h4_close(self, *a, **k):
        return {"published": False, "reason": "D1_PUBLISH_DISABLED"}

    def status(self):
        return {"enabled": False}


def build_d1_producer_from_env():
    """Boot factory. Returns DisabledD1Producer (no-op) UNLESS canonical forwarding is on (SINK=canonical)
    AND HERMES_CANDLE_D1_PUBLISH_ENABLED=true. Then requires HERMES_CANDLE_D1_PUBLISH_AUTHORISED=true,
    an explicit XAU_USD allowlist, and source timeframe H4 (all fail-loud). Gated ALSO by the canonical
    publish controls (publish enabled+authorised, explicit bus target). Deploying this code with canonical
    live but the D1 flag unset keeps D1 dark. No Redis I/O at import."""
    from env_config import get_env, get_env_bool, get_env_int   # lazy; HERMES-owned config only
    if not get_env_bool("HERMES_CANDLE_FORWARD_ENABLED", False):
        return DisabledD1Producer()
    if (get_env("HERMES_CANDLE_FORWARD_SINK", default="") or "").strip().lower() != seam.CANONICAL_SINK:
        return DisabledD1Producer()
    if not get_env_bool(D1_PUBLISH_ENABLED_ENV, False):
        return DisabledD1Producer()
    if not get_env_bool(D1_PUBLISH_AUTHORISED_ENV, False):
        raise ValueError(f"GOV-CANDLE-D1-WIRE-004: {D1_PUBLISH_ENABLED_ENV}=true requires "
                         f"{D1_PUBLISH_AUTHORISED_ENV}=true (refusing to publish D1 without authorisation)")
    source_tf = (get_env(D1_SOURCE_TIMEFRAME_ENV, default=D1_REQUIRED_SOURCE_TIMEFRAME) or "").strip()
    assert_d1_source_timeframe(source_tf)
    allowed = parse_d1_instruments(get_env(D1_INSTRUMENTS_ENV, default=None))
    config = seam._canonical_config_from_env(get_env, get_env_bool, get_env_int)
    config.assert_canonical_allowed()                          # publish_enabled AND authorised (fail-loud)
    client = seam._real_canonical_redis_client(config)
    writer = cp.SerializingCandleCanonicalWriter(config=config, redis_client=client)
    # WO-…-D1-CANDLE-HISTORY-SERIES-0001 — build the D1 history writer DARK by default (own HERMES_CANDLE_D1_HISTORY_*
    # gate). D1 latest live + D1-history gate unset => DisabledD1HistoryWriter (no-op) => D1 history stays dark.
    d1_history_writer = d1h.build_d1_history_writer_from_env(redis_client=client)
    return CanonicalD1Producer(writer, allowed_instruments=allowed, source_timeframe=source_tf,
                               d1_history_writer=d1_history_writer)
