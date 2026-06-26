"""HERMES runtime candle-forward emit seam — disabled-by-default; INERT or governed dev-SHADOW.

WO-HELM-HERMES-CANDLE-FORWARD-RUNTIME-WIRE-INERT-0001 (inert seam) +
WO-HELM-HERMES-CANDLE-FORWARD-SHADOW-WRITER-WIRE-DEV-0001 (dev shadow writer wiring).

Makes the HERMES runtime structurally aware of the governed candle-forward foundation
(utils/candle_contract_v1, utils/candle_publisher_v1). Default DISABLED -> DisabledCandleEmitter
(no sink, no write). When explicitly enabled, an explicit sink mode is REQUIRED (no hidden default):

  HERMES_CANDLE_FORWARD_SINK:
    'none' / 'inert'        -> governed NoWriteCandleSink (no-write inert seam)
    'shadow'               -> dev SHADOW writer: SerializingCandleShadowWriter -> dev Redis 6380,
                              writes ONLY hermes:shadow:candles:{instrument}:{M1|M5|M15|H1}:latest:v1
    'canonical'/'live'/'prod' -> FAIL LOUD (canonical publish is a separate gated WO)

DIRECT-NATIVE shadow grid (WO-...-GOLD-MTF-CANDLE-CONTRACT-EXTEND-0001, extending the original
R2D2 M5/H1 ruling): timeframes M1, M5, M15, H1 (source_count=expected=1, coverage=1.0,
DIRECT_FROM_SOURCE, NONE_DIRECT, source_policy_epoch=DIRECT_NATIVE_V1). Unsupported runtime
timeframes (D1/H4/D) are SKIPPED with reason UNSUPPORTED_TIMEFRAME — never silently remapped,
never derived from stale SQL. Instruments are canonicalised (XAUUSD -> XAU_USD); only the canonical
id is published, never the alias, never both.
H4 derivation and D anchor ratification are deferred to later WOs. Canonical stays dark; no Proteus,
no legacy candle pubsub output, no shared-code import. ALL timestamps UTC.

This module wires the writer path; it does NOT enable it (no live .env flags set here).
"""
import logging
from datetime import datetime, timezone

from utils import candle_publisher_v1 as cp
from utils import candle_contract_v1 as cc

# Failure visibility: emit faults were previously swallowed into in-process counters only. Log them
# (rate-limited) so a future silent failure is observable in the journal (stdlib WARNING -> stderr).
_LOG = logging.getLogger("hermes.candle_forward")
_FAIL_LOG_EVERY = 50   # log 1st occurrence per reason, then every Nth, with suppressed count

FAULT_NO_SINK = "GOV-CANDLE-FWD-SEAM-001"          # enabled without explicit sink mode
FAULT_WRITE_FORBIDDEN = "GOV-CANDLE-FWD-SEAM-002"  # live/prod (or unknown) sink requested
FAULT_SHADOW_CLIENT = "GOV-CANDLE-FWD-SEAM-003"    # shadow enabled but no redis client constructed
FAULT_CANONICAL_CLIENT = "GOV-CANDLE-FWD-SEAM-005" # canonical enabled but no redis client constructed
FAULT_CANONICAL_ALLOWLIST = "GOV-CANDLE-FWD-SEAM-007"  # canonical enabled but allowlist missing/empty/invalid
REASON_NOT_ALLOWLISTED = "INSTRUMENT_NOT_ALLOWLISTED"
CANONICAL_ALLOWLIST_ENV = "HERMES_CANDLE_CANONICAL_INSTRUMENTS"

ALLOWED_INERT_SINKS = ("none", "inert")
SHADOW_SINK = "shadow"
CANONICAL_SINK = "canonical"                        # the ONLY governed canonical selector
# 'live'/'prod' imply production intent and are NOT accepted selectors — use 'canonical' with explicit
# governed dev config (host/port/db + publish_enabled + publish_authorised).
FORBIDDEN_CANONICAL_ALIASES = ("live", "prod")

