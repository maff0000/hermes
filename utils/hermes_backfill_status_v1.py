"""HERMES PH2 recovery surface #2 — hermes:backfill:status:XAU_USD:v1 STATUS-ONLY telemetry v1.
WO-HELM-HERMES-PH2-BACKFILL-STATUS-SURFACE-0001.

Deterministic, read-only recovery/backfill TELEMETRY & READINESS only. This surface NEVER executes backfill, NEVER repairs
data, NEVER writes/deletes any Redis candle/history key, NEVER writes SQL, NEVER pulls a vendor, NEVER touches market_map or
Falcon, and carries no strategy/risk/signal/trade semantics. It reads the LIVE gaps truth surface (hermes:gaps:XAU_USD:v1) as
its primary input and describes, per timeframe + D1, how much recovery is needed and whether a governed backfill PATH exists —
without ever acting on it.

Hard invariants (validate_backfill_status_contract): instrument=XAU_USD; no XAUUSD; consumer_live/execution_enabled/
backfill_executed/repair_executed are HARD false; active_job/completed_pct are null (no governed job runner is wired to this
surface); overall_status is fail-closed and NEVER OK (this surface will not claim recovery complete without a governed evidence
source, which does not exist in this WO). Missing/invalid gaps input -> GAPS_SURFACE_MISSING (fail-closed), never OK.

DARK by default: build_backfill_status_publisher_from_env() -> DisabledBackfillStatusPublisher (no I/O). The builder is PURE
(data-injected). Runtime publication (BackfillStatusPublisher.publish -> a single SET of BACKFILL_STATUS_KEY, wired as the gated
backfill_status runtime step in WO-HELM-HERMES-PH2-BACKFILL-STATUS-PUBLISH-WIRING-0001) stays inert until
HERMES_BACKFILL_STATUS_PUBLISH_ENABLED and HERMES_BACKFILL_STATUS_PUBLISH_AUTHORISED are both set; enabling activation is a SEPARATE later WO.
"""
from __future__ import annotations
import json

from utils import candle_contract_v1 as cc
from utils import hermes_gaps_v1 as gaps     # primary input surface + shared timeframes/depth/retention (NOT instrument)
from utils import hermes_advanced_v1_selection_v1 as sel   # registry selection seam (backfill-status instruments + keys)

SCHEMA_VERSION = "v1"
PUBLISHER = "HERMES"
TIMEFRAMES = gaps.TIMEFRAMES                        # ("M1","M5","M15","H1","H4","D1")
_ALIAS_DENY = ("XAUUSD",)                           # inbound alias only; never an output key
# Per-instrument keys via the seam: sel.backfill_status_key(instrument) / sel.gaps_key(instrument). Backfill-status
# projection follows the gap-detection capability (BACKFILL_STATUS_FLAG). One SET per selected instrument; XAU byte-identical.
def backfill_status_key(instrument):
    return sel.backfill_status_key(instrument)

# Fail-closed status vocab (WO-suggested order; R2D2 may amend). overall_status is NEVER OK in this WO.
STATUS_ORDER = ("SOURCE_MISSING", "GAPS_SURFACE_MISSING", "GAPS_FOUND", "READY_FOR_BACKFILL_DESIGN", "IDLE",
                "INSUFFICIENT_HISTORY", "STALE", "RATE_LIMITED", "STALLED", "BLOCKED", "OK")

NO_EXECUTOR_REASON = "NO_BACKFILL_EXECUTOR_CONFIGURED"

# --- governed gate NAMES this surface REPORTS as telemetry (it never reads-to-act; values are injected read-only). ---
# D1 has a governed seed/backfill engine (utils/candle_d1_history_seed_backfill_v1.py) -> a real, gated, dry-run-by-default
# recovery PATH exists for D1. M1-H4 have NO governed gap-closing executor wired to this surface.
D1_BACKFILL_ENABLED_ENV = "HERMES_D1_HISTORY_BACKFILL_ENABLED"
D1_BACKFILL_AUTHORISED_ENV = "HERMES_D1_HISTORY_BACKFILL_AUTHORISED"
D1_BACKFILL_DRY_RUN_ENV = "HERMES_D1_HISTORY_BACKFILL_DRY_RUN"
# forward-writer gates (context only; forward writers are NOT backfill executors).
D1_FWD_ENABLED_ENV = gaps.D1_FWD_ENABLED_ENV        # HERMES_CANDLE_D1_HISTORY_ENABLED
D1_FWD_AUTHORISED_ENV = gaps.D1_FWD_AUTHORISED_ENV  # HERMES_CANDLE_D1_HISTORY_AUTHORISED
CANDLE_HISTORY_FORWARD_ENABLED_ENV = "HERMES_CANDLE_HISTORY_FORWARD_ENABLED"
CANDLE_HISTORY_FORWARD_AUTHORISED_ENV = "HERMES_CANDLE_HISTORY_FORWARD_AUTHORISED"

