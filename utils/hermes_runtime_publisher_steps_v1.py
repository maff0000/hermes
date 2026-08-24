"""HERMES durable in-process publisher STEPS v1.
WO-HELM-HERMES-DURABLE-PUBLISHER-WIRING-MAINPY-0001.

Durable, repo-resident ports of the (previously detached /tmp dev-loop) governed publish steps, parameterised on
an INJECTED redis client so there is NO module-level redis client and NO Redis/SQL/file I/O at import. Each step:
  * builds its family publisher via the existing from_env factory (so ALL gates are preserved — disabled -> no-op,
    enabled-without-authorised -> SystemExit(101), instrument/timeframe/scope/D1 allowlists honoured),
  * reads the GOVERNED candle history/latest keys (bounded reads) via the injected client,
  * computes deterministic facts using the existing HERMES compute modules + merged contract builders (validated),
  * writes only the governed versioned keys via the injected client,
  * returns {"published": N} for fault/throughput accounting.

No fabricated values (uncomputable -> null/omitted). NO regime/risk/decision fields. NO auth. D1 stays GATED.
These steps are invoked ONLY by the in-process supervisor, which itself is DISABLED by default and refuses to
start unless HERMES_PUBLISHER_RUNTIME_OWNER=in_process (duplicate-publisher guard).
"""
from __future__ import annotations
import datetime
import json

from utils import candle_contract_v1 as cc
from utils import hermes_control_plane_v1 as cp
from utils import hermes_indicators_v1 as ind
from utils import hermes_candle_features_v1 as feat
from utils import hermes_sessions_v1 as sess
from utils import hermes_levels_v1 as lvl
from utils import indicators as ind_compute
from utils import atr_calculator
from utils import candle_features as cf
from utils import hermes_instrument_catalog_v1 as ic   # instrument-catalog runtime publisher step (gated dark)
from utils import hermes_feed_health_v1 as fh          # feed-health runtime publisher step (gated dark)
from utils import hermes_quote_tick_contract_v1 as qt  # quote runtime publisher step (gated dark)
from utils import hermes_gaps_v1 as gaps               # PH2 gaps runtime publisher step (gated dark)
from utils import hermes_backfill_status_v1 as bfs     # PH2 backfill-status runtime publisher step (gated dark)
from utils import hermes_advanced_v1_publication_gate_v1 as pgate   # central master/scope publication-eligibility gate
from utils import candle_d1_history_v1 as d1h          # D1 history depth guard + sealed-6/6 source validation

UTC = datetime.timezone.utc
INST = "XAU_USD"
LATEST_TFS = ("M1", "M5", "M15", "H1", "H4")        # D1 gated until D1 latest GREEN
INDICATOR_WINDOW = 60                                # bounded read per TF (covers EMA50 / BB20 / ADX14)
# WO-HELM-HERMES-INDICATOR-PUBLICATION-WIRING-0001: minimum depth for a SETTLED Wilder ADX(14). Below this the reused
# calculate_adx() returns a NEUTRAL fabricated (20/20/20) result, so we emit explicit null instead of publishing it.
ADX_PERIOD = 14
ADX_MIN_DEPTH = 2 * ADX_PERIOD + 1                   # 29
BOLLINGER_PERIOD = 20
EMA_LONG_PERIOD = 50
SESSION_PRIORITY = ("overlap_ldn_ny", "london", "newyork", "asia", "off_hours")


def _now():
    return datetime.datetime.now(UTC)


def _deployed_sha():
    # WO-HELM-HERMES-DEV-DEPLOYMENT-IDENTITY-AND-HOST-CONFIG-BINDING-0001:
    # deployed_sha derives from the CANONICAL build truth (the same source
    # /buildinfo reports) via the validated runtime identity. The old separate
    # deployed-SHA env variable silently defaulted and let the Redis contract
    # contradict the real deployment — that class is retired: an unresolvable
    # identity raises (fail loud), it never publishes a sentinel value.
    from utils.hermes_runtime_identity_v1 import cached_runtime_identity
    return cached_runtime_identity().source_sha


def _run_env():
    # Canonical environment identity (ENVIRONMENT + its governed RUN_ENV pair),
    # resolved and host-bound at startup. The old separately-defaulted run-env /
    # environment variables could silently mislabel PROD as dev/staging — retired.
    from utils.hermes_runtime_identity_v1 import cached_runtime_identity
    ident = cached_runtime_identity()
    return ident.run_env, ident.environment


def _redis_target(client):
    kw = getattr(getattr(client, "connection_pool", None), "connection_kwargs", {}) or {}
    return cp.redact_redis_target(kw.get("host", "redis"), kw.get("port", 6379), kw.get("db", 0))


# --------------------------------------------------------------------------- shared bounded readers
def _read_history(client, tf, n):
    """Last n CLOSED candles oldest-first from the governed history ZSET (bounded; no unbounded scan)."""
    idx = f"hermes:candles:{INST}:{tf}:history:v1:index"
    eps = [int(e) for e in client.zrevrange(idx, 0, n - 1)]
    eps.reverse()
    out = []
    for ep in eps:
        raw = client.get(f"hermes:candles:{INST}:{tf}:history:v1:{ep}")
        if raw:
            out.append(json.loads(raw)["data"])
    return out


def _freshness(client, tf):
    raw = client.get(f"hermes:candles:{INST}:{tf}:latest:v1")
    if not raw:
        return "UNKNOWN"
    st = json.loads(raw).get("status")
    return "FRESH" if st == "OK" else st


def derive_publisher_status(freshness_map):
    """WO-HELM-HERMES-DEV-STARTUP-RECOVERY-RESILIENCE-AND-HEALTH-TRUTHFIX-0001.

    Pure top-level publisher status from the per-timeframe latest-key freshness map,
    in the governed OK/WARN/FAIL vocabulary (GOV-HERMES-CP-020/040):

        every timeframe FRESH            -> "OK"
        some FRESH, some not             -> "WARN"
        no timeframe FRESH (or no map)   -> "FAIL"

    During the 2026-08-20..24 DEV halt the heartbeat published status=OK with every
    timeframe UNKNOWN — stale market data must NEVER produce an OK top-level
    publisher state. This changes ONLY operational-status truthfulness; no market
    calculation is touched.
    """
    vals = [str(v).strip().upper() for v in (freshness_map or {}).values()]
    if not vals:
        return "FAIL"
    fresh = sum(1 for v in vals if v == "FRESH")
    if fresh == len(vals):
        return "OK"
    if fresh == 0:
        return "FAIL"
    return "WARN"