# DIRECT-NATIVE shadow grid (GOLD-MTF extension of the R2D2 ruling). Everything else (D1/H4/D) is
# skipped UNSUPPORTED_TIMEFRAME — never remapped, never derived from stale SQL.
SUPPORTED_TF = ("M1", "M5", "M15", "H1")          # DIRECT-NATIVE grid (the seam's direct emit path)
REASON_UNSUPPORTED_TF = "UNSUPPORTED_TIMEFRAME"
# H4 is a DERIVED timeframe — published ONLY via the governed derived-H4 producer
# (candle_h4_publish_wire_v1), NEVER as a stale direct candle through this seam's direct emit path.
DERIVED_TF = ("H4",)
REASON_DERIVED_PATH_ONLY = "H4_DERIVED_PATH_ONLY"
DIRECT_NATIVE_EPOCH = "DIRECT_NATIVE_V1"

# Broker-alias -> canonical instrument id. Publish ONLY the canonical form; NEVER dual-publish the
# alias. Extend deliberately (one entry per proven alias) — no fuzzy normalisation.
INSTRUMENT_ALIASES = {"XAUUSD": "XAU_USD"}


def canonical_instrument(instrument):
    """Map a known broker alias to its canonical instrument id (e.g. XAUUSD -> XAU_USD). Unknown ids
    pass through unchanged. The alias form is never published and never dual-written."""
    return INSTRUMENT_ALIASES.get(instrument, instrument)


def parse_canonical_allowlist(raw):
    """Parse the governed canonical instrument allowlist (comma-separated) into a frozenset of CANONICAL
    instrument ids. FAIL-CLOSED: a missing (None) or empty/whitespace-only allowlist raises — canonical
    publishing must never fan out to all instruments by default. Alias entries (e.g. XAUUSD) are
    canonicalised to XAU_USD for matching, so the set holds canonical ids only and an alias can never
    become an output key. Used to gate which instruments may publish canonical candle keys."""
    if raw is None:
        raise ValueError(f"{FAULT_CANONICAL_ALLOWLIST}: {CANONICAL_ALLOWLIST_ENV} is required when canonical "
                         "publishing is enabled (fail-closed — no default fan-out to all instruments)")
    items = [x.strip() for x in str(raw).split(",") if x.strip()]
    if not items:
        raise ValueError(f"{FAULT_CANONICAL_ALLOWLIST}: {CANONICAL_ALLOWLIST_ENV} is empty (fail-closed)")
    return frozenset(canonical_instrument(x) for x in items)


def _disabled_config():
    return cp.CandlePublisherConfig(
        publish_enabled=False, publish_authorised=False,
        shadow_publish_enabled=False, shadow_authorised=False,
        namespace="hermes", contract_version="v1")


def _tf_name(candle):
    tf = getattr(candle, "timeframe", None)
    return tf.name if hasattr(tf, "name") else str(tf)


def runtime_candle_to_contract(candle, *, generated_at_utc):
    """Map a runtime Candle (DIRECT-NATIVE M1/M5/M15/H1 only) to a candle_contract_v1 envelope.
    Caller MUST have verified the timeframe is supported. complete=False -> FORMING (never OK)."""
    tf = _tf_name(candle)
    if tf not in SUPPORTED_TF:
        raise ValueError(f"{REASON_UNSUPPORTED_TF}: {tf} is not a DIRECT-NATIVE shadow timeframe")
    return cc.build_candle_contract(
        instrument=canonical_instrument(candle.instrument), timeframe=tf, timestamp_utc=candle.timestamp,
        ohlc={"open": candle.open, "high": candle.high, "low": candle.low,
              "close": candle.close, "volume": getattr(candle, "volume", 0)},
        is_closed=bool(getattr(candle, "complete", True)),
        generated_at_utc=generated_at_utc, source_timeframe=tf,
        source_count=1, expected_source_count=1,
        derivation=cc.DERIVATION_DIRECT, derivation_policy=cc.DERIVATION_POLICY_DIRECT,
        source_policy_epoch=DIRECT_NATIVE_EPOCH, market_open=True)