# This surface's OWN dark activation gates (unset by default -> DisabledBackfillStatusPublisher).
STATUS_ENABLED_ENV = "HERMES_BACKFILL_STATUS_PUBLISH_ENABLED"
STATUS_AUTHORISED_ENV = "HERMES_BACKFILL_STATUS_PUBLISH_AUTHORISED"
HALT_CODE = 101


# --------------------------------------------------------------------------- static recovery topology (governed truth)
def _tf_backfill_topology(tf, gate_values):
    """Per-tf governed backfill PATH availability + the gate names/values a future executor would consult. This surface
    NEVER executes any of it. gate_values = injected dict {env_name: value} (read-only telemetry; missing -> None)."""
    def g(name):
        return gate_values.get(name)
    if tf == "D1":
        return {"backfill_path_available": True,      # a governed D1 seed/backfill engine exists (gated, dry-run default)
                "backfill_gates": {"enabled_env": D1_BACKFILL_ENABLED_ENV, "authorised_env": D1_BACKFILL_AUTHORISED_ENV,
                                   "dry_run_env": D1_BACKFILL_DRY_RUN_ENV, "enabled": bool(g(D1_BACKFILL_ENABLED_ENV)),
                                   "authorised": bool(g(D1_BACKFILL_AUTHORISED_ENV)),
                                   "dry_run": True if g(D1_BACKFILL_DRY_RUN_ENV) is None else bool(g(D1_BACKFILL_DRY_RUN_ENV))},
                "forward_writer_gates": {"enabled_env": D1_FWD_ENABLED_ENV, "authorised_env": D1_FWD_AUTHORISED_ENV,
                                         "enabled": bool(g(D1_FWD_ENABLED_ENV)), "authorised": bool(g(D1_FWD_AUTHORISED_ENV))}}
    # M1-H4: no governed gap-closing executor wired to this surface
    return {"backfill_path_available": False, "backfill_gates": None,
            "forward_writer_gates": {"enabled_env": CANDLE_HISTORY_FORWARD_ENABLED_ENV,
                                     "authorised_env": CANDLE_HISTORY_FORWARD_AUTHORISED_ENV,
                                     "enabled": bool(g(CANDLE_HISTORY_FORWARD_ENABLED_ENV)),
                                     "authorised": bool(g(CANDLE_HISTORY_FORWARD_AUTHORISED_ENV))}}


def _tf_status(gap_state):
    """Map a gaps per-tf gap_state to a recovery status. NEVER turns a recovery-needed gaps state into OK/IDLE silently:
    open-market GAPS_FOUND -> READY_FOR_BACKFILL_DESIGN; closed/out-of-retention -> IDLE (nothing recoverable now)."""
    if gap_state == "OK":
        return "OK"
    if gap_state in ("MARKET_CLOSED", "OUT_OF_RETENTION"):
        return "IDLE"                                 # not a live recoverable gap
    if gap_state == "GAPS_FOUND":
        return "READY_FOR_BACKFILL_DESIGN"
    if gap_state in ("SOURCE_MISSING", "INSUFFICIENT_HISTORY", "STALE"):
        return gap_state                              # mirror the fail-closed state
    return "BLOCKED"                                  # INVALID_ANCHOR / anything unknown -> BLOCKED (never OK)


def _overall_status(gaps_contract):
    """Fail-closed overall interpretation. Missing/invalid gaps -> GAPS_SURFACE_MISSING. Never OK in this WO (no governed
    completion evidence). gaps OK -> IDLE (nothing to recover, no job running)."""
    if not isinstance(gaps_contract, dict):
        return "GAPS_SURFACE_MISSING"
    try:
        gaps.validate_gaps_contract(gaps_contract)
    except Exception:  # noqa: BLE001 - invalid gaps input is fail-closed, not trusted
        return "GAPS_SURFACE_MISSING"
    ov = gaps_contract.get("overall_gap_state")
    if ov == "OK":
        return "IDLE"
    if ov in ("MARKET_CLOSED", "OUT_OF_RETENTION"):
        return "IDLE"
    if ov == "GAPS_FOUND":
        return "READY_FOR_BACKFILL_DESIGN"
    if ov in ("SOURCE_MISSING", "INSUFFICIENT_HISTORY", "STALE"):
        return ov
    return "BLOCKED"