# --------------------------------------------------------------------------- D1-derived surfaces (gated dark)
# WO-HELM-HERMES-D1-INDICATORS-FEATURES-LEVELS-0002 — D1 indicators/candle_features/daily-levels are DERIVED from the
# governed D1 candle HISTORY series (hermes:candles:XAU_USD:D1:history:v1) ONLY — never SQL, never market_map, never
# the unsealed live D1 latest. DARK by default: D1 is included ONLY when the publisher itself authorised D1 (D1 in
# pub.timeframes / 'daily' in pub.scopes — driven by the existing HERMES_*_D1_AUTHORISED gates) AND the D1 history is
# deep enough (>= candle_d1_history_v1.D1_MIN_DEPTH_FOR_INDICATORS). Below depth -> D1 is a governed SKIP (blocked,
# never fabricated). M1-H4 behaviour is unchanged (LATEST_TFS is never modified).
D1_TF = "D1"


def _d1_history_depth(client):
    idx = f"hermes:candles:{INST}:{D1_TF}:history:v1:index"
    return client.zcard(idx) if client.exists(idx) else 0


def _read_d1_history_validated(client, n):
    """Newest-n SEALED D1 history candles (oldest-first) from the governed D1 history ZSET, each RE-VALIDATED
    sealed-6/6 + canonical + 22:00 NY-5PM anchor + UTC before use (defence-in-depth). Raises on any non-sealed /
    mis-anchored / alias member so the caller GOVERNED-SKIPS D1 (never computes from fabricated/partial source)."""
    idx = f"hermes:candles:{INST}:{D1_TF}:history:v1:index"
    eps = [int(e) for e in client.zrevrange(idx, 0, max(0, int(n) - 1))]
    eps.reverse()
    out = []
    for ep in eps:
        raw = client.get(f"hermes:candles:{INST}:{D1_TF}:history:v1:{ep}")
        if not raw:
            continue
        env = json.loads(raw)
        d1h.assert_sealed_complete_d1(env)                 # status OK, closed, 6/6, coverage 1.0, no gap, XAU_USD/D1
        d = env["data"]
        if "XAUUSD" in json.dumps(env):
            raise ValueError("GOV-HERMES-D1DERIV-001: XAUUSD alias in D1 source (canonical XAU_USD only)")
        open_dt = datetime.datetime.strptime(d["timestamp_utc"][:-1], cc._UTC_MS).replace(tzinfo=UTC)
        if (open_dt.hour, open_dt.minute) != (22, 0):
            raise ValueError(f"GOV-HERMES-D1DERIV-002: D1 source {d['timestamp_utc']} not on 22:00 NY-5PM anchor")
        out.append(d)
    return out


def _d1_derived_ready(client):
    """Governed depth guard: True iff the D1 history series is deep enough for D1-derived surfaces (>= min depth).
    Below this, D1 is blocked (never fabricated); M1-H4 continue normally."""
    return d1h.d1_history_depth_sufficient(_d1_history_depth(client))


def _d1_tf_if_ready(pub, client):
    """(D1,) iff the publisher authorised D1 (D1 in its timeframes) AND the depth guard passes; else (). Never
    modifies the M1-H4 set."""
    return (D1_TF,) if (D1_TF in getattr(pub, "timeframes", ()) and _d1_derived_ready(client)) else ()


def _d1_daily_levels(d1_candles):
    """Deterministic PRIOR-DAY levels from sealed D1 history (newest = most recent COMPLETED day): previous-day
    high/low/close + ADR over the last 20 D1 (mean high-low). Fail-loud if no prior D1 candle is available. Pure
    OHLC facts — no regime/risk/trade interpretation."""
    if not d1_candles:
        raise ValueError("GOV-HERMES-D1DERIV-010: previous-day levels require >= 1 sealed D1 candle (none available)")
    prev = d1_candles[-1]                                   # oldest-first -> newest is the last COMPLETED day
    levels = {"previous_day_high": prev["high"], "previous_day_low": prev["low"],
              "previous_day_close": prev["close"]}
    if len(d1_candles) >= 20:                              # ADR_20 only when the source truly supports it (no fabrication)
        window = d1_candles[-20:]
        levels["adr_20"] = round(sum(c["high"] - c["low"] for c in window) / 20.0, 6)
    return levels


# =========================================================================== control-plane
def _live_tfs(client, family):
    return [tf for tf in LATEST_TFS if client.exists(f"hermes:{family}:{INST}:{tf}:v1")]


def _d1_authorised(env_name):
    """The explicit D1 authorisation gate for a derived family (env bool). No hidden default — false unless set."""
    from env_config import get_env_bool
    return get_env_bool(env_name, False)


def _d1_family_active(client, family, d1_authorised_env):
    """Manifest truth for a D1 derived surface: ACTIVE iff the D1 authorisation gate is TRUE and the live D1 key is
    present. Gate-derived AND live-state-derived — never assumed true; a residual key with the gate false is NOT
    active (no overclaim), and an authorised-but-not-yet-published surface is NOT active (conservative warm-up).
    WO-HELM-HERMES-D1-MANIFEST-TRUTH-0001."""
    return _d1_authorised(d1_authorised_env) and bool(client.exists(f"hermes:{family}:{INST}:D1:v1"))


def _daily_levels_active(client, d1_authorised_env):
    """Daily-levels manifest truth: ACTIVE iff HERMES_LEVEL_D1_AUTHORISED and the live daily levels key is present."""
    return _d1_authorised(d1_authorised_env) and bool(client.exists(lvl.levels_key(INST, "daily")))


def _d1_latest_active(client):
    """Manifest truth for candle_latest_d1: True iff hermes:candles:XAU_USD:D1:latest:v1 exists AND validates as a
    genuine live sealed D1 candle — canonical XAU_USD (no XAUUSD), status-OK, closed, complete 6/6, coverage 1.0, no
    gap (assert_sealed_complete_d1), and 22:00 NY-5PM anchored with a valid UTC timestamp. Missing/invalid/mis-anchored
    -> False (fail-closed, NO overclaim). WO-HELM-HERMES-D1-CANDLE-LATEST-MANIFEST-TRUTH-0001."""
    raw = client.get(f"hermes:candles:{INST}:D1:latest:v1")
    if not raw:
        return False
    try:
        env = json.loads(raw)
        if "XAUUSD" in json.dumps(env):
            return False
        d1h.assert_sealed_complete_d1(env)               # XAU_USD/D1, status OK, closed, 6/6, coverage 1.0, no gap
        open_dt = datetime.datetime.strptime(env["data"]["timestamp_utc"][:-1], cc._UTC_MS).replace(tzinfo=UTC)
        return (open_dt.hour, open_dt.minute) == (22, 0)
    except Exception:  # noqa: BLE001 - any malformed/unsealed/invalid D1 latest -> not active (fail-closed)
        return False