class InertCandleForwardSeam:
    """Structurally-wired but inert. Holds a governed no-write sink; emit() never writes."""

    def __init__(self, sink):
        self.sink = sink
        self.enabled = False
        self.write_mode = cp.WRITE_MODE_INERT

    def emit(self, candle=None, *, generated_at_utc=None, **_):
        return {"emitted": False, "wrote": False, "reason": "CANDLE_FORWARD_INERT"}

    def status(self):
        return {"enabled": False, "write_mode": self.write_mode, "sink": type(self.sink).__name__}


class ShadowCandleForwardSeam:
    """Dev-SHADOW writer seam. Builds a governed candle envelope for DIRECT-NATIVE M5/H1 and writes a
    JSON-serialised payload to hermes:shadow:candles:* on dev Redis ONLY. Unsupported timeframes are
    skipped (UNSUPPORTED_TIMEFRAME). No canonical key, no Redis 6379, no legacy pubsub. Observability
    counters are surfaced; writer errors are never silently swallowed."""

    def __init__(self, writer):
        if not isinstance(writer, cp.SerializingCandleShadowWriter):
            raise ValueError("GOV-CANDLE-FWD-SEAM-004: ShadowCandleForwardSeam requires a "
                             "SerializingCandleShadowWriter (the only governed real-client write path)")
        self.writer = writer
        self.enabled = True
        self.write_mode = cp.WRITE_MODE_SHADOW
        self.metrics = {"candles_shadow_published": {}, "candles_skipped_unsupported_tf": {},
                        "candle_validate_fail": 0, "candle_emit_fail": 0}
        self._fail_log_counts = {}

    def _bump(self, bucket, tf):
        self.metrics[bucket][tf] = self.metrics[bucket].get(tf, 0) + 1

    def _log_fail(self, reason, instrument, timeframe, error):
        # Rate-limited WARNING (1st per reason, then every Nth) so silent failures become visible.
        n = self._fail_log_counts.get(reason, 0) + 1
        self._fail_log_counts[reason] = n
        if n == 1 or n % _FAIL_LOG_EVERY == 0:
            _LOG.warning("[%s] instrument=%s timeframe=%s count=%d error=%s",
                         reason, instrument, timeframe, n, error)

    def emit(self, candle=None, *, generated_at_utc=None, **_):
        if candle is None:
            return {"emitted": False, "wrote": False, "reason": "NO_CANDLE"}
        tf = _tf_name(candle)
        if tf not in SUPPORTED_TF:
            self._bump("candles_skipped_unsupported_tf", tf)
            return {"emitted": False, "wrote": False, "reason": REASON_UNSUPPORTED_TF, "timeframe": tf}
        now = generated_at_utc or datetime.now(timezone.utc)
        try:
            envelope = runtime_candle_to_contract(candle, generated_at_utc=now)
            cc.validate_candle_contract(envelope)
        except Exception as exc:  # noqa: BLE001 - bounded; surfaced (counter + rate-limited log), not swallowed
            self.metrics["candle_validate_fail"] += 1
            self._log_fail("CANDLE_VALIDATE_FAIL", getattr(candle, "instrument", None), tf,
                           f"{type(exc).__name__}: {str(exc)[:160]}")
            return {"emitted": False, "wrote": False, "reason": "CANDLE_VALIDATE_FAIL",
                    "error": repr(exc), "timeframe": tf}
        try:
            res = self.writer.publish(envelope)   # JSON-serialised; shadow key only; assert_shadow_key
        except Exception as exc:  # noqa: BLE001
            self.metrics["candle_emit_fail"] += 1
            self._log_fail("CANDLE_EMIT_FAIL", getattr(candle, "instrument", None), tf,
                           f"{type(exc).__name__}: {str(exc)[:160]}")
            return {"emitted": False, "wrote": False, "reason": "CANDLE_EMIT_FAIL",
                    "error": repr(exc), "timeframe": tf}
        self._bump("candles_shadow_published", tf)
        return {"emitted": True, "wrote": True, "key": res["key"], "timeframe": tf,
                "status": envelope["status"], "freshness_state": envelope["freshness_state"],
                "gap_state": envelope["data"]["gap_state"]}

    def status(self):
        return {"enabled": True, "write_mode": self.write_mode, **self.metrics}


