"""HERMES governed FORWARD history writer v1.
WO-HELM-HERMES-GOLD-MTF-FORWARD-HISTORY-WRITER-0001.

Appends CLOSED canonical candles to the governed Redis history keyspace as they are produced, so history
stays continuous instead of decaying under the configured retention TTL after a one-time snapshot backfill.

Also prunes the ordered ZSET index on every write (WO-HELM-HERMES-DEV-REDIS-CAPACITY-RETENTION-AND-PROD-
INCIDENT-RECOVERY-DESIGN-0001): the per-candle key already expires via Redis TTL, but nothing previously
removed its now-stale index entry, so the index grew forever regardless of retention — the confirmed root
cause of PROD's unbounded Redis growth. Pruning here keeps the fix on the same hot path as the write it
protects, touches only the one index just written to, and removes at most the handful of members that
crossed the cutoff since the last write — cheap and non-blocking by construction.

It does NOT derive or query anything: it SNAPSHOTS an already-built, already-validated canonical candle
envelope (the exact payload written to `:latest:v1`) into its immutable history key, adding only a `history`
provenance block whose schema is IDENTICAL to the backfill writer's (5 fields) so the history surface stays
uniform across backfill + forward records. Every guard from the history contract still runs before any write.

Triggers (called by a LATER, separately-authorised runtime-integrate WO — NOT wired here):
  * M1/M5/M15/H1  -> on_canonical_close(envelope)  when the canonical latest close event is emitted
  * H4            -> on_h4_sealed(envelope)        when the derived H4 producer seals a COMPLETE 4/4 bucket

DISABLED BY DEFAULT. No Redis I/O at import. Disabled -> no-op. Enabled-without-authorised -> FAIL LOUD.
Scope: XAU_USD only; M1/M5/M15/H1/H4 only. No D1, no regime, no XAUUSD output, no shadow, no backfill here.
"""
from __future__ import annotations
import copy
import json

from utils import candle_contract_v1 as cc
from utils import candle_history_v1 as chv
from utils import candle_runtime_seam_v1 as seam   # canonical_instrument + canonical redis target
from utils.hermes_redis_auth_v1 import redis_auth_kwargs

# ----- governed env controls (explicit, no hidden defaults) -----
ENABLED_ENV = "HERMES_CANDLE_HISTORY_FORWARD_ENABLED"
AUTHORISED_ENV = "HERMES_CANDLE_HISTORY_FORWARD_AUTHORISED"
TIMEFRAMES_ENV = "HERMES_CANDLE_HISTORY_FORWARD_TIMEFRAMES"
INSTRUMENTS_ENV = "HERMES_CANDLE_HISTORY_FORWARD_INSTRUMENTS"
# redis target reused from the canonical bus contract (explicit, required when forwarding is enabled)
REDIS_HOST_ENV = "HERMES_CANDLE_CANONICAL_REDIS_HOST"
REDIS_PORT_ENV = "HERMES_CANDLE_CANONICAL_REDIS_PORT"
REDIS_DB_ENV = "HERMES_CANDLE_CANONICAL_REDIS_DB"

FORWARD_TIMEFRAMES = chv.HISTORY_TIMEFRAMES        # ("M1","M5","M15","H1","H4") — D1/D excluded
_D1_DENY = ("D1", "D")
FORWARD_RUN_MARKER = "FORWARD:WO-HELM-HERMES-GOLD-MTF-FORWARD-HISTORY-WRITER-0001"
HISTORY_STATUS_ALLOWED = ("OK",)                   # only complete, closed, fresh candles enter history

REASON_DISABLED = "HISTORY_FORWARD_DISABLED"
REASON_FORMING = "FORMING_NOT_HISTORY"
REASON_STATUS_NOT_OK = "STATUS_NOT_OK"
REASON_TF_NOT_CONFIGURED = "TF_NOT_CONFIGURED"
REASON_NOT_ALLOWLISTED = "INSTRUMENT_NOT_ALLOWLISTED"