# --------------------------------------------------------------------------- pure aggregate builder
def build_backfill_status_contract(*, instrument, gaps_contract, now, gate_values=None, markers=None):
    """Assemble hermes:backfill:status:<instrument>:v1 from that instrument's LIVE gaps contract (or None). Generic per
    instrument (XAU byte-identical). PURE: no I/O, no env, no execution. gate_values = injected {env_name: value} telemetry
    (read-only). markers = injected {tf: last_backfill_run_marker}. Invariants execution_enabled/backfill_executed/
    repair_executed/consumer_live are HARD false; active_job/completed_pct null."""
    if not instrument or instrument in _ALIAS_DENY:
        raise ValueError(f"GOV-HERMES-BFS-001: instrument {instrument!r} not a canonical symbol (alias/empty rejected)")
    gaps_source_key = sel.gaps_key(instrument)
    gate_values = gate_values or {}
    markers = markers or {}
    valid_gaps = isinstance(gaps_contract, dict)
    if valid_gaps:
        try:
            gaps.validate_gaps_contract(gaps_contract)
        except Exception:  # noqa: BLE001
            valid_gaps = False
    overall = _overall_status(gaps_contract if valid_gaps else None)

    tf_blocks = {}
    gaps_tfs = (gaps_contract.get("timeframes", {}) if valid_gaps else {})
    for tf in TIMEFRAMES:
        gb = gaps_tfs.get(tf) or {}
        topo = _tf_backfill_topology(tf, gate_values)
        gap_state = gb.get("gap_state")
        st = _tf_status(gap_state) if gap_state is not None else ("GAPS_SURFACE_MISSING" if not valid_gaps else "SOURCE_MISSING")
        needs = st in ("READY_FOR_BACKFILL_DESIGN", "SOURCE_MISSING", "INSUFFICIENT_HISTORY", "STALE", "GAPS_FOUND")
        blocked = NO_EXECUTOR_REASON if (needs and not topo["backfill_path_available"]) else None
        tf_blocks[tf] = {"timeframe": tf, "gap_state": gap_state,
                         "history_depth": gb.get("history_depth"), "min_required_depth": gaps.MIN_REQUIRED_DEPTH[tf],
                         "sufficient": gb.get("sufficient_depth"), "retention_policy": f"{gaps.RETENTION_DAYS[tf]}d",
                         "backfill_path_available": topo["backfill_path_available"], "backfill_gates": topo["backfill_gates"],
                         "forward_writer_gates": topo["forward_writer_gates"],
                         "last_backfill_run_marker": markers.get(tf), "status": st, "blocked_reason": blocked}

    d1b = (gaps_contract.get("d1_boundary", {}) if valid_gaps else {})
    d1_gb = gaps_tfs.get("D1") or {}
    d1_topo = _tf_backfill_topology("D1", gate_values)
    d1 = {"history_depth": d1_gb.get("history_depth"), "min_required_depth": gaps.MIN_REQUIRED_DEPTH["D1"],
          "latest_open_utc": d1b.get("latest_open_utc"), "history_newest_open_utc": d1b.get("history_newest_open_utc"),
          "d1_boundary_state": d1b.get("d1_boundary_state"),
          "forward_writer_enabled": bool(d1b.get("forward_writer_enabled")),
          "forward_writer_authorised": bool(d1b.get("forward_writer_authorised")),
          "backfill_path_available": d1_topo["backfill_path_available"],
          "status": tf_blocks["D1"]["status"]}

    overall_blocked = NO_EXECUTOR_REASON if overall in ("READY_FOR_BACKFILL_DESIGN", "SOURCE_MISSING",
                                                        "INSUFFICIENT_HISTORY", "STALE") else None
    c = {"publisher": PUBLISHER, "schema_version": SCHEMA_VERSION, "contract_version": "v1",
         "instrument": instrument, "canonical_instrument": instrument,
         "generated_at_utc": cc._fmt(cc.normalise_utc(now)), "source": f"{gaps_source_key} (governed, read-only)",
         "consumer_live": False, "execution_enabled": False, "backfill_executed": False, "repair_executed": False,
         "deterministic_only": True, "status_order": list(STATUS_ORDER), "overall_status": overall,
         "active_job": None, "last_completed_job": None, "blocked_reason": overall_blocked,
         "rate_limit_tokens": None, "completed_pct": None, "timeframes": tf_blocks, "d1": d1,
         "gaps_source": {"key": gaps_source_key, "present": bool(valid_gaps),
                         "overall_gap_state": (gaps_contract.get("overall_gap_state") if valid_gaps else None),
                         "generated_at_utc": (gaps_contract.get("generated_at_utc") if valid_gaps else None)},
         "caveats": ["status-only telemetry; this surface NEVER executes backfill/repair, NEVER writes/deletes candle/history",
                     "no governed backfill executor is wired to this surface; recovery is DESIGN-stage (execution_enabled=false)",
                     "D1 has a governed seed/backfill engine (gated, dry-run default); M1-H4 have no gap-closing executor",
                     "overall_status is fail-closed and never OK without a governed completion-evidence source (none in this WO)"]}
    validate_backfill_status_contract(c)
    return c