def build_shadow_seam(*, config, redis_client):
    """Construct the dev-shadow seam from an explicit config + injected Redis client. The writer
    validates shadow config (assert_shadow_allowed: explicit host/port/db, dev-shadow, not
    localhost-as-prod) and refuses canonical keys. Injectable for tests (fake client)."""
    writer = cp.SerializingCandleShadowWriter(config=config, redis_client=redis_client)
    return ShadowCandleForwardSeam(writer)


def _shadow_config_from_env(get_env, get_env_bool, get_env_int):
    """Build the shadow CandlePublisherConfig from the explicit env contract (fail-loud, no defaults).
      HERMES_CANDLE_FORWARD_SHADOW_REDIS_HOST/PORT/DB   (required when shadow enabled)
      HERMES_CANDLE_FORWARD_SHADOW_AUTHORISED           (must be true)
      HERMES_CANDLE_FORWARD_SHADOW_TREAT_AS_PRODUCTION  (default false)
      HERMES_CANDLE_FORWARD_SHADOW_DEV_SHADOW           (default false; required when host is loopback)
    """
    return cp.CandlePublisherConfig(
        publish_enabled=False, publish_authorised=False,
        shadow_publish_enabled=True,
        shadow_authorised=get_env_bool("HERMES_CANDLE_FORWARD_SHADOW_AUTHORISED", False),
        namespace="hermes", contract_version="v1",
        redis_host=get_env("HERMES_CANDLE_FORWARD_SHADOW_REDIS_HOST", required=True),
        redis_port=get_env_int("HERMES_CANDLE_FORWARD_SHADOW_REDIS_PORT", required=True),
        redis_db=get_env_int("HERMES_CANDLE_FORWARD_SHADOW_REDIS_DB", required=True),
        treat_as_production=get_env_bool("HERMES_CANDLE_FORWARD_SHADOW_TREAT_AS_PRODUCTION", False),
        dev_shadow=get_env_bool("HERMES_CANDLE_FORWARD_SHADOW_DEV_SHADOW", False))


def _real_shadow_redis_client(config):
    """Explicit-target real Redis client for the shadow writer (lazy import; only built when the
    shadow sink is enabled). NEVER called by tests (which inject a fake)."""
    import redis  # lazy; only on the shadow path
    return redis.Redis(host=config.redis_host, port=config.redis_port, db=config.redis_db,
                       socket_timeout=5)