# --------------------------------------------------------------------------- parse helpers (fail-closed)
def parse_forward_timeframes(raw):
    """Explicit, fail-closed timeframe list. Missing/empty -> raise (no hidden default). D1/D -> raise.
    Anything outside the history grid -> raise. Returns an ordered, de-duplicated tuple."""
    if raw is None or not str(raw).strip():
        raise ValueError(f"GOV-CANDLE-HIST-FWD-003: {TIMEFRAMES_ENV} is required and non-empty "
                         "when history forwarding is enabled (no hidden defaults)")
    items = [x.strip() for x in str(raw).split(",") if x.strip()]
    if not items:
        raise ValueError(f"GOV-CANDLE-HIST-FWD-003: {TIMEFRAMES_ENV} is required and non-empty "
                         "when history forwarding is enabled (no hidden defaults)")
    out = []
    for tf in items:
        if tf in _D1_DENY:
            raise ValueError(f"GOV-CANDLE-HIST-FWD-005: timeframe {tf!r} (D1/D) is forbidden in forward history")
        if tf not in FORWARD_TIMEFRAMES:
            raise ValueError(f"GOV-CANDLE-HIST-FWD-007: timeframe {tf!r} not in history grid {FORWARD_TIMEFRAMES}")
        if tf not in out:
            out.append(tf)
    return tuple(out)


def parse_forward_instruments(raw):
    """Explicit, fail-closed instrument allowlist. Missing/empty -> raise. Alias (XAUUSD) is canonicalised
    to XAU_USD; any non-XAU id -> raise (this lane is XAU_USD only). Returns a frozenset of canonical ids."""
    if raw is None or not str(raw).strip():
        raise ValueError(f"GOV-CANDLE-HIST-FWD-004: {INSTRUMENTS_ENV} is required and non-empty "
                         "when history forwarding is enabled (fail-closed, no default fan-out)")
    items = [x.strip() for x in str(raw).split(",") if x.strip()]
    if not items:
        raise ValueError(f"GOV-CANDLE-HIST-FWD-004: {INSTRUMENTS_ENV} is required and non-empty "
                         "when history forwarding is enabled (fail-closed, no default fan-out)")
    canon = set()
    for inst in items:
        # WO-HELM-HERMES-DEV-MULTI-INSTRUMENT-CORE-CANDLE-WICK-HISTORY-CONTRACT-0001: the forward-history
        # lane now serves the configured multi-instrument enabled set. Aliases (XAUUSD) are still rejected
        # as an output-key request; canonical ids are accepted. Fail-closed remains (raw is required and is
        # the EXPLICIT allowlist -> no default fan-out to all instruments).
        if inst in chv._ALIAS_DENY:
            raise ValueError(f"GOV-CANDLE-HIST-FWD-006: alias {inst!r} not allowed as a canonical output "
                             "instrument (use the canonical id, e.g. XAU_USD)")
        canon.add(seam.canonical_instrument(inst))
    return frozenset(canon)


def _candle_fingerprint(env):
    """Immutable candle truth (OHLCV + geometry + timestamp + instrument/tf + source provenance counts).
    Excludes the volatile `history.backfill_inserted_at_utc` so an idempotent re-write of the same candle
    compares equal regardless of when it was inserted."""
    return json.dumps(env["data"], sort_keys=True)