def validate_backfill_status_contract(c):
    inst = c.get("instrument")
    if not inst or inst in _ALIAS_DENY or c.get("canonical_instrument") != inst:
        raise ValueError("GOV-HERMES-BFS-001: instrument must be a canonical symbol (no alias/empty; canonical==instrument)")
    if "XAUUSD" in json.dumps(c):
        raise ValueError("GOV-HERMES-BFS-002: XAUUSD alias must not appear")
    for f in ("consumer_live", "execution_enabled", "backfill_executed", "repair_executed"):
        if c.get(f) is not False:
            raise ValueError(f"GOV-HERMES-BFS-003: {f} must be hard false (status-only surface, no execution)")
    for f in ("active_job", "completed_pct"):
        if c.get(f) is not None:
            raise ValueError(f"GOV-HERMES-BFS-004: {f} must be null (no governed job runner wired to this surface)")
    if c.get("overall_status") not in STATUS_ORDER:
        raise ValueError("GOV-HERMES-BFS-005: overall_status not in fail-closed vocab")
    if c.get("overall_status") == "OK":
        raise ValueError("GOV-HERMES-BFS-006: overall_status must not be OK without a governed completion-evidence source")
    for tf, b in c.get("timeframes", {}).items():
        if b.get("status") not in STATUS_ORDER:
            raise ValueError(f"GOV-HERMES-BFS-007: {tf} status not in vocab")
    return True


# --------------------------------------------------------------------------- read-only reader (NO writes)
def _read_env_gate_values():
    """Read-only env snapshot of the governed gate VALUES this surface REPORTS (never acts on). Env read only, no client."""
    from env_config import get_env_bool
    names = (D1_BACKFILL_ENABLED_ENV, D1_BACKFILL_AUTHORISED_ENV, D1_BACKFILL_DRY_RUN_ENV, D1_FWD_ENABLED_ENV,
             D1_FWD_AUTHORISED_ENV, CANDLE_HISTORY_FORWARD_ENABLED_ENV, CANDLE_HISTORY_FORWARD_AUTHORISED_ENV)
    return {n: get_env_bool(n, False) for n in names}


def analyze_backfill_status(client, *, instrument, now, gate_values=None):
    """READ-ONLY: GET that instrument's live gaps key (sel.gaps_key(instrument)) and build its status contract. Performs NO
    writes/deletes, NO SQL, NO backfill/repair, NO vendor pull. `client` is used ONLY for GET. Every key is instrument-scoped
    so per-instrument analysis cannot collide. Returns the contract dict; this function never sets a key."""
    raw = client.get(sel.gaps_key(instrument))
    gaps_contract = None
    if raw:
        try:
            gaps_contract = json.loads(raw)
        except Exception:  # noqa: BLE001 - unparseable gaps input is fail-closed -> GAPS_SURFACE_MISSING
            gaps_contract = None
    gv = gate_values if gate_values is not None else _read_env_gate_values()
    return build_backfill_status_contract(instrument=instrument, gaps_contract=gaps_contract, now=now, gate_values=gv)


# --------------------------------------------------------------------------- dark publisher (default DISABLED, NO SET path)
class DisabledBackfillStatusPublisher:
    """Safe no-op (DEFAULT). No Redis client, no I/O. This WO ships the surface DARK and INERT — no runtime publication."""
    enabled = False

    def status(self):
        return {"enabled": False}