class CanonicalCandleForwardSeam:
    """CANONICAL writer seam (GOLD MTF). Builds a governed candle envelope for DIRECT-NATIVE
    M1/M5/M15/H1 and writes the JSON-serialised payload to the canonical key
    hermes:candles:{instrument}:{tf}:latest:v1 on the configured canonical bus. Unsupported timeframes
    (D1/H4/D) are skipped (UNSUPPORTED_TIMEFRAME) — never published. Instruments are canonicalised
    (XAUUSD->XAU_USD, never dual-published). Observability counters are surfaced; writer errors are
    never silently swallowed. Requires a SerializingCandleCanonicalWriter (enabled AND authorised)."""

    def __init__(self, writer, allowed_instruments):
        if not isinstance(writer, cp.SerializingCandleCanonicalWriter):
            raise ValueError("GOV-CANDLE-FWD-SEAM-006: CanonicalCandleForwardSeam requires a "
                             "SerializingCandleCanonicalWriter (the only governed canonical write path)")
        allowed = frozenset(allowed_instruments or ())
        if not allowed:
            raise ValueError(f"{FAULT_CANONICAL_ALLOWLIST}: CanonicalCandleForwardSeam requires a non-empty "
                             "instrument allowlist (fail-closed)")
        self.writer = writer
        self.allowed_instruments = allowed     # canonical ids only; non-allowlisted candles are skipped
        self.enabled = True
        self.write_mode = cp.WRITE_MODE_CANONICAL
        self.metrics = {"candles_canonical_published": {}, "candles_skipped_unsupported_tf": {},
                        "candles_skipped_not_allowlisted": {}, "candles_skipped_derived_tf": {},
                        "candle_validate_fail": 0, "candle_emit_fail": 0}
        self._fail_log_counts = {}

    def _bump(self, bucket, tf):
        self.metrics[bucket][tf] = self.metrics[bucket].get(tf, 0) + 1

    def _log_fail(self, reason, instrument, timeframe, error):
        n = self._fail_log_counts.get(reason, 0) + 1
        self._fail_log_counts[reason] = n
        if n == 1 or n % _FAIL_LOG_EVERY == 0:
            _LOG.warning("[%s] instrument=%s timeframe=%s count=%d error=%s",
                         reason, instrument, timeframe, n, error)

    def emit(self, candle=None, *, generated_at_utc=None, **_):
        if candle is None:
            return {"emitted": False, "wrote": False, "reason": "NO_CANDLE"}
        tf = _tf_name(candle)
        if tf in DERIVED_TF:
            # H4 is derived-only — the direct emit path must NEVER publish it (no stale direct candle).
            self._bump("candles_skipped_derived_tf", tf)
            return {"emitted": False, "wrote": False, "reason": REASON_DERIVED_PATH_ONLY, "timeframe": tf}
        if tf not in SUPPORTED_TF:
            self._bump("candles_skipped_unsupported_tf", tf)
            return {"emitted": False, "wrote": False, "reason": REASON_UNSUPPORTED_TF, "timeframe": tf}
        # Fail-closed instrument allowlist: only authorised canonical instruments may publish. Skips are
        # observable via a per-instrument counter (no log spam) — never an exception, never a write.
        inst = canonical_instrument(getattr(candle, "instrument", None))
        if inst not in self.allowed_instruments:
            self._bump("candles_skipped_not_allowlisted", inst)
            return {"emitted": False, "wrote": False, "reason": REASON_NOT_ALLOWLISTED,
                    "instrument": inst, "timeframe": tf}
        now = generated_at_utc or datetime.now(timezone.utc)
        try:
            envelope = runtime_candle_to_contract(candle, generated_at_utc=now)
            cc.validate_candle_contract(envelope)
        except Exception as exc:  # noqa: BLE001 - bounded; surfaced (counter + rate-limited log), not swallowed
            self.metrics["candle_validate_fail"] += 1
            self._log_fail("CANDLE_VALIDATE_FAIL", getattr(candle, "instrument", None), tf,
                           f"{type(exc).__name__}: {str(exc)[:160]}")
            return {"emitted": False, "wrote": False, "reason": "CANDLE_VALIDATE_FAIL",
                    "error": repr(exc), "timeframe": tf}
        try:
            res = self.writer.publish(envelope)   # JSON-serialised; canonical key only; assert_canonical_key
        except Exception as exc:  # noqa: BLE001
            self.metrics["candle_emit_fail"] += 1
            self._log_fail("CANDLE_EMIT_FAIL", getattr(candle, "instrument", None), tf,
                           f"{type(exc).__name__}: {str(exc)[:160]}")
            return {"emitted": False, "wrote": False, "reason": "CANDLE_EMIT_FAIL",
                    "error": repr(exc), "timeframe": tf}
        self._bump("candles_canonical_published", tf)
        return {"emitted": True, "wrote": True, "key": res["key"], "timeframe": tf,
                "status": envelope["status"], "freshness_state": envelope["freshness_state"],
                "gap_state": envelope["data"]["gap_state"]}

    def status(self):
        return {"enabled": True, "write_mode": self.write_mode, **self.metrics}


def build_canonical_seam(*, config, redis_client, allowed_instruments):
    """Construct the canonical seam from an explicit config + injected Redis client + non-empty
    instrument allowlist. The writer validates canonical config (assert_canonical_allowed:
    publish_enabled AND publish_authorised) and publishes only versioned canonical keys for the
    M1/M5/M15/H1 grid; the seam additionally restricts publication to allowlisted instruments.
    Injectable for tests."""
    writer = cp.SerializingCandleCanonicalWriter(config=config, redis_client=redis_client)
    return CanonicalCandleForwardSeam(writer, allowed_instruments=allowed_instruments)


