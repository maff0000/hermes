"""HERMES PH2 recovery surface — hermes:gaps:XAU_USD:v1 DARK / READ-ONLY gap-detection publisher v1.
WO-HELM-HERMES-PH2-GAPS-SURFACE-0001.

Deterministic, read-only gap DETECTION only — this surface NEVER repairs, NEVER backfills, NEVER writes/deletes any
Redis candle key, NEVER calls SQL/vendor/market_map, and carries no strategy/risk/signal/trade semantics. It reads the
governed candle latest/history surfaces (hermes:candles:XAU_USD:{tf}:latest:v1 + :history:v1:index/{open_epoch}) and
reports, per timeframe, whether the OPEN-MARKET grid is complete — classifying every non-OK state fail-closed.

Market calendar (R2D2 ruling): gold open-market is ~Sunday 22:00Z -> Friday 21:00Z; the weekend is MARKET_CLOSED and its
missing slots are NEVER GAPS_FOUND (calendar_source=WEEKLY_WEEKEND_UTC; holiday calendar is a documented open question).
D1 boundary is enforced from day one (22:00 NY-5PM anchor only; 00:00 = INVALID_ANCHOR; sealed 6/6; XAU_USD; latest==history-newest).

DARK by default: build_gaps_publisher_from_env() -> DisabledGapsPublisher (no I/O). The core builders are PURE (data-injected)
so they are fully testable without Redis and can never auto-publish. Runtime publication (GapsPublisher.publish -> a single
SET of GAPS_KEY, wired as the gated gaps runtime step in WO-HELM-HERMES-PH2-GAPS-SURFACE-PUBLISH-WIRING-0001) stays inert
until HERMES_GAPS_PUBLISH_ENABLED and HERMES_GAPS_PUBLISH_AUTHORISED are both set; enabling activation is a SEPARATE later WO.
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone

from utils import candle_contract_v1 as cc
from utils import candle_d1_history_v1 as d1h     # reuse: assert_sealed_complete_d1 + D1 forward gate names (safe shared)

UTC = timezone.utc
CANONICAL_INSTRUMENT = "XAU_USD"
_ALIAS_DENY = ("XAUUSD",)
SCHEMA_VERSION = "v1"
PUBLISHER = "HERMES"
CALENDAR_SOURCE = "WEEKLY_WEEKEND_UTC"           # holiday calendar = documented open question, NOT implemented here
MISSING_SAMPLE_CAP = 20                          # bounded — never an unbounded epoch list
_OOR_LOOKBACK_PERIODS = 5                         # bounded slots probed just before the retention floor (OUT_OF_RETENTION)

TIMEFRAMES = ("M1", "M5", "M15", "H1", "H4", "D1")
PERIOD_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}
STALE_THRESHOLD_SECONDS = {tf: 2 * PERIOD_SECONDS[tf] for tf in TIMEFRAMES}   # market-aware 2x period
MIN_REQUIRED_DEPTH = {tf: 26 for tf in TIMEFRAMES}                            # provisional EMA-26 floor, uniform
RETENTION_DAYS = {"M1": 35, "M5": 35, "M15": 35, "H1": 35, "H4": 35, "D1": 120}
EXPECTED_GRID_POLICY = {"M1": "NATIVE_UTC_GRID", "M5": "NATIVE_UTC_GRID", "M15": "NATIVE_UTC_GRID",
                        "H1": "NATIVE_UTC_GRID", "H4": "FIXED_2200_NY5PM", "D1": "FIXED_2200_DAILY"}
ANCHOR = {"M1": "NATIVE_UTC_GRID", "M5": "NATIVE_UTC_GRID", "M15": "NATIVE_UTC_GRID", "H1": "NATIVE_UTC_GRID",
          "H4": "FIXED_2200_UTC_NY5PM (22/02/06/10/14/18)", "D1": "FIXED_2200_UTC_NY5PM_DAILY"}
_NY5PM_SHIFT = 2 * 3600                           # 22:00Z is 2h before UTC midnight -> aligns H4/D1 grids to 22:00

# Fail-closed states, HIGH->LOW severity (worst-of overall). Every non-OK is explicit; nothing silently becomes OK.
GAP_STATES = ("SOURCE_MISSING", "INVALID_ANCHOR", "INSUFFICIENT_HISTORY", "GAPS_FOUND", "STALE",
              "MARKET_CLOSED", "OUT_OF_RETENTION", "OK")
_SEVERITY = {s: i for i, s in enumerate(GAP_STATES)}     # lower index = worse
MARKET_PHASES = ("OPEN", "CLOSED_WEEKEND")

# D1 gates (reused from the D1 history writer) for forward-writer state reporting.
D1_FWD_ENABLED_ENV = d1h.D1_HISTORY_ENABLED_ENV
D1_FWD_AUTHORISED_ENV = d1h.D1_HISTORY_AUTHORISED_ENV
# This surface's own dark activation gates (unset by default -> DisabledGapsPublisher).
GAPS_ENABLED_ENV = "HERMES_GAPS_PUBLISH_ENABLED"
GAPS_AUTHORISED_ENV = "HERMES_GAPS_PUBLISH_AUTHORISED"
HALT_CODE = 101
GAPS_KEY = f"hermes:gaps:{CANONICAL_INSTRUMENT}:v1"   # the SINGLE aggregate key this surface ever writes (SET target)


# --------------------------------------------------------------------------- market calendar (weekly weekend, UTC)
def market_phase(dt):
    """OPEN vs CLOSED_WEEKEND for gold at UTC datetime dt. Open-market ~ Sunday 22:00Z -> Friday 21:00Z (weekly).
    Mon-Thu open all day; Fri open until 21:00Z; Sat closed; Sun closed until 22:00Z. Holiday calendar NOT modelled."""
    d = cc.normalise_utc(dt)
    wd, hm = d.weekday(), d.hour * 60 + d.minute     # Mon=0 .. Sun=6
    if wd in (0, 1, 2, 3):                           # Mon-Thu
        return "OPEN"
    if wd == 4:                                       # Friday: open until 21:00Z
        return "OPEN" if hm < 21 * 60 else "CLOSED_WEEKEND"
    if wd == 6:                                        # Sunday: closed until 22:00Z
        return "OPEN" if hm >= 22 * 60 else "CLOSED_WEEKEND"
    return "CLOSED_WEEKEND"                            # Saturday


def _period_fully_open(open_epoch, tf):
    """True iff the WHOLE candle period [open, open+period) lies in open-market time (sampled at open + each hour + end)."""
    period = PERIOD_SECONDS[tf]
    start = datetime.fromtimestamp(open_epoch, UTC)
    end = start + timedelta(seconds=period)
    t = start
    step = timedelta(hours=1) if period > 3600 else timedelta(seconds=period)
    while t < end:
        if market_phase(t) != "OPEN":
            return False
        t += step
    return market_phase(end - timedelta(seconds=1)) == "OPEN"


# --------------------------------------------------------------------------- expected grid
def _is_grid_open(epoch, tf):
    if tf in ("M1", "M5", "M15", "H1"):
        return epoch % PERIOD_SECONDS[tf] == 0        # native UTC grid (aligned to epoch 0 == 00:00Z)
    if tf == "H4":
        return (epoch + _NY5PM_SHIFT) % PERIOD_SECONDS["H4"] == 0    # 22/02/06/10/14/18 UTC
    return (epoch + _NY5PM_SHIFT) % PERIOD_SECONDS["D1"] == 0        # D1: 22:00 UTC daily


def _grid_opens(tf, floor_epoch, now_epoch):
    """All grid-aligned candle OPENs whose full period has closed by now (open+period <= now), from floor_epoch up."""
    p = PERIOD_SECONDS[tf]
    off = 0 if tf in ("M1", "M5", "M15", "H1") else _NY5PM_SHIFT
    first = ((floor_epoch + off + p - 1) // p) * p - off      # first grid open >= floor
    out, o = [], first
    while o + p <= now_epoch:
        out.append(o)
        o += p
    return out


def anchor_hour_ok(open_epoch, tf):
    """D1/H4 anchor validity — D1 must be 22:00; H4 on the NY-5PM grid. Native tfs are always grid-anchored here."""
    if tf == "D1":
        return datetime.fromtimestamp(open_epoch, UTC).hour == 22
    if tf == "H4":
        return datetime.fromtimestamp(open_epoch, UTC).hour in (22, 2, 6, 10, 14, 18)
    return open_epoch % PERIOD_SECONDS[tf] == 0


# --------------------------------------------------------------------------- per-timeframe classification (PURE)
def _worst(states):
    present = [s for s in states if s]
    return min(present, key=lambda s: _SEVERITY[s]) if present else "OK"


def _reason(gap_state, missing_open, invalid_anchor, sufficient, stale, phase):
    """Short deterministic explanation string for a per-tf gap_state (no interpretive/strategy content)."""
    return {
        "OK": "open-market grid complete, sufficient depth, fresh",
        "SOURCE_MISSING": "no latest and no history",
        "INVALID_ANCHOR": f"{invalid_anchor} member(s) off the expected grid anchor",
        "INSUFFICIENT_HISTORY": "history depth below required minimum",
        "GAPS_FOUND": f"{len(missing_open)} open-market slot(s) missing within retention",
        "STALE": "latest candle older than freshness threshold during open market",
        "MARKET_CLOSED": "expected weekend/closed-market window; missing slots not gaps",
        "OUT_OF_RETENTION": "missing slot(s) predate the retention floor",
    }.get(gap_state, gap_state)


def classify_timeframe(tf, *, latest, history_opens, now, latest_status=None):
    """PURE per-tf gap block. `latest` = the latest candle envelope dict (or None); `history_opens` = iterable of int
    open-epochs present in history; `now` = aware UTC. Reads nothing. Returns the per-tf contract block."""
    now = cc.normalise_utc(now)
    now_epoch = int(now.timestamp())
    p = PERIOD_SECONDS[tf]
    opens = set(int(e) for e in history_opens)
    retention_floor = now_epoch - RETENTION_DAYS[tf] * 86400
    retention_floor_dt = datetime.fromtimestamp(retention_floor, UTC)
    # source presence
    if latest is None and not opens:
        return {"timeframe": tf, "period_seconds": p, "history_key": _hist_index_key(tf), "latest_key": _latest_key(tf),
                "history_depth": 0, "min_required_depth": MIN_REQUIRED_DEPTH[tf], "sufficient_depth": False,
                "retention_policy": f"{RETENTION_DAYS[tf]}d", "retention_floor_utc": cc._fmt(retention_floor_dt),
                "oldest_open_utc": None, "newest_open_utc": None, "expected_grid_policy": EXPECTED_GRID_POLICY[tf],
                "anchor": ANCHOR[tf], "market_phase": market_phase(now), "expected_slots_open_market": 0,
                "present_slots": 0, "missing_slots": 0, "missing_open_epochs_sample": [],
                "closed_market_missing_slots": 0, "out_of_retention_slots": 0, "invalid_anchor_count": 0,
                "latest_status": "ABSENT", "gap_state": "SOURCE_MISSING", "status_reason": "no latest and no history"}
    # anchor audit (present members off the expected grid)
    invalid_anchor = sum(1 for e in opens if not anchor_hour_ok(e, tf))
    # expected grid slots (fully-closed candles up to now), classified. Grid starts a bounded few periods BEFORE the
    # retention floor so pre-floor missing slots surface as OUT_OF_RETENTION (never gaps), not silently ignored.
    grid = _grid_opens(tf, retention_floor - _OOR_LOOKBACK_PERIODS * p, now_epoch)
    expected_open, closed_missing, oor_missing, missing_open = 0, 0, 0, []
    for o in grid:
        if o < retention_floor:
            if o not in opens:
                oor_missing += 1
            continue
        if _period_fully_open(o, tf):
            expected_open += 1
            if o not in opens:
                missing_open.append(o)
        else:
            if o not in opens:
                closed_missing += 1
    present = expected_open - len(missing_open)
    depth = len(opens)
    sufficient = depth >= MIN_REQUIRED_DEPTH[tf]
    # latest staleness (market-aware; suppressed when closed or out-of-retention)
    if latest_status is None:
        latest_status = (latest.get("status") if isinstance(latest, dict) else None) or "UNKNOWN"
    stale = False
    if latest is not None:
        lts = (latest.get("data", {}) or {}).get("timestamp_utc")
        if lts:
            lopen = int(datetime.strptime(lts[:-1], cc._UTC_MS).replace(tzinfo=UTC).timestamp())
            age = now_epoch - (lopen + p)            # age since the latest candle CLOSED
            stale = age > STALE_THRESHOLD_SECONDS[tf] and market_phase(now) == "OPEN"
    # fail-closed state (worst-of severity)
    candidates = []
    if invalid_anchor:
        candidates.append("INVALID_ANCHOR")
    if not sufficient:
        candidates.append("INSUFFICIENT_HISTORY")
    if missing_open:
        candidates.append("GAPS_FOUND")
    if stale:
        candidates.append("STALE")
    if market_phase(now) == "CLOSED_WEEKEND":
        candidates.append("MARKET_CLOSED")
    if oor_missing and not candidates:
        candidates.append("OUT_OF_RETENTION")
    gap_state = _worst(candidates) if candidates else "OK"
    srt = sorted(opens)
    return {"timeframe": tf, "period_seconds": p, "history_key": _hist_index_key(tf), "latest_key": _latest_key(tf),
            "history_depth": depth, "min_required_depth": MIN_REQUIRED_DEPTH[tf], "sufficient_depth": sufficient,
            "retention_policy": f"{RETENTION_DAYS[tf]}d", "retention_floor_utc": cc._fmt(retention_floor_dt),
            "oldest_open_utc": _fmt_epoch(srt[0]) if srt else None, "newest_open_utc": _fmt_epoch(srt[-1]) if srt else None,
            "expected_grid_policy": EXPECTED_GRID_POLICY[tf], "anchor": ANCHOR[tf], "market_phase": market_phase(now),
            "expected_slots_open_market": expected_open, "present_slots": present, "missing_slots": len(missing_open),
            "missing_open_epochs_sample": [int(x) for x in missing_open[:MISSING_SAMPLE_CAP]],
            "closed_market_missing_slots": closed_missing, "out_of_retention_slots": oor_missing,
            "invalid_anchor_count": invalid_anchor, "latest_status": latest_status, "gap_state": gap_state,
            "status_reason": _reason(gap_state, missing_open, invalid_anchor, sufficient, stale, market_phase(now))}


def classify_d1_boundary(*, d1_latest, d1_history_opens, now, forward_enabled=False, forward_authorised=False):
    """PURE D1 boundary block: 22:00 anchor only / sealed 6/6 / XAU_USD / latest==history-newest / forward gate state."""
    opens = sorted(int(e) for e in d1_history_opens)
    non_22 = sum(1 for e in opens if datetime.fromtimestamp(e, UTC).hour != 22)
    sealed = False
    src, exp, cov, gap = None, 6, None, None
    latest_open = None
    if isinstance(d1_latest, dict):
        latest_open = (d1_latest.get("data", {}) or {}).get("timestamp_utc")
        try:
            if "XAUUSD" not in json.dumps(d1_latest):
                d1h.assert_sealed_complete_d1(d1_latest)
                sealed = True
        except Exception:  # noqa: BLE001 - invalid/unsealed -> not sealed (fail-closed)
            sealed = False
        d = d1_latest.get("data", {}) or {}
        src, exp, cov, gap = d.get("source_count"), d.get("expected_source_count"), d.get("source_coverage"), d.get("gap_state")
    hist_newest = _fmt_epoch(opens[-1]) if opens else None
    latest_matches = bool(latest_open and hist_newest and latest_open == hist_newest)
    latest_anchor_22 = bool(latest_open and latest_open[11:16] == "22:00")
    invalid_anchor = non_22 + (0 if latest_anchor_22 or latest_open is None else 1)
    state = "OK"
    if not latest_anchor_22 or non_22:
        state = "INVALID_ANCHOR"
    elif not sealed:
        state = "NOT_SEALED"
    elif not latest_matches:
        state = "LATEST_HISTORY_INCONSISTENT"
    return {"expected_anchor_utc": "22:00", "ny5pm_anchor": True, "latest_open_utc": latest_open,
            "history_newest_open_utc": hist_newest, "latest_matches_history_newest": latest_matches,
            "sealed_complete": sealed, "source_count": src, "expected_source_count": 6, "coverage": cov, "gap": gap,
            "invalid_anchor_count": invalid_anchor, "non_22_anchor_count": non_22,
            "forward_writer_enabled": bool(forward_enabled), "forward_writer_authorised": bool(forward_authorised),
            "weekend_d1_buckets": "NOT_EXPECTED", "d1_boundary_state": state}


# --------------------------------------------------------------------------- aggregate contract (PURE)
def build_gaps_contract(*, timeframes, d1_boundary, generated_at_utc, source="hermes:candles:XAU_USD:* (governed, read-only)"):
    """Assemble hermes:gaps:XAU_USD:v1. repair/backfill/consumer are HARD false (this surface never repairs/backfills/exposes)."""
    overall = _worst([b["gap_state"] for b in timeframes.values()])
    c = {"publisher": PUBLISHER, "schema_version": SCHEMA_VERSION, "contract_version": "v1",
         "instrument": CANONICAL_INSTRUMENT, "canonical_instrument": CANONICAL_INSTRUMENT,
         "generated_at_utc": cc._fmt(cc.normalise_utc(generated_at_utc)), "source": source,
         "calendar_source": CALENDAR_SOURCE, "consumer_live": False, "repair_executed": False,
         "backfill_executed": False, "deterministic_only": True, "overall_gap_state": overall,
         "severity_order": list(GAP_STATES), "timeframes": timeframes, "d1_boundary": d1_boundary,
         "caveats": ["read-only detection; no repair/backfill/delete", "market calendar = weekly weekend UTC; holiday calendar not modelled",
                     "weekend M15/H1 direct + H4-derived candles are legitimate closed-market facts, annotated market_phase, retained, not counted toward open-market grid"]}
    validate_gaps_contract(c)
    return c


def validate_gaps_contract(c):
    if c.get("instrument") != CANONICAL_INSTRUMENT or c.get("canonical_instrument") != CANONICAL_INSTRUMENT:
        raise ValueError("GOV-HERMES-GAPS-001: instrument must be canonical XAU_USD")
    if "XAUUSD" in json.dumps(c):
        raise ValueError("GOV-HERMES-GAPS-002: XAUUSD alias must not appear")
    for f in ("repair_executed", "backfill_executed", "consumer_live"):
        if c.get(f) is not False:
            raise ValueError(f"GOV-HERMES-GAPS-003: {f} must be hard false (read-only detection surface)")
    if c.get("overall_gap_state") not in GAP_STATES:
        raise ValueError("GOV-HERMES-GAPS-004: overall_gap_state not in fail-closed vocab")
    for tf, b in c.get("timeframes", {}).items():
        if b.get("gap_state") not in GAP_STATES:
            raise ValueError(f"GOV-HERMES-GAPS-005: {tf} gap_state not in vocab")
        if b.get("market_phase") not in MARKET_PHASES:
            raise ValueError(f"GOV-HERMES-GAPS-006: {tf} market_phase not in vocab")
    return True


# --------------------------------------------------------------------------- read-only Redis reader (NO writes)
def _latest_key(tf):
    return f"hermes:candles:{CANONICAL_INSTRUMENT}:{tf}:latest:v1"


def _hist_index_key(tf):
    return f"hermes:candles:{CANONICAL_INSTRUMENT}:{tf}:history:v1:index"


def _fmt_epoch(epoch):
    return cc._fmt(datetime.fromtimestamp(int(epoch), UTC))


def analyze_gaps(client, *, now, forward_enabled=False, forward_authorised=False):
    """READ-ONLY: reads governed candle latest/history from Redis and builds the gaps contract. Performs NO writes/deletes,
    NO SQL, NO backfill/repair. `client` is used only for GET/ZRANGE/EXISTS. Returns the contract dict (caller decides to
    publish in a LATER gated WO — this function never sets a key)."""
    tf_blocks = {}
    for tf in TIMEFRAMES:
        lraw = client.get(_latest_key(tf))
        latest = json.loads(lraw) if lraw else None
        idx = _hist_index_key(tf)
        opens = [int(e) for e in client.zrange(idx, 0, -1)] if client.exists(idx) else []
        tf_blocks[tf] = classify_timeframe(tf, latest=latest, history_opens=opens, now=now)
    d1raw = client.get(_latest_key("D1"))
    d1_latest = json.loads(d1raw) if d1raw else None
    d1_opens = [int(e) for e in client.zrange(_hist_index_key("D1"), 0, -1)] if client.exists(_hist_index_key("D1")) else []
    d1b = classify_d1_boundary(d1_latest=d1_latest, d1_history_opens=d1_opens, now=now,
                               forward_enabled=forward_enabled, forward_authorised=forward_authorised)
    return build_gaps_contract(timeframes=tf_blocks, d1_boundary=d1b, generated_at_utc=now)


# --------------------------------------------------------------------------- dark publisher (default DISABLED)
class DisabledGapsPublisher:
    """Safe no-op (DEFAULT). No Redis client, no I/O. This WO ships the surface DARK — no runtime publication."""
    enabled = False

    def status(self):
        return {"enabled": False}


class GapsPublisher:
    """ENABLED + AUTHORISED gaps publisher. Builds the read-only gaps contract from governed candle surfaces and, when
    driven by the gated runtime step, publishes it to the SINGLE key GAPS_KEY. It NEVER writes any candle/history key,
    NEVER deletes, NEVER writes SQL, NEVER launches backfill/repair, NEVER calls vendor/market_map/Falcon. The contract
    invariants consumer_live/repair_executed/backfill_executed stay HARD false (enforced by validate_gaps_contract)."""
    enabled = True

    def __init__(self, *, redis_client):
        if redis_client is None:
            raise ValueError("GOV-HERMES-GAPS-020: enabled gaps publisher requires an explicit redis client")
        self.redis_client = redis_client

    def analyze(self, *, now, forward_enabled=False, forward_authorised=False):
        return analyze_gaps(self.redis_client, now=now, forward_enabled=forward_enabled,
                            forward_authorised=forward_authorised)

    def publish(self, *, now, forward_enabled=False, forward_authorised=False):
        """Governed publication: build the read-only gaps contract (analyze_gaps) and SET exactly ONE key (GAPS_KEY).
        Re-validates before write (defence-in-depth: never publish an invalid/aliased/overclaiming contract). Persistent
        truth surface -> no TTL (parity with control-plane manifest/catalog). Writes NOTHING else; no delete/SQL/backfill/
        repair. Returns a small result dict; consumer_live/repair_executed/backfill_executed are hard false."""
        contract = self.analyze(now=now, forward_enabled=forward_enabled, forward_authorised=forward_authorised)
        validate_gaps_contract(contract)                       # fail-closed guard immediately before the single SET
        self.redis_client.set(GAPS_KEY, json.dumps(contract))  # the ONLY write this surface performs
        return {"published": 1, "key": GAPS_KEY, "overall_gap_state": contract["overall_gap_state"],
                "consumer_live": False, "repair_executed": False, "backfill_executed": False}

    def status(self):
        return {"enabled": True, "key": GAPS_KEY, "read_only": True,
                "repair_executed": False, "backfill_executed": False}


def gaps_publish_enabled():
    """Env-ONLY governed gate (no Redis client, no I/O): True iff gaps publication is ENABLED+AUTHORISED. Used by the
    runtime runner-spec assembly to decide whether to append the gaps runner WITHOUT constructing a client. Same two
    gates and same fail-closed as build_gaps_publisher_from_env: enabled-without-authorised -> SystemExit(101)."""
    from env_config import get_env_bool
    if not get_env_bool(GAPS_ENABLED_ENV, False):
        return False
    if not get_env_bool(GAPS_AUTHORISED_ENV, False):
        raise SystemExit(HALT_CODE)
    return True


def build_gaps_publisher_from_env(*, redis_client=None, redis_client_factory=None):
    """Boot entrypoint. DISABLED by default -> DisabledGapsPublisher (no client, no I/O). Enabled-without-authorised ->
    SystemExit(101). Enabled+authorised -> GapsPublisher (reads governed surfaces; publishes ONLY GAPS_KEY when its
    publish() is driven by the gated runtime step). No hidden default that activates publication; NO Redis client
    constructed when disabled."""
    from env_config import get_env_bool
    if not get_env_bool(GAPS_ENABLED_ENV, False):
        return DisabledGapsPublisher()
    if not get_env_bool(GAPS_AUTHORISED_ENV, False):
        raise SystemExit(HALT_CODE)
    client = redis_client
    if client is None:
        client = (redis_client_factory or _default_redis_client)()
    return GapsPublisher(redis_client=client)


def _default_redis_client():
    import redis
    from env_config import get_env, get_env_int
    return redis.Redis(host=get_env("HERMES_CANDLE_CANONICAL_REDIS_HOST", required=True),
                       port=get_env_int("HERMES_CANDLE_CANONICAL_REDIS_PORT", required=True),
                       db=get_env_int("HERMES_CANDLE_CANONICAL_REDIS_DB", required=True), socket_timeout=5)