def _d1_history_active(client):
    """catalog:candles truth for D1 history_status: True iff the governed D1 history index exists with depth>0 AND its
    NEWEST member re-validates as a sealed 6/6, 22:00-anchored, canonical XAU_USD D1 candle (via _read_d1_history_validated
    -> assert_sealed_complete_d1). Missing index / empty / malformed / mis-anchored / XAUUSD -> False (fail-closed, no
    overclaim). Read-only. WO-HELM-HERMES-D1-CATALOG-CANDLES-TRUTH-0001."""
    idx = f"hermes:candles:{INST}:{D1_TF}:history:v1:index"
    if not client.exists(idx) or (client.zcard(idx) or 0) <= 0:
        return False
    try:
        return len(_read_d1_history_validated(client, 1)) >= 1   # newest member validates sealed-6/6 + anchor + no XAUUSD
    except Exception:  # noqa: BLE001 - any malformed/invalid history member -> not active (fail-closed)
        return False


def _d1_forward_writer_active():
    """catalog:candles truth for D1 forward_history_status: True iff the governed D1 history FORWARD-WRITER gates are set
    (HERMES_CANDLE_D1_HISTORY_ENABLED AND _AUTHORISED). Derived from the SAME env gates that build the live writer — a
    governed runtime-config truth source, NOT code-presence. Env read only, no client. WO-...-CATALOG-CANDLES-TRUTH-0001."""
    from env_config import get_env_bool
    return get_env_bool(d1h.D1_HISTORY_ENABLED_ENV, False) and get_env_bool(d1h.D1_HISTORY_AUTHORISED_ENV, False)


def _d1_state(client):
    return "ACTIVE" if _d1_latest_active(client) else "PENDING_FIRST_DAILY_SEAL"