class BackfillStatusPublisher:
    """ENABLED + AUTHORISED status publisher. For each registry backfill-status instrument (projection follows the
    gap-detection capability) it builds that instrument's read-only status contract from its governed gaps surface and
    publishes it to sel.backfill_status_key(instrument) (one SET per instrument; XAU byte-identical). It NEVER writes any
    gaps/candle/history key, NEVER deletes, NEVER writes SQL, NEVER invokes the D1 seed/backfill engine, NEVER launches
    backfill/repair, NEVER pulls a vendor, NEVER touches market_map/Falcon. Invariants execution_enabled/backfill_executed/
    repair_executed/consumer_live stay HARD false; active_job/completed_pct null. Instrument authority is the registry
    selection seam; zero selected instruments is a valid no-publication state."""
    enabled = True

    def __init__(self, *, redis_client, records=None):
        if redis_client is None:
            raise ValueError("GOV-HERMES-BFS-020: enabled backfill-status publisher requires an explicit redis client")
        self.redis_client = redis_client
        # Default-load through the registry loader's module attribute (single fail-closed source, patchable in tests).
        if records is None:
            from utils import hermes_instrument_registry_v1 as reg
            records = reg.load_from_db()
        self.instruments = tuple(sel.backfill_status_instruments(records))

    def analyze(self, *, instrument, now, gate_values=None):
        return analyze_backfill_status(self.redis_client, instrument=instrument, now=now, gate_values=gate_values)

    def publish(self, *, now, gate_values=None):
        """Governed publication: for each selected instrument, build its read-only status contract (analyze -> GET that
        instrument's gaps key only) and SET exactly ONE key sel.backfill_status_key(instrument). Re-validates before every
        write (defence-in-depth: never publish an invalid/aliased/overclaiming contract; overall_status=OK is rejected). No
        TTL. Writes NOTHING else; no gaps-key write, no delete, no SQL, no backfill/repair, no vendor/market_map/Falcon.
        Per-instrument keys never collide. Zero selected -> published:0."""
        results = {}
        for instrument in self.instruments:
            contract = self.analyze(instrument=instrument, now=now, gate_values=gate_values)
            validate_backfill_status_contract(contract)           # fail-closed guard immediately before this instrument's SET
            key = backfill_status_key(instrument)
            self.redis_client.set(key, json.dumps(contract))      # the ONLY write this surface performs (one per instrument)
            results[instrument] = {"key": key, "overall_status": contract["overall_status"]}
        return {"published": len(results), "keys": [r["key"] for r in results.values()],
                "instruments": list(results), "per_instrument": results,
                "consumer_live": False, "execution_enabled": False, "backfill_executed": False, "repair_executed": False}

    def status(self):
        return {"enabled": True, "instruments": list(self.instruments),
                "keys": [backfill_status_key(i) for i in self.instruments], "read_only": True,
                "execution_enabled": False, "backfill_executed": False, "repair_executed": False}


def backfill_status_publish_enabled():
    """Env-ONLY governed gate (no Redis client, no I/O): True iff backfill-status publication is ENABLED+AUTHORISED. Used by
    the runtime runner-spec assembly to decide whether to append the runner WITHOUT constructing a client. Same two gates and
    same fail-closed as build_backfill_status_publisher_from_env: enabled-without-authorised -> SystemExit(101)."""
    from env_config import get_env_bool
    if not get_env_bool(STATUS_ENABLED_ENV, False):
        return False
    if not get_env_bool(STATUS_AUTHORISED_ENV, False):
        raise SystemExit(HALT_CODE)
    return True


def build_backfill_status_publisher_from_env(*, redis_client=None, redis_client_factory=None):
    """Boot entrypoint. DISABLED by default -> DisabledBackfillStatusPublisher (no client, no I/O). Enabled-without-authorised
    -> SystemExit(101). Enabled+authorised -> BackfillStatusPublisher (reads the governed gaps surface; publishes ONLY
    BACKFILL_STATUS_KEY when its publish() is driven by the gated runtime step). No hidden default that activates publication;
    NO Redis client constructed when disabled."""
    from env_config import get_env_bool
    if not get_env_bool(STATUS_ENABLED_ENV, False):
        return DisabledBackfillStatusPublisher()
    if not get_env_bool(STATUS_AUTHORISED_ENV, False):
        raise SystemExit(HALT_CODE)
    client = redis_client
    if client is None:
        client = (redis_client_factory or _default_redis_client)()
    return BackfillStatusPublisher(redis_client=client)


def _default_redis_client():
    import redis
    from env_config import get_env, get_env_int
    return redis.Redis(host=get_env("HERMES_CANDLE_CANONICAL_REDIS_HOST", required=True),
                       port=get_env_int("HERMES_CANDLE_CANONICAL_REDIS_PORT", required=True),
                       db=get_env_int("HERMES_CANDLE_CANONICAL_REDIS_DB", required=True), socket_timeout=5)