# --------------------------------------------------------------------------- the writer
class CandleHistoryForwardWriter:
    """Governed forward-history writer. Snapshots a CLOSED, status-OK canonical candle envelope into its
    immutable history key + ordered index. Every write: instrument/timeframe guard -> closed+OK guard ->
    history provenance block -> validate_candle_contract -> build_history_write_plan (asserts history
    target on key AND index) -> idempotent SET(TTL) + ZADD(score=member=open_epoch). Never touches latest."""

    def __init__(self, *, redis_client, allowed_instruments, timeframes, run_id=FORWARD_RUN_MARKER):
        allowed = frozenset(allowed_instruments)
        if not allowed:
            raise ValueError("GOV-CANDLE-HIST-FWD-001: forward-history allowlist must be non-empty")
        for inst in allowed:
            # Multi-instrument (WO-...-CORE-CANDLE-WICK-HISTORY): accept any canonical id; still reject the
            # XAUUSD alias form as an output instrument. Enablement is governed by the explicit config allowlist.
            if inst in chv._ALIAS_DENY:
                raise ValueError(f"GOV-CANDLE-HIST-FWD-006: alias {inst!r} not allowed (use canonical id)")
        tfs = tuple(timeframes)
        if not tfs:
            raise ValueError("GOV-CANDLE-HIST-FWD-008: forward-history timeframes must be non-empty")
        for tf in tfs:
            if tf in _D1_DENY:
                raise ValueError(f"GOV-CANDLE-HIST-FWD-005: timeframe {tf!r} (D1/D) forbidden")
            if tf not in FORWARD_TIMEFRAMES:
                raise ValueError(f"GOV-CANDLE-HIST-FWD-007: timeframe {tf!r} not in history grid")
        self.redis_client = redis_client
        self.allowed_instruments = allowed
        self.timeframes = tfs
        self.run_id = str(run_id)
        self.enabled = True
        self.metrics = {"history_written": 0, "history_idempotent_duplicate": 0,
                        "history_skipped_forming": 0, "history_skipped_status": 0,
                        "history_skipped_tf": 0, "history_skipped_instrument": 0,
                        "history_index_pruned": 0, "history_index_prune_errors": 0}

    # ---- public trigger hooks (called by a later runtime-integrate WO) ----
    def on_canonical_close(self, envelope, *, inserted_at_utc):
        """M1/M5/M15/H1 trigger: snapshot a freshly-closed DIRECT canonical candle to history."""
        return self.write_closed_envelope(envelope, inserted_at_utc=inserted_at_utc,
                                          source_table="canonical_latest_forward:DIRECT")

    def on_h4_sealed(self, envelope, *, inserted_at_utc):
        """H4 trigger: snapshot a sealed DERIVED H4 candle. Incomplete (status != OK) is skipped, never
        written as OK; forming is skipped. No synthesis of missing children — that is the producer's job."""
        return self.write_closed_envelope(envelope, inserted_at_utc=inserted_at_utc,
                                          source_table="canonical_latest_forward:DERIVED_H4_FROM_H1")

    # ---- core ----
    def write_closed_envelope(self, envelope, *, inserted_at_utc, source_table):
        d = envelope["data"]
        inst = seam.canonical_instrument(d["instrument"])           # XAUUSD -> XAU_USD (never an alias output)
        tf = d["timeframe"]
        if tf in _D1_DENY:
            raise ValueError(f"GOV-CANDLE-HIST-FWD-014: D1/D candle {tf!r} may never enter forward history")
        if inst not in self.allowed_instruments:
            self.metrics["history_skipped_instrument"] += 1
            return {"wrote": False, "reason": REASON_NOT_ALLOWLISTED, "instrument": d["instrument"]}
        if tf not in self.timeframes:
            self.metrics["history_skipped_tf"] += 1
            return {"wrote": False, "reason": REASON_TF_NOT_CONFIGURED, "timeframe": tf}
        if not d.get("is_closed"):
            self.metrics["history_skipped_forming"] += 1
            return {"wrote": False, "reason": REASON_FORMING, "timeframe": tf}
        if envelope.get("status") not in HISTORY_STATUS_ALLOWED:
            self.metrics["history_skipped_status"] += 1
            return {"wrote": False, "reason": REASON_STATUS_NOT_OK, "status": envelope.get("status"), "timeframe": tf}

        # build the history payload: deep copy (never mutate the caller's latest envelope) + history block
        he = copy.deepcopy(envelope)
        he["data"]["instrument"] = inst                            # canonical id only (defensive)
        he["history"] = {
            "history_contract_version": chv.HISTORY_CONTRACT_VERSION,
            "backfill_run_id": self.run_id,                        # schema-identical to backfill; FORWARD-marked
            "backfill_inserted_at_utc": cc._fmt(cc.normalise_utc(inserted_at_utc)),
            "source_table": str(source_table),
            "source_timestamp_utc": d["timestamp_utc"],
        }
        cc.validate_candle_contract(he)                            # fail-loud on any leak (regime/etc.)
        plan = chv.build_history_write_plan(he)                    # re-validates + asserts history target (key+index)
        chv.assert_history_target(plan["key"])                     # belt-and-braces before any write
        chv.assert_history_target(plan["index_key"])
        if "XAUUSD" in plan["key"]:                                # impossible by construction; fail loud anyway
            raise ValueError("GOV-CANDLE-HIST-FWD-009: refusing alias XAUUSD history key")

        idempotent = False
        existing = self.redis_client.get(plan["key"])
        if existing is not None:
            ex_env = json.loads(existing.decode() if isinstance(existing, (bytes, bytearray)) else existing)
            if _candle_fingerprint(ex_env) != _candle_fingerprint(he):
                raise ValueError(f"GOV-CANDLE-HIST-FWD-020: open_epoch conflict at {plan['key']!r} — "
                                 "existing candle truth differs from new (refusing to overwrite history)")
            idempotent = True

        # idempotent by construction: identical key + identical ZSET member/score -> re-write refreshes TTL only
        self.redis_client.set(plan["key"], json.dumps(plan["value"]), ex=plan["ttl_seconds"])
        self.redis_client.zadd(plan["index_key"], {plan["index_member"]: plan["index_score"]})
        if idempotent:
            self.metrics["history_idempotent_duplicate"] += 1
        else:
            self.metrics["history_written"] += 1

        # GOV-CANDLE-HIST-FWD-021 (capacity fix): the per-candle key expires on its own TTL, but nothing
        # removed its index entry — the confirmed cause of unbounded Redis growth. Prune members that have
        # already crossed the same retention cutoff the TTL uses, so index and live data stay bounded
        # together. Best-effort: a pruning failure never invalidates the write that already succeeded above,
        # so it is caught and counted rather than raised.
        #
        # Cutoff is anchored to `inserted_at_utc` (the caller's declared "as of" time for this write) rather
        # than a hidden `datetime.now()` read: deterministic, replay-safe, and correct even when this writer
        # processes candles whose wall-clock insertion time differs from their own timestamp (e.g. a bounded
        # catch-up after a pause) — the cutoff always means "N days before this write believes it is
        # happening", never "N days before whatever moment this line of code happens to execute".
        pruned = 0
        try:
            cutoff = chv.history_retention_cutoff_epoch(cc.normalise_utc(inserted_at_utc))
            pruned = self.redis_client.zremrangebyscore(plan["index_key"], "-inf", cutoff)
            self.metrics["history_index_pruned"] += pruned
        except Exception:
            self.metrics["history_index_prune_errors"] += 1

        return {"wrote": True, "key": plan["key"], "index_key": plan["index_key"], "timeframe": tf,
                "instrument": inst, "open_epoch": plan["index_score"], "ttl": plan["ttl_seconds"],
                "idempotent_duplicate": idempotent, "status": "OK", "index_pruned": pruned}

    def status(self):
        return {"enabled": True, "timeframes": list(self.timeframes),
                "instruments": sorted(self.allowed_instruments), **self.metrics}