def control_plane_step(client):
    """Refresh the 4 control-plane keys, reflecting whichever governed families are live (R2D2 self-listing fix:
    control_plane_active=True). Heartbeat TTL>refresh. Manifest/catalog/health persistent. No regime/risk."""
    b = cp.build_control_plane_from_env()           # SystemExit(101) if enabled-without-authorised
    if not getattr(b, "enabled", False):
        return {"published": 0}
    now = _now()
    run_env, environment = _run_env()
    sha, target = _deployed_sha(), _redis_target(client)

    # WO-HELM-HERMES-DEV-DEPLOYMENT-IDENTITY-AND-HOST-CONFIG-BINDING-0001 (§9
    # identity-consistency invariant): before overwriting, compare the identity
    # a consumer could currently read against the canonical runtime identity. A
    # material mismatch (stale keys from an older/foreign writer) is surfaced
    # LOUDLY (ERROR log + heartbeat fault counter) and then corrected by this
    # very cycle's truthful publish — never silently left contradictory.
    identity_faults = []
    try:
        from utils.hermes_runtime_identity_v1 import cached_runtime_identity, check_identity_consistency
        _ident = cached_runtime_identity()
        for _key in (cp.KEY_CONTRACT_MANIFEST, cp.KEY_PUBLISHER_HEARTBEAT):
            _raw = client.get(_key)
            if _raw:
                identity_faults += ["%s:%s" % (_key.split(":")[-2], f)
                                    for f in check_identity_consistency(_ident, json.loads(_raw))]
    except Exception:  # noqa: BLE001 — a broken pre-read must not block the truthful publish
        pass
    if identity_faults:
        import logging
        logging.getLogger("hermes.control_plane").error(
            "[DEPLOYMENT_IDENTITY_MISMATCH] published identity contradicted runtime "
            "identity and is being corrected this cycle: %s", identity_faults)
    # M1-H4 liveness (health completeness is M1-H4-based, unchanged). WO-HELM-HERMES-D1-MANIFEST-TRUTH-0001: D1 derived
    # families are reflected ACTIVE in the manifest ONLY when their D1 gate is true AND their D1 key is live (else they
    # stay in gated_families — no overclaim). LATEST_TFS is never modified, so M1-H4 manifest behaviour is unchanged.
    ind_m1h4, feat_m1h4 = _live_tfs(client, "indicators"), _live_tfs(client, "candle_features")
    ind_active, feat_active = len(ind_m1h4) == len(LATEST_TFS), len(feat_m1h4) == len(LATEST_TFS)
    ind_d1_active = _d1_family_active(client, "indicators", ind.D1_AUTHORISED_ENV)
    feat_d1_active = _d1_family_active(client, "candle_features", feat.D1_AUTHORISED_ENV)
    daily_active = _daily_levels_active(client, lvl.D1_AUTHORISED_ENV)
    ind_live = ind_m1h4 + (["D1"] if ind_d1_active else [])
    feat_live = feat_m1h4 + (["D1"] if feat_d1_active else [])
    sess_live = bool(client.exists(sess.sessions_key(INST)))
    lvl_live = [sc for sc in ("session", "intraday") if client.exists(lvl.levels_key(INST, sc))] \
        + (["daily"] if daily_active else [])

    d1_latest_active = _d1_latest_active(client)
    manifest = b.manifest(generated_at_utc=now, environment=environment, run_env=run_env,
                          deployed_sha=sha, service_identity="hermes-signal")
    if d1_latest_active:                                # WO-...-CANDLE-LATEST-MANIFEST-TRUTH-0001: reflect live D1 latest
        manifest["active_families"]["candle_latest"]["D1"] = cp.STATUS_ACTIVE
        manifest["gated_families"].pop("candle_latest_d1", None)   # no longer PENDING once the D1 latest validates live
    if ind_live:
        manifest["not_implemented_families"].pop("indicators", None)
        manifest["active_families"]["indicators"] = {tf: cp.STATUS_ACTIVE for tf in ind_live}
        if not ind_d1_active:                       # D1 stays gated ONLY while it is not live-authorised
            manifest["gated_families"]["indicators_d1"] = {"status": cp.STATUS_GATED,
                "explanation": "D1 indicators gated until D1 latest GREEN"}
    if feat_live:
        manifest["not_implemented_families"].pop("candle_features", None)
        manifest["active_families"]["candle_features"] = {tf: cp.STATUS_ACTIVE for tf in feat_live}
        if not feat_d1_active:
            manifest["gated_families"]["candle_features_d1"] = {"status": cp.STATUS_GATED,
                "explanation": "D1 candle_features gated until D1 latest GREEN"}
    if sess_live:
        manifest["not_implemented_families"].pop("sessions", None)
        manifest["active_families"]["sessions"] = cp.STATUS_ACTIVE
    if lvl_live:
        manifest["active_families"]["levels"] = {sc: cp.STATUS_ACTIVE for sc in lvl_live}
        if not daily_active:
            manifest["gated_families"]["levels_d1"] = {"status": cp.STATUS_GATED,
                "explanation": "D1-derived daily/weekly levels (PDH/PDL/ADR) gated until D1 latest GREEN"}
    if ind_live or feat_live or sess_live or lvl_live or d1_latest_active:
        cp.validate_manifest(manifest)

    lp_fresh = {tf: _freshness(client, tf) for tf in LATEST_TFS}
    # WO-HELM-HERMES-DEV-STARTUP-RECOVERY-RESILIENCE-AND-HEALTH-TRUTHFIX-0001: the
    # top-level heartbeat status is DERIVED from the per-timeframe freshness truth
    # (OK only when every timeframe is FRESH), never hardcoded OK.
    pub_status = derive_publisher_status(lp_fresh)
    heartbeat = b.heartbeat(updated_at_utc=now, generated_at_utc=now, service_identity="hermes-signal",
                            deployed_sha=sha, run_env=run_env, redis_target=target, status=pub_status,
                            enabled_publishers=["canonical_seam", "h4_producer", "forward_history", "d1_producer",
                                                "control_plane", "indicator_publisher", "candle_feature_publisher",
                                                "sessions_levels_publisher"],
                            active_timeframes=list(LATEST_TFS), last_publish_utc={}, latest_key_freshness=lp_fresh,
                            history_forward_state="ACTIVE", d1_state=_d1_state(client),
                            fault_counters_summary=(
                                {"deployment_identity_mismatch": len(identity_faults)}
                                if identity_faults else {}),
                            skip_counters_summary={})
    catalog = b.candle_catalog(generated_at_utc=now)
    # WO-HELM-HERMES-D1-CATALOG-CANDLES-TRUTH-0001 — reflect live D1 candle truth in catalog:candles. The builder is pure
    # (defaults D1 latest=PENDING / history+forward=BLOCKED); override to ACTIVE ONLY from validated runtime truth
    # (validated sealed D1 latest / validated D1 history newest member + depth>0 / forward-writer gates). No hardcoding;
    # missing/invalid -> stays PENDING/BLOCKED (no overclaim). Re-validated with the relaxed validator before publish.
    d1e = catalog["timeframes"]["D1"]
    d1_latest_ok, d1_hist_ok, d1_fwd_ok = _d1_latest_active(client), _d1_history_active(client), _d1_forward_writer_active()
    if d1_latest_ok:
        d1e["latest_status"] = cp.STATUS_ACTIVE
    if d1_hist_ok:
        d1e["history_status"] = cp.STATUS_ACTIVE
    if d1_fwd_ok:
        d1e["forward_history_status"] = cp.STATUS_ACTIVE
    if d1_latest_ok and d1_hist_ok and d1_fwd_ok:
        d1e["notes"] = None                             # no more "armed/awaiting/blocked" caveat once fully live+truthful
    cp.validate_candle_catalog(catalog)                 # re-validate with the relaxed D1 latest/history rules
    # WO-HELM-HERMES-DEV-STARTUP-RECOVERY-RESILIENCE-AND-HEALTH-TRUTHFIX-0001:
    # hermes:health:v1 overall_status follows the same derived freshness truth as
    # the heartbeat — stale market data can never publish an OK health summary.
    health = b.health(generated_at_utc=now, overall_status=pub_status,
                      control_plane_active=True, indicators_built=bool(ind_live),
                      candle_features_built=bool(feat_live), candle_features_active=feat_active)
    if ind_active:
        health["per_family_health"]["indicators"] = cp.STATUS_ACTIVE
        if "indicators" in health["missing_but_expected_families"]:
            health["missing_but_expected_families"].remove("indicators")
    if sess_live:
        health["per_family_health"]["sessions"] = cp.STATUS_ACTIVE
        if "sessions" in health["missing_but_expected_families"]:
            health["missing_but_expected_families"].remove("sessions")
    if lvl_live:
        health["per_family_health"]["levels"] = cp.STATUS_ACTIVE
        health["per_family_health"]["levels_d1"] = cp.STATUS_ACTIVE if daily_active else cp.STATUS_GATED
    cp.validate_health(health)

    client.set(cp.KEY_CONTRACT_MANIFEST, json.dumps(manifest))
    client.set(cp.KEY_CATALOG_CANDLES, json.dumps(catalog))
    client.set(cp.KEY_HEALTH, json.dumps(health))
    client.set(cp.KEY_PUBLISHER_HEARTBEAT, json.dumps(heartbeat), ex=cp.HEARTBEAT_TTL_SECONDS)
    return {"published": 4}