def _canonical_config_from_env(get_env, get_env_bool, get_env_int):
    """Build the canonical CandlePublisherConfig from the explicit env contract (fail-loud, no defaults).
      HERMES_CANDLE_PUBLISH_ENABLED      (must be true)   -- canonical master enable
      HERMES_CANDLE_PUBLISH_AUTHORISED   (must be true)   -- canonical authorisation
      HERMES_CANDLE_CANONICAL_REDIS_HOST/PORT/DB          (required; explicit canonical bus target)
    Both flags AND the explicit bus target are required; any missing -> fail loud (no accidental
    canonical selection, no hidden defaults)."""
    return cp.CandlePublisherConfig(
        publish_enabled=get_env_bool("HERMES_CANDLE_PUBLISH_ENABLED", False),
        publish_authorised=get_env_bool("HERMES_CANDLE_PUBLISH_AUTHORISED", False),
        shadow_publish_enabled=False, shadow_authorised=False,
        namespace="hermes", contract_version="v1",
        redis_host=get_env("HERMES_CANDLE_CANONICAL_REDIS_HOST", required=True),
        redis_port=get_env_int("HERMES_CANDLE_CANONICAL_REDIS_PORT", required=True),
        redis_db=get_env_int("HERMES_CANDLE_CANONICAL_REDIS_DB", required=True))


def _real_canonical_redis_client(config):
    """Explicit-target real Redis client for the canonical writer (lazy import; only built when the
    canonical sink is enabled). NEVER called by tests (which inject a fake)."""
    import redis  # lazy; only on the canonical path
    return redis.Redis(host=config.redis_host, port=config.redis_port, db=config.redis_db,
                       socket_timeout=5)


def build_candle_forward_seam_from_env():
    """Boot factory. Default DISABLED. Enabled requires an explicit sink mode:
    none/inert -> no-write; shadow -> dev shadow writer (fail-loud on missing/unsafe config);
    canonical -> canonical writer (fail-loud unless enabled AND authorised AND explicit bus config);
    live/prod or unknown -> FAIL LOUD. Never enables anything by itself."""
    from env_config import get_env, get_env_bool, get_env_int  # lazy; HERMES-owned config only
    if not get_env_bool("HERMES_CANDLE_FORWARD_ENABLED", False):
        return cp.DisabledCandleEmitter(_disabled_config())
    sink_mode = get_env("HERMES_CANDLE_FORWARD_SINK", required=True)
    sink_mode = (sink_mode or "").strip().lower()
    if sink_mode in ALLOWED_INERT_SINKS:
        return InertCandleForwardSeam(cp.NoWriteCandleSink())
    if sink_mode == SHADOW_SINK:
        config = _shadow_config_from_env(get_env, get_env_bool, get_env_int)
        config.assert_shadow_allowed()                 # fail-loud on missing/unsafe target
        client = _real_shadow_redis_client(config)
        if client is None:
            raise ValueError(f"{FAULT_SHADOW_CLIENT}: shadow sink requires a Redis client (none constructed)")
        return build_shadow_seam(config=config, redis_client=client)
    if sink_mode == CANONICAL_SINK:
        config = _canonical_config_from_env(get_env, get_env_bool, get_env_int)
        config.assert_canonical_allowed()              # fail-loud unless publish_enabled AND authorised
        allowed = parse_canonical_allowlist(get_env(CANONICAL_ALLOWLIST_ENV, required=True))  # fail-closed
        client = _real_canonical_redis_client(config)
        if client is None:
            raise ValueError(f"{FAULT_CANONICAL_CLIENT}: canonical sink requires a Redis client (none constructed)")
        return build_canonical_seam(config=config, redis_client=client, allowed_instruments=allowed)
    if sink_mode in FORBIDDEN_CANONICAL_ALIASES:
        raise ValueError(f"{FAULT_WRITE_FORBIDDEN}: candle-forward sink '{sink_mode}' is NOT a permitted "
                         "selector — use 'canonical' with explicit governed config (no production-implying sinks).")
    raise ValueError(f"{FAULT_WRITE_FORBIDDEN}: unknown candle-forward sink '{sink_mode}' "
                     f"(allowed: {ALLOWED_INERT_SINKS + (SHADOW_SINK, CANONICAL_SINK)}).")
