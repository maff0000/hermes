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

UTC = datetime.timezone.utc
INST = "XAU_USD"
LATEST_TFS = ("M1", "M5", "M15", "H1", "H4")        # D1 gated until D1 latest GREEN
INDICATOR_WINDOW = 60                                # bounded read per TF
SESSION_PRIORITY = ("overlap_ldn_ny", "london", "newyork", "asia", "off_hours")


def _now():
    return datetime.datetime.now(UTC)


def _deployed_sha():
    from env_config import get_env
    return get_env("HERMES_DEPLOYED_SHA", default="unknown") or "unknown"


def _run_env():
    from env_config import get_env
    return (get_env("HERMES_RUN_ENV", default="STAGING") or "STAGING"), (get_env("HERMES_ENVIRONMENT", default="dev") or "dev")


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


# =========================================================================== control-plane
def _live_tfs(client, family):
    return [tf for tf in LATEST_TFS if client.exists(f"hermes:{family}:{INST}:{tf}:v1")]


def _d1_state(client):
    return "ACTIVE" if client.exists(f"hermes:candles:{INST}:D1:latest:v1") else "PENDING_FIRST_DAILY_SEAL"


def control_plane_step(client):
    """Refresh the 4 control-plane keys, reflecting whichever governed families are live (R2D2 self-listing fix:
    control_plane_active=True). Heartbeat TTL>refresh. Manifest/catalog/health persistent. No regime/risk."""
    b = cp.build_control_plane_from_env()           # SystemExit(101) if enabled-without-authorised
    if not getattr(b, "enabled", False):
        return {"published": 0}
    now = _now()
    run_env, environment = _run_env()
    sha, target = _deployed_sha(), _redis_target(client)
    ind_live, feat_live = _live_tfs(client, "indicators"), _live_tfs(client, "candle_features")
    ind_active, feat_active = len(ind_live) == len(LATEST_TFS), len(feat_live) == len(LATEST_TFS)
    sess_live = bool(client.exists(sess.sessions_key(INST)))
    lvl_live = [sc for sc in ("session", "intraday") if client.exists(lvl.levels_key(INST, sc))]

    manifest = b.manifest(generated_at_utc=now, environment=environment, run_env=run_env,
                          deployed_sha=sha, service_identity="hermes-signal")
    if ind_live:
        manifest["not_implemented_families"].pop("indicators", None)
        manifest["active_families"]["indicators"] = {tf: cp.STATUS_ACTIVE for tf in ind_live}
        manifest["gated_families"]["indicators_d1"] = {"status": cp.STATUS_GATED,
            "explanation": "D1 indicators gated until D1 latest GREEN"}
    if feat_live:
        manifest["not_implemented_families"].pop("candle_features", None)
        manifest["active_families"]["candle_features"] = {tf: cp.STATUS_ACTIVE for tf in feat_live}
        manifest["gated_families"]["candle_features_d1"] = {"status": cp.STATUS_GATED,
            "explanation": "D1 candle_features gated until D1 latest GREEN"}
    if sess_live:
        manifest["not_implemented_families"].pop("sessions", None)
        manifest["active_families"]["sessions"] = cp.STATUS_ACTIVE
    if lvl_live:
        manifest["active_families"]["levels"] = {sc: cp.STATUS_ACTIVE for sc in lvl_live}
        manifest["gated_families"]["levels_d1"] = {"status": cp.STATUS_GATED,
            "explanation": "D1-derived daily/weekly levels (PDH/PDL/ADR) gated until D1 latest GREEN"}
    if ind_live or feat_live or sess_live or lvl_live:
        cp.validate_manifest(manifest)

    lp_fresh = {tf: _freshness(client, tf) for tf in LATEST_TFS}
    heartbeat = b.heartbeat(updated_at_utc=now, generated_at_utc=now, service_identity="hermes-signal",
                            deployed_sha=sha, run_env=run_env, redis_target=target, status="OK",
                            enabled_publishers=["canonical_seam", "h4_producer", "forward_history", "d1_producer",
                                                "control_plane", "indicator_publisher", "candle_feature_publisher",
                                                "sessions_levels_publisher"],
                            active_timeframes=list(LATEST_TFS), last_publish_utc={}, latest_key_freshness=lp_fresh,
                            history_forward_state="ACTIVE", d1_state=_d1_state(client),
                            fault_counters_summary={}, skip_counters_summary={})
    catalog = b.candle_catalog(generated_at_utc=now)
    health = b.health(generated_at_utc=now, control_plane_active=True, indicators_built=bool(ind_live),
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
        health["per_family_health"]["levels_d1"] = cp.STATUS_GATED
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
    if len(closes) >= 12:
        out["ema_12"] = round(ind_compute.calculate_ema(closes, 12), 6)
    if len(closes) >= 26:
        out["ema_26"] = round(ind_compute.calculate_ema(closes, 26), 6)
    if len(closes) >= 15:
        out["rsi_14"] = round(ind_compute.calculate_rsi(closes, 14), 4)
    atr = atr_calculator.calculate_atr(candles, 14)
    out["atr_14"] = round(atr, 6) if atr is not None else None
    return out


def indicator_step(client):
    pub = ind.build_indicator_publisher_from_env()  # SystemExit(101) if enabled-without-authorised
    if not getattr(pub, "enabled", False):
        return {"published": 0}
    now, n = _now(), 0
    for tf in LATEST_TFS:
        candles = _read_history(client, tf, INDICATOR_WINDOW)
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
    for tf in LATEST_TFS:
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
    return {"published": n}


# =========================================================================== instrument_catalog (gated dark)
# WO-HELM-HERMES-INSTRUMENT-CATALOG-RUNTIME-PUBLISHER-WIRING-0001. PR #63 supervisor-compatible runner step.
def _catalog_runtime_published_surfaces():
    """The surfaces THIS process is actually runtime-publishing, for the catalog self-description. Always includes
    instrument_catalog (this step IS its publication). Includes feed_health when its runtime-publisher gate is
    enabled — the SAME gate default_runner_specs uses to add the feed_health runner (so the catalog truth tracks the
    running supervisor atomically). quote/tick are NOT included (no active runtime publisher; they stay dark). Env
    read only, no Redis I/O; a mis-gated feed-health (enabled-without-authorised) fails loud, exactly as the
    supervisor build does."""
    surfaces = ["instrument_catalog"]
    if getattr(fh.build_feed_health_publisher_from_env(), "enabled", False):   # SystemExit(101) if enabled-without-authorised
        surfaces.append("feed_health")
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
    touch, no write); enabled-without-authorised -> SystemExit(101) (fail-closed); enabled+authorised without a
    valid canonical XAU_USD scope -> fail-closed (GOV-HERMES-FH-020/021). When enabled+authorised, reads HERMES-owned
    surfaces READ-ONLY via collect_feed_health_snapshot (GET only), builds the governed feed-health contract
    (canonical XAU_USD; no XAUUSD; D1 GATED never RED; deterministic ingestion-health facts; no regime/risk/signal/
    decision fields) and writes ONLY hermes:feed_health:XAU_USD:v1 (TTL)."""
    pub = fh.build_feed_health_publisher_from_env()          # SystemExit(101) if enabled-without-authorised; fail-closed on scope
    if not getattr(pub, "enabled", False):
        return {"published": 0}
    now = _now()
    d1_green = bool(client.exists(f"hermes:candles:{INST}:D1:latest:v1"))   # D1 GATED unless a genuine D1 latest exists
    snap = fh.collect_feed_health_snapshot(client, timeframes=_FH_TFS, generated_at_utc=now,
                                           source_name=pub.source_name, d1_latest_green=d1_green)  # READ-ONLY (GET only)
    payload = pub.build(**snap)
    payload["published_at_utc"] = fh._utc(now)               # published_at where governed
    fh.validate_feed_health_contract(payload)                # re-validate: forbidden-key scan + governed vocab
    key = pub.key(fh.CANONICAL_INSTRUMENT)                   # hermes:feed_health:XAU_USD:v1
    if not (key.endswith(":v1") and "XAUUSD" not in key):
        return {"published": 0}
    client.set(key, json.dumps(payload), ex=fh.TTL_SECONDS)
    return {"published": 1}


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