class DisabledHistoryForwardWriter:
    """Safe no-op when history forwarding is disabled. Never touches Redis."""
    enabled = False

    def on_canonical_close(self, *_a, **_k):
        return {"wrote": False, "reason": REASON_DISABLED}

    def on_h4_sealed(self, *_a, **_k):
        return {"wrote": False, "reason": REASON_DISABLED}

    def write_closed_envelope(self, *_a, **_k):
        return {"wrote": False, "reason": REASON_DISABLED}

    def status(self):
        return {"enabled": False}


def _redis_client_from_env(get_env, get_env_int):
    import redis  # lazy; only on the enabled path
    return redis.Redis(host=get_env(REDIS_HOST_ENV, required=True),
                       port=get_env_int(REDIS_PORT_ENV, required=True),
                       db=get_env_int(REDIS_DB_ENV, required=True), socket_timeout=5, **redis_auth_kwargs())


def build_history_forward_writer_from_env():
    """Boot factory. DEFAULT DISABLED -> DisabledHistoryForwardWriter (no-op). Enabled requires:
      HERMES_CANDLE_HISTORY_FORWARD_ENABLED=true
      HERMES_CANDLE_HISTORY_FORWARD_AUTHORISED=true            (else FAIL LOUD)
      HERMES_CANDLE_HISTORY_FORWARD_TIMEFRAMES=<explicit, no D1> (else FAIL LOUD)
      HERMES_CANDLE_HISTORY_FORWARD_INSTRUMENTS=<explicit XAU_USD> (else FAIL LOUD)
      HERMES_CANDLE_CANONICAL_REDIS_HOST/PORT/DB                (explicit bus target)
    No Redis I/O at import; the client is only built when enabled+authorised."""
    from env_config import get_env, get_env_bool, get_env_int   # lazy; HERMES-owned config only
    if not get_env_bool(ENABLED_ENV, False):
        return DisabledHistoryForwardWriter()
    if not get_env_bool(AUTHORISED_ENV, False):
        raise ValueError(f"GOV-CANDLE-HIST-FWD-002: {ENABLED_ENV}=true requires {AUTHORISED_ENV}=true "
                         "(refusing to forward history without explicit authorisation)")
    timeframes = parse_forward_timeframes(get_env(TIMEFRAMES_ENV, default=None))
    instruments = parse_forward_instruments(get_env(INSTRUMENTS_ENV, default=None))
    client = _redis_client_from_env(get_env, get_env_int)
    return CandleHistoryForwardWriter(redis_client=client, allowed_instruments=instruments,
                                      timeframes=timeframes, run_id=FORWARD_RUN_MARKER)