# =========================================================================== indicators
def _compute_indicators(candles):
    closes = [c["close"] for c in candles]
    out = {}
    # ---- existing indicators (behaviour PRESERVED exactly) ----
    if len(closes) >= 12:
        out["ema_12"] = round(ind_compute.calculate_ema(closes, 12), 6)
    if len(closes) >= 26:
        out["ema_26"] = round(ind_compute.calculate_ema(closes, 26), 6)
    if len(closes) >= 15:
        out["rsi_14"] = round(ind_compute.calculate_rsi(closes, 14), 4)
    atr = atr_calculator.calculate_atr(candles, 14)
    out["atr_14"] = round(atr, 6) if atr is not None else None
    # ---- WO-...-INDICATOR-PUBLICATION-WIRING-0001: EMA50 + Bollinger(20,2) + ADX(+DI/-DI) ----
    # Extends the existing EMA calc set; reuses utils.indicators; explicit null when depth insufficient (never fabricated).
    out["ema_50"] = round(ind_compute.calculate_ema(closes, EMA_LONG_PERIOD), 6) if len(closes) >= EMA_LONG_PERIOD else None
    # Bollinger Bands (20, 2) — reuse calculate_bollinger_bands; guard its current-price fabrication branch (< period).
    if len(closes) >= BOLLINGER_PERIOD:
        bb = ind_compute.calculate_bollinger_bands(closes, BOLLINGER_PERIOD, 2.0)
        out["bollinger_upper_20_2"] = round(bb.upper, 6)
        out["bollinger_middle_20_2"] = round(bb.middle, 6)
        out["bollinger_lower_20_2"] = round(bb.lower, 6)
    else:
        out["bollinger_upper_20_2"] = out["bollinger_middle_20_2"] = out["bollinger_lower_20_2"] = None
    # ADX(14) with +DI/-DI — reuse calculate_adx (Wilder); require 2*period+1 so the neutral (20/20/20) fabrication path
    # is never published; below that emit explicit null.
    if len(candles) >= ADX_MIN_DEPTH:
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        adxr = ind_compute.calculate_adx(highs, lows, closes, ADX_PERIOD)
        out["adx_14"] = round(adxr.adx, 4)
        out["adx_plus_di_14"] = round(adxr.plus_di, 4)
        out["adx_minus_di_14"] = round(adxr.minus_di, 4)
    else:
        out["adx_14"] = out["adx_plus_di_14"] = out["adx_minus_di_14"] = None
    return out


def indicator_step(client):
    pub = ind.build_indicator_publisher_from_env()  # SystemExit(101) if enabled-without-authorised
    if not getattr(pub, "enabled", False):
        return {"published": 0}
    now, n = _now(), 0
    if not pgate.decide(INST, "indicator", now=now).permitted:   # pilot preserved; expansion fail-closed
        return {"published": 0}
    for tf in LATEST_TFS + _d1_tf_if_ready(pub, client):   # D1 appended ONLY when authorised + depth>=min (dark default)
        candles = _read_d1_history_validated(client, INDICATOR_WINDOW) if tf == D1_TF \
            else _read_history(client, tf, INDICATOR_WINDOW)
        if len(candles) < 15:
            continue
        inds = _compute_indicators(candles)
        if not any(v is not None for v in inds.values()):
            continue
        value_open = candles[-1]["timestamp_utc"]
        vot = datetime.datetime.strptime(value_open[:-1], cc._UTC_MS).replace(tzinfo=UTC)
        payload = pub.build(instrument=INST, timeframe=tf, generated_at_utc=now, value_open_time_utc=vot,
                            indicators=inds, freshness_state=_freshness(client, tf))
        key = pub.key(INST, tf)
        if not (key.endswith(":v1") and "XAUUSD" not in key):
            continue
        client.set(key, json.dumps(payload))
        n += 1
    return {"published": n}


# =========================================================================== candle_features
def _features_for(latest, prev, config):
    geo = cf.candle_geometry(latest["open"], latest["high"], latest["low"], latest["close"])
    feats = {
        "body_high": geo["body_high"], "body_low": geo["body_low"], "body_size": geo["body_size"],
        "range_size": geo["total_range"], "upper_wick_size": geo["upper_wick_size"],
        "lower_wick_size": geo["lower_wick_size"], "candle_direction": latest.get("candle_direction"),
        "body_to_range_ratio": geo["body_to_range_ratio"], "upper_wick_to_range_ratio": geo["upper_wick_ratio"],
        "lower_wick_to_range_ratio": geo["lower_wick_ratio"], "close_position_in_range": geo["close_position_in_range"],
    }
    if prev:
        ph, pl = prev["high"], prev["low"]
        pbh, pbl = max(prev["open"], prev["close"]), min(prev["open"], prev["close"])
        feats["inside_bar"] = bool(latest["high"] <= ph and latest["low"] >= pl)
        feats["outside_bar"] = bool(latest["high"] >= ph and latest["low"] <= pl)
        feats["engulfing"] = bool(geo["body_high"] >= pbh and geo["body_low"] <= pbl and geo["body_size"] > 0)
    method_config = {}
    if config is not None:
        cls = cf.classify(geo, config)
        for k in ("doji", "full_body", "long_wick", "pin_bar"):
            if k in cls:
                feats[k] = bool(cls[k])
        method_config = config.provenance() if hasattr(config, "provenance") else {}
    return feats, method_config


def _load_feature_config():
    """Governed classification config (read-only). None -> geometry-only (no fabrication of threshold flags)."""
    try:
        import pymysql
        from env_config import get_db_config

        def get_conn():
            c = get_db_config()
            return pymysql.connect(host=c["host"], port=c["port"], user=c["user"],
                                   password=c["password"], database=c["database"])

        return cf.load_config(get_conn)
    except Exception:
        return None


def candle_feature_step(client, _config_cache={}):
    pub = feat.build_candle_feature_publisher_from_env()  # SystemExit(101) if enabled-without-authorised
    if not getattr(pub, "enabled", False):
        return {"published": 0}
    if "cfg" not in _config_cache:
        _config_cache["cfg"] = _load_feature_config()
    config = _config_cache["cfg"]
    now, n = _now(), 0
    for tf in LATEST_TFS + _d1_tf_if_ready(pub, client):   # D1 appended ONLY when authorised + depth>=min (dark default)
        if tf == D1_TF:
            d1c = _read_d1_history_validated(client, 2)     # newest 2 sealed D1 (oldest-first) -> prev, latest
            if len(d1c) < 2:
                raise ValueError("GOV-HERMES-D1DERIV-011: D1 candle_features require a prior sealed D1 candle")
            c2 = [d1c[-1], d1c[-2]]                          # [latest, prev] to match the M1-H4 ordering below
        else:
            idx = f"hermes:candles:{INST}:{tf}:history:v1:index"
            eps = [int(e) for e in client.zrevrange(idx, 0, 1)]
            c2 = []
            for ep in eps:
                raw = client.get(f"hermes:candles:{INST}:{tf}:history:v1:{ep}")
                if raw:
                    c2.append(json.loads(raw)["data"])
        if not c2:
            continue
        latest, prev = c2[0], (c2[1] if len(c2) > 1 else None)
        feats, mcfg = _features_for(latest, prev, config)
        vot = datetime.datetime.strptime(latest["timestamp_utc"][:-1], cc._UTC_MS).replace(tzinfo=UTC)
        payload = pub.build(instrument=INST, timeframe=tf, generated_at_utc=now,
                            source_candle_key=f"hermes:candles:{INST}:{tf}:latest:v1",
                            source_candle_open_time_utc=vot, features=feats, method_config=mcfg,
                            freshness_state=_freshness(client, tf))
        key = pub.key(INST, tf)
        if not (key.endswith(":v1") and "XAUUSD" not in key):
            continue
        client.set(key, json.dumps(payload))
        n += 1
    return {"published": n}


# =========================================================================== sessions + levels
def _td_to_time(v):
    s = int(v.total_seconds()) if isinstance(v, datetime.timedelta) else (v.hour * 3600 + v.minute * 60)
    return (s // 3600) % 24, (s % 3600) // 60


def _in_window(now_hm, start_hm, end_hm):
    n = now_hm[0] * 60 + now_hm[1]; s = start_hm[0] * 60 + start_hm[1]; e = end_hm[0] * 60 + end_hm[1]
    if e == 0:
        e = 24 * 60
    return s <= n < e if s < e else (n >= s or n < e)


def _load_windows():
    import pymysql
    from env_config import get_db_config
    c = get_db_config()
    cn = pymysql.connect(host=c["host"], port=c["port"], user=c["user"], password=c["password"], database=c["database"])
    cu = cn.cursor(pymysql.cursors.DictCursor)
    cu.execute("SELECT session_name,start_time_utc,end_time_utc FROM trading_windows")
    rows = cu.fetchall(); cn.close()
    return {row["session_name"]: (_td_to_time(row["start_time_utc"]), _td_to_time(row["end_time_utc"])) for row in rows}


def _current_session(now, windows):
    hm = (now.hour, now.minute)
    for name in SESSION_PRIORITY:
        if name in windows and _in_window(hm, *windows[name]):
            return name
    return "off_hours"


def _today_h1(client, now):
    idx = f"hermes:candles:{INST}:H1:history:v1:index"
    eps = [int(e) for e in client.zrevrange(idx, 0, 30)]
    day0 = int(datetime.datetime(now.year, now.month, now.day, tzinfo=UTC).timestamp())
    out = []
    for ep in eps:
        if ep >= day0:
            raw = client.get(f"hermes:candles:{INST}:H1:history:v1:{ep}")
            if raw:
                out.append((ep, json.loads(raw)["data"]))
    return sorted(out)


def _hl_mid(candles):
    if not candles:
        return None, None, None
    hi = max(c["high"] for _, c in candles); lo = min(c["low"] for _, c in candles)
    return round(hi, 6), round(lo, 6), round((hi + lo) / 2, 6)


def _session_levels(windows, candles):
    out = {}
    for name in ("asia", "london", "newyork"):
        if name not in windows:
            continue
        s, e = windows[name]
        sel = [(ep, c) for ep, c in candles
               if _in_window((datetime.datetime.fromtimestamp(ep, UTC).hour,
                              datetime.datetime.fromtimestamp(ep, UTC).minute), s, e)]
        hi, lo, mid = _hl_mid(sel)
        if hi is not None:
            out[f"{name}_high"] = hi; out[f"{name}_low"] = lo; out[f"{name}_midpoint"] = mid
    return out


def _last_h1_close_utc(candles):
    """as-of close of the last CLOSED H1 candle used (open + 1h); None if no candles."""
    if not candles:
        return None
    ep = candles[-1][0]
    return datetime.datetime.fromtimestamp(ep, UTC) + datetime.timedelta(hours=1)


def sessions_levels_step(client, _win_cache={}):
    sp = sess.build_session_publisher_from_env()    # SystemExit(101) if enabled-without-authorised
    lp = lvl.build_level_publisher_from_env()        # daily/weekly D1-derived scope gated until D1 GREEN
    if not (getattr(sp, "enabled", False) and getattr(lp, "enabled", False)):
        return {"published": 0}
    if "w" not in _win_cache:
        _win_cache["w"] = _load_windows()
    windows = _win_cache["w"]
    now = _now()
    fresh = _freshness(client, "H1")
    cs = _current_session(now, windows)
    sessions_obj = {n: {"open_time_utc": "%02d:%02d" % windows[n][0], "close_time_utc": "%02d:%02d" % windows[n][1]}
                    for n in windows}
    sp_payload = sp.build(instrument=INST, generated_at_utc=now, current_session=cs, sessions=sessions_obj,
                          freshness_state=fresh)
    if cs in windows:
        sp_payload["current_session_open_utc"] = "%02d:%02d" % windows[cs][0]
        sp_payload["current_session_close_utc"] = "%02d:%02d" % windows[cs][1]
    sess.validate_session_contract(sp_payload)
    client.set(sess.sessions_key(INST), json.dumps(sp_payload))
    n = 1

    candles = _today_h1(client, now)
    as_of = _last_h1_close_utc(candles)
    slv = _session_levels(windows, candles)
    if slv:
        p = lp.build(instrument=INST, scope="session", generated_at_utc=now, levels=slv,
                     source_inputs=["trading_windows (governed)", "hermes:candles:XAU_USD:H1 (governed)"],
                     freshness_state=fresh, as_of_candle_close_utc=as_of)
        p["gated_d1_dependencies"] = ["PDH", "PDL", "ADR_20", "daily_levels", "weekly_levels"]
        lvl.validate_level_contract(p)
        client.set(lvl.levels_key(INST, "session"), json.dumps(p)); n += 1
    hi, lo, mid = _hl_mid(candles)
    if hi is not None:
        p = lp.build(instrument=INST, scope="intraday", generated_at_utc=now,
                     levels={"intraday_high": hi, "intraday_low": lo, "intraday_midpoint": mid},
                     source_inputs=["hermes:candles:XAU_USD:H1 (governed, today)"],
                     freshness_state=fresh, as_of_candle_close_utc=as_of)
        p["gated_d1_dependencies"] = ["PDH", "PDL", "ADR_20", "daily_levels", "weekly_levels"]
        lvl.validate_level_contract(p)
        client.set(lvl.levels_key(INST, "intraday"), json.dumps(p)); n += 1
    # WO-HELM-HERMES-D1-INDICATORS-FEATURES-LEVELS-0002 — daily D1-derived levels (PDH/PDL/PDC + ADR_20) published
    # ONLY when the level publisher authorised the 'daily' scope (HERMES_LEVEL_D1_AUTHORISED) AND the D1 history is
    # deep enough. DARK by default (daily not in lp.scopes). Source = validated sealed D1 history ONLY; fail-loud on
    # missing prior. as-of = the last sealed D1 close (D1-derived level_source_granularity).
    if "daily" in getattr(lp, "scopes", ()) and _d1_derived_ready(client):
        d1c = _read_d1_history_validated(client, 20)        # bounded newest 20 sealed D1 (oldest-first)
        daily = _d1_daily_levels(d1c)
        d1_open = datetime.datetime.strptime(d1c[-1]["timestamp_utc"][:-1], cc._UTC_MS).replace(tzinfo=UTC)
        d1_close = d1_open + datetime.timedelta(seconds=cc.TF_SECONDS["D1"])   # last sealed D1 close (as-of)
        p = lp.build(instrument=INST, scope="daily", generated_at_utc=now, levels=daily,
                     source_inputs=["hermes:candles:XAU_USD:D1:history:v1 (governed, sealed 6/6)"],
                     freshness_state=fresh, d1_latest_green=True,
                     as_of_candle_close_utc=d1_close)   # DATETIME (build_level_contract/_assert_utc requires it; not a str)
        lvl.validate_level_contract(p)
        client.set(lvl.levels_key(INST, "daily"), json.dumps(p)); n += 1
    return {"published": n}


# =========================================================================== instrument_catalog (gated dark)
# WO-HELM-HERMES-INSTRUMENT-CATALOG-RUNTIME-PUBLISHER-WIRING-0001. PR #63 supervisor-compatible runner step.
def _catalog_runtime_published_surfaces():
    """The surfaces THIS process is actually runtime-publishing, for the catalog self-description. Always includes
    instrument_catalog (this step IS its publication). Includes feed_health / quote when their runtime-publisher gate
    is enabled (the SAME gates default_runner_specs uses to add those runners), and tick when the LIVE canonical tick
    EMITTER gate is enabled (the SAME gate the stream-loop emitter uses) — so the catalog truth tracks the running
    supervisor + emitter atomically, no split-brain. tick is emitter-based (per-tick stream loop), not a supervisor
    runner, but the gate-driven catalog coupling is identical. Env read only, no Redis I/O; a mis-gated surface
    (enabled-without-authorised, or enabled+authorised without valid canonical scope) fails loud."""
    from utils import tick_live_emitter_v1 as tle
    surfaces = ["instrument_catalog"]
    if getattr(fh.build_feed_health_publisher_from_env(), "enabled", False):   # SystemExit(101) if enabled-without-authorised
        surfaces.append("feed_health")
    if getattr(qt.build_quote_publisher_from_env(), "enabled", False):         # SystemExit(101)/fail-closed on scope
        surfaces.append("quote")
    if tle.tick_live_gate_enabled():                                           # env-only; SystemExit(101)/fail-closed on scope
        surfaces.append("tick")
    return surfaces


def instrument_catalog_step(client):
    """Governed instrument-catalog publisher step. Gate-first: NO-OP when the catalog gate is disabled (no client
    touch, no write); enabled-without-authorised -> SystemExit(101) (fail-closed). When enabled+authorised, builds
    the governed catalog contract (canonical XAU_USD; alias-only XAUUSD; dark/pending-runtime-deployment markers;
    D1 default PENDING — never inferred ACTIVE; no regime/risk/signal/decision fields) and writes ONLY
    hermes:instrument_catalog:XAU_USD:v1 (TTL). Publishes deterministic HERMES discovery facts only."""
    pub = ic.build_instrument_catalog_publisher_from_env()   # SystemExit(101) if enabled-without-authorised
    if not getattr(pub, "enabled", False):
        return {"published": 0}
    now = _now()
    # This step IS the runtime publication of the catalog self-surface -> mark it runtime_published. feed_health is
    # marked runtime_published too WHEN its runner gate is enabled (catalog truth tracks the live supervisor). This
    # does NOT imply consumer cutover / trusted-live-plane (a separate authorisation); quote/tick stay dark.
    payload = pub.build(instrument=ic.CANONICAL_INSTRUMENT, generated_at_utc=now, source_name=pub.source_name,
                        runtime_published_surfaces=_catalog_runtime_published_surfaces())
    payload["published_at_utc"] = ic._utc(now)               # published_at where governed
    ic.validate_instrument_catalog_contract(payload)         # re-validate: forbidden-key + no :XAUUSD: output scan
    key = pub.key(ic.CANONICAL_INSTRUMENT)                   # hermes:instrument_catalog:XAU_USD:v1
    if not (key.endswith(":v1") and "XAUUSD" not in key):
        return {"published": 0}
    client.set(key, json.dumps(payload), ex=ic.TTL_SECONDS)
    return {"published": 1}


# =========================================================================== feed_health (gated dark)
# WO-HELM-HERMES-FEED-HEALTH-RUNTIME-PUBLISHER-WIRING-0001. PR #63 supervisor-compatible runner step; INERT by
# default (NOT wired live in this WO — a separate activate WO turns it on). Reuses the EXISTING PR #66 feed-health
# contract/gates/snapshot verbatim (no new contract/key invented).
_FH_TFS = ("M1", "M5", "M15", "H1", "H4", "D1")             # D1 stays GATED (never RED) until D1 latest GREEN


def feed_health_step(client):
    """Governed feed-health publisher step. Gate-first: NO-OP when the feed-health gate is disabled (no client
    touch, no write); enabled-without-authorised -> SystemExit(101) (fail-closed); migration 025 not applied ->
    DisabledFeedHealthPublisher (MIGRATION_REQUIRED, no write). When enabled+authorised+ready, iterates the registry
    feed-health-selected instruments (XAU pilot ACTIVE; 7 new NOT_ENABLED via the capability flag) and, per instrument,
    reads HERMES-owned surfaces READ-ONLY via collect_feed_health_snapshot (GET only), builds the governed contract
    (D1 GATED never RED; deterministic ingestion-health facts; no regime/risk/signal/decision fields; no XAUUSD) and
    writes ONLY hermes:feed_health:<instrument>:v1 (TTL). Per-instrument keys never collide; zero-selection -> no write."""
    pub = fh.build_feed_health_publisher_from_env()          # SystemExit(101) if enabled-without-authorised; fail-closed on scope
    if not getattr(pub, "enabled", False):
        return {"published": 0}
    now = _now()
    published, keys = 0, []
    for instrument in sorted(pub.allowed_instruments):
        if not pgate.decide(instrument, "feed_health", now=now, records=getattr(pub, "records", None)).permitted:   # pilot preserved; expansion fail-closed
            continue
        d1_green = bool(client.exists(f"hermes:candles:{instrument}:D1:latest:v1"))   # D1 GATED unless a genuine D1 latest
        snap = fh.collect_feed_health_snapshot(client, instrument=instrument, timeframes=_FH_TFS, generated_at_utc=now,
                                               source_name=pub.source_name, d1_latest_green=d1_green)  # READ-ONLY (GET only)
        payload = pub.build(**snap)
        payload["published_at_utc"] = fh._utc(now)           # published_at where governed
        fh.validate_feed_health_contract(payload)            # re-validate: forbidden-key scan + governed vocab
        key = pub.key(instrument)                            # hermes:feed_health:<instrument>:v1
        if not (key.endswith(":v1") and "XAUUSD" not in key):
            continue
        client.set(key, json.dumps(payload), ex=fh.TTL_SECONDS)
        keys.append(key)
        published += 1
    return {"published": published, "keys": keys}


# =========================================================================== quote (gated dark)
# WO-HELM-HERMES-QUOTE-RUNTIME-PUBLISHER-WIRING-0001. PR #63 supervisor-compatible runner step; INERT by default
# (NOT wired live in this WO — a separate activate WO turns it on). Reuses the EXISTING PR #68 quote contract/gates
# verbatim (no new contract/key). Governed source = the EXISTING tick surface hermes:ticks:XAU_USD:latest:v1,
# mapped via reconcile_quote_from_tick_envelope. Stale/market-closed is HONEST (present+fresh -> GREEN;
# present+stale -> AMBER_STALE; absent -> bid/ask null -> RED_MISSING/UNAVAILABLE). Never a fabricated/fake quote.
def quote_step(client):
    """Governed quote publisher step. Gate-first: NO-OP when the quote gate is disabled (no client touch, no write);
    enabled-without-authorised -> SystemExit(101) (fail-closed); enabled+authorised without a valid canonical
    XAU_USD scope -> fail-closed (GOV-HERMES-QT-020/021). When enabled+authorised, reads the EXISTING governed tick
    surface READ-ONLY (GET only) and reconciles it into the governed quote contract (canonical XAU_USD; no XAUUSD;
    deterministic bid/ask/mid/spread; honest stale/market-closed status; no regime/risk/signal/decision fields),
    writing ONLY hermes:quote:XAU_USD:v1 (TTL). A missing/stale tick source stays EXPLICIT (RED_MISSING/AMBER_STALE),
    never a faked live quote and never a false GREEN."""
    pub = qt.build_quote_publisher_from_env()               # SystemExit(101) if enabled-without-authorised; fail-closed on scope
    if not getattr(pub, "enabled", False):
        return {"published": 0}
    now = _now()
    tick_key = qt.governed_tick_surface_reference()["key"]   # hermes:ticks:XAU_USD:latest:v1 (EXISTING; referenced, not rebuilt)
    raw = client.get(tick_key)                               # READ-ONLY (GET only)
    tick_env = json.loads(raw) if raw else {}                # absent tick source -> empty envelope -> honest RED_MISSING (no fabrication)
    payload = qt.reconcile_quote_from_tick_envelope(tick_env, generated_at_utc=now, source_name=pub.source_name)
    payload["published_at_utc"] = qt._utc(now)               # published_at where governed
    qt.validate_quote_contract(payload)                     # re-validate: forbidden-key scan + governed vocab + arithmetic
    key = pub.key(qt.CANONICAL_INSTRUMENT)                   # hermes:quote:XAU_USD:v1
    if not (key.endswith(":v1") and "XAUUSD" not in key):
        return {"published": 0}
    client.set(key, json.dumps(payload), ex=qt.QUOTE_TTL_SECONDS)
    return {"published": 1}


def gaps_step(client):
    """PH2 gaps recovery-surface publisher step (governed, DARK by default). Gate-first: NO-OP when the gaps gate is
    disabled (no write); enabled-without-authorised -> SystemExit(101) (fail-closed). When enabled+authorised, reads the
    governed candle latest/history surfaces READ-ONLY (GET/EXISTS/ZRANGE only) and publishes ONLY the single aggregate
    key gaps.GAPS_KEY (hermes:gaps:XAU_USD:v1) with the read-only gap-detection contract. It NEVER writes any candle/
    history key, NEVER deletes, NEVER writes SQL, NEVER launches backfill/repair, NEVER touches vendor/market_map/Falcon;
    consumer_live/repair_executed/backfill_executed stay hard false. The D1 forward-writer gate state is REPORTED (read
    from the same governed env gates as the live writer), not acted on."""
    pub = gaps.build_gaps_publisher_from_env(redis_client=client)   # SystemExit(101) if enabled-without-authorised
    if not getattr(pub, "enabled", False):
        return {"published": 0}
    from env_config import get_env_bool                             # env read only; report the two D1 gates independently
    fe = get_env_bool(d1h.D1_HISTORY_ENABLED_ENV, False)
    fa = get_env_bool(d1h.D1_HISTORY_AUTHORISED_ENV, False)
    return pub.publish(now=_now(), forward_enabled=fe, forward_authorised=fa)


def backfill_status_step(client):
    """PH2 backfill-status recovery-surface publisher step (governed, DARK by default). Gate-first: NO-OP when the
    backfill-status gate is disabled (no write); enabled-without-authorised -> SystemExit(101) (fail-closed). When
    enabled+authorised, reads the LIVE gaps truth surface READ-ONLY (GET hermes:gaps:XAU_USD:v1) + read-only env gate
    telemetry and publishes ONLY the single aggregate key bfs.BACKFILL_STATUS_KEY (hermes:backfill:status:XAU_USD:v1) with the
    status-only contract. It NEVER writes the gaps key or any candle/history key, NEVER deletes, NEVER writes SQL, NEVER
    invokes the D1 seed/backfill engine, NEVER launches backfill/repair, NEVER touches vendor/market_map/Falcon;
    execution_enabled/backfill_executed/repair_executed/consumer_live stay hard false; active_job/completed_pct null."""
    pub = bfs.build_backfill_status_publisher_from_env(redis_client=client)   # SystemExit(101) if enabled-without-authorised
    if not getattr(pub, "enabled", False):
        return {"published": 0}
    return pub.publish(now=_now())
