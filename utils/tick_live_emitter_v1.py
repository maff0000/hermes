"""HERMES LIVE canonical tick emitter v1 — dark/inert by default.
WO-HELM-HERMES-LIVE-TICK-PUBLISHER-V1-BUILD-0001.

Type-B EMITTER (per-tick, invoked from the OANDA stream loop where the live SignalTick / state.latest_ticks context
is available). A supervisor-runner (Type-A) was REJECTED with code evidence: (1) state.latest_ticks is in-process
stream state inside main.py, unreachable from a supervisor step (step(client) gets Redis only); (2) the 60s
supervisor cadence cannot serve a 5s-TTL / 10s-EX hot key; (3) the proven tick pattern is already emitter-based
(tick_runtime_shadow_adapter_v1). This module mirrors that shadow emitter but writes the CANONICAL live key
hermes:ticks:XAU_USD:latest:v1 (EX=10) ONLY when the LIVE gates are enabled+authorised+canonically-scoped.

Guarantees: DISABLED by default (no client, no I/O). Never writes from shadow mode / to a shadow key. Never
dual-publishes XAUUSD. Never writes quote keys. Reuses utils.tick_contract_v1 verbatim (fail-loud on malformed
bid/ask; absent tick -> explicit UNAVAILABLE, never fake data). emit_tick_observed never breaks the tick path. UTC.

Out-of-scope handling: the OANDA stream carries MANY instruments and the stream loop calls the emitter for every
tick; instruments outside the LIVE scope are a NORMAL condition. emit_tick SKIPS them BEFORE any envelope build
(returns OUT_OF_SCOPE) — no Redis write, no fault, no WARN log. The contract fail-loud guard (GOV-HERMES-TICK-034)
remains in build_envelope as defence-in-depth for any path that bypassed the scope skip.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

from utils import tick_contract_v1 as tc
from utils import hermes_advanced_v1_publication_gate_v1 as _pubgate   # central master/scope publication-eligibility gate
from utils.tick_runtime_shadow_adapter_v1 import tick_to_raw_tick   # reuse the proven SignalTick -> raw adapter

# WO-HELM-HERMES-ADVANCED-V1-XAU-MODULE-ADOPTION-0001: instrument selection is the canonical registry
# (via the selection seam), NOT a hard-coded XAU authority constant. `_ALIAS_DENY` remains a format guard.
_ALIAS_DENY = ("XAUUSD",)
SHADOW_PREFIX = "hermes:shadow:"
HALT_CODE = 101

# LIVE gates (established enabled -> authorised -> instrument-scope pattern). Names reserved in
# hermes_quote_tick_contract_v1.py (HERMES_TICK_PUBLISH_ENABLED/AUTHORISED); INSTRUMENTS added here.
ENABLED_ENV = "HERMES_TICK_PUBLISH_ENABLED"
AUTHORISED_ENV = "HERMES_TICK_PUBLISH_AUTHORISED"
INSTRUMENTS_ENV = "HERMES_TICK_PUBLISH_INSTRUMENTS"


def _validate_allowed(allowed):
    """Fail-closed: the tick allowlist (from the registry selection seam) must be a non-empty set of canonical
    symbols with no inbound alias. Zero-selection is handled by the caller (Disabled emitter), not here."""
    allowed = frozenset(allowed)
    if not allowed:
        raise ValueError("GOV-HERMES-TICK-020: tick allowlist is empty (fail-closed; caller must disable)")
    for inst in allowed:
        if inst in _ALIAS_DENY:
            raise ValueError(f"GOV-HERMES-TICK-021: instrument {inst!r} not allowed (XAUUSD is an inbound alias, "
                             "never an output publish key)")
    return allowed


def parse_tick_instruments(raw, *, allowed):
    """TRANSITIONAL env consistency validator (§16): env INSTRUMENTS is NOT an authority — every env-named
    instrument must be present in the registry-selected `allowed` set, else fail closed. Returns `allowed`."""
    allowed = _validate_allowed(allowed)
    if raw is None or not str(raw).strip():
        raise ValueError(f"GOV-HERMES-TICK-020: {INSTRUMENTS_ENV} required and non-empty (fail-closed)")
    items = [x.strip() for x in str(raw).split(",") if x.strip()]
    if not items:
        raise ValueError(f"GOV-HERMES-TICK-020: {INSTRUMENTS_ENV} required and non-empty (fail-closed)")
    for inst in items:
        if inst in _ALIAS_DENY or inst not in allowed:
            raise ValueError(f"GOV-HERMES-TICK-021: env instrument {inst!r} not in the registry tick selection "
                             f"{sorted(allowed)} (the SQL registry is the sole authority; env is a validator)")
    return allowed


def _assert_canonical_live_key(key, instrument):
    """The LIVE emitter writes ONLY the per-instrument canonical key for the instrument being emitted. Refuse
    shadow-prefix, XAUUSD alias, or any non-canonical key (fail-loud — never a silent wrong-key write)."""
    if key.startswith(SHADOW_PREFIX):
        raise ValueError(f"GOV-HERMES-TICK-030: LIVE emitter refuses shadow key {key!r} (canonical live only)")
    if "XAUUSD" in key:
        raise ValueError(f"GOV-HERMES-TICK-031: LIVE emitter refuses XAUUSD key {key!r} (canonical alias)")
    if key != tc.canonical_key(instrument):
        raise ValueError(f"GOV-HERMES-TICK-032: LIVE emitter writes only {tc.canonical_key(instrument)} "
                         f"(got {key!r})")
    return True


def _coerce_utc(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        s = value[:-1] if value.endswith("Z") else value
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    raise ValueError("GOV-HERMES-TICK-035: source_received_at_utc must be a UTC datetime or ISO string")


def _tick_instrument(tick):
    """The instrument of a live SignalTick (duck-typed) or raw dict — cheap read for scope filtering (no envelope
    build). Matches the instrument tick_to_raw_tick would extract."""
    if isinstance(tick, dict):
        return tick.get("instrument")
    return getattr(tick, "instrument", None)


class DisabledTickEmitter:
    """Explicit no-op emitter (default). Holds NO redis client and performs NO I/O. Never writes."""
    enabled = False

    def emit_tick(self, tick, *, now=None, logger=None):
        return {"emitted": False, "reason": "LIVE_TICK_DISABLED"}

    def emit_tick_observed(self, tick, *, logger=None, now=None):
        return {"emitted": False, "reason": "LIVE_TICK_DISABLED"}

    def status(self):
        return {"enabled": False}


class LiveTickEmitter:
    """Enabled emitter — writes ONLY hermes:ticks:XAU_USD:latest:v1 (EX=10) via the governed tick contract.
    emit_tick is fail-loud (raises on GOV/contract faults); emit_tick_observed wraps it for the stream path and
    never raises (a tick-emit fault must not disrupt the market-truth path)."""
    enabled = True

    def __init__(self, *, allowed_instruments, redis_client, records=None):
        allowed_instruments = _validate_allowed(allowed_instruments)  # registry-selected, non-empty, no alias
        if redis_client is None:
            raise ValueError("GOV-HERMES-TICK-033: enabled LIVE tick emitter requires an explicit redis client "
                             "(no silent no-op when enabled)")
        self.allowed_instruments = frozenset(allowed_instruments)
        self.redis_client = redis_client
        self._records = tuple(records) if records is not None else None   # registry snapshot the gate must agree with
        self.attempts = 0
        self.published = 0
        self.faults = 0
        self.skipped_out_of_scope = 0
        self.last_fault = None

    def in_scope(self, tick):
        """True iff this tick's instrument is within the emitter's allowed LIVE scope. The OANDA stream carries
        MANY instruments and the stream loop calls the emitter for every tick; instruments outside scope are a
        NORMAL condition (skip), not an error."""
        return _tick_instrument(tick) in self.allowed_instruments

    def build_envelope(self, tick, *, now):
        """Map a live SignalTick (or raw dict) -> governed canonical tick envelope via tick_contract_v1. Absent
        source data -> explicit UNAVAILABLE (honest, never fabricated); malformed bid/ask -> fail loud (contract).
        Out-of-scope here is IMPOSSIBLE in normal flow (emit_tick skips first); GOV-HERMES-TICK-034 remains as
        defence-in-depth for any direct/unsafe call that bypassed the scope skip."""
        raw = tick_to_raw_tick(tick)
        instrument = raw.get("instrument")
        if instrument not in self.allowed_instruments:
            raise ValueError(f"GOV-HERMES-TICK-034: instrument {instrument!r} not in scope "
                             f"{sorted(self.allowed_instruments)} (fail-closed defence-in-depth)")
        bid, ask, received = raw.get("bid"), raw.get("ask"), raw.get("source_received_at_utc")
        if bid is None and ask is None and received is None:
            env = tc.build_unavailable(instrument=instrument, generated_at_utc=now,
                                       reason_codes=["NO_VALID_SOURCE_TICK"])
        else:
            env = tc.build_tick_contract(instrument=instrument, source_received_at_utc=_coerce_utc(received),
                                         generated_at_utc=now, bid=bid, ask=ask,
                                         source=raw.get("source", "oanda"),
                                         source_warning=bool(raw.get("source_warning", False)))
        tc.validate_tick_contract(env)          # fail-loud governance re-validation
        return env

    def emit_tick(self, tick, *, now=None, logger=None):
        now = now or datetime.now(timezone.utc)
        # SCOPE SKIP FIRST (before any attempt/envelope/fault): the multi-instrument stream carries non-XAU_USD ticks
        # constantly; those are a NORMAL skip, NOT a fault. No envelope build, no Redis write, no fault counter, no log.
        if not self.in_scope(tick):
            self.skipped_out_of_scope += 1
            return {"emitted": False, "reason": "OUT_OF_SCOPE", "instrument": _tick_instrument(tick)}
        # CENTRAL MASTER/SCOPE GATE: pilot preserved; expansion instruments fail-closed until master+mode+registry+
        # calendar+readiness all pass. A denied expansion tick is a governed skip (no envelope, no write), not a fault.
        _inst = _tick_instrument(tick)
        if not _pubgate.decide(_inst, "tick", now=now, records=self._records).permitted:
            self.skipped_out_of_scope += 1
            return {"emitted": False, "reason": "EXPANSION_GATED", "instrument": _inst}
        self.attempts += 1
        env = self.build_envelope(tick, now=now)
        key = env["key"]
        _assert_canonical_live_key(key, env["data"]["instrument"])   # canonical-only per emitted instrument
        self.redis_client.set(key, json.dumps(env), ex=tc.REDIS_EX_SECONDS)   # SET canonical, EX=10
        self.published += 1
        return {"emitted": True, "key": key, "status": env["status"], "freshness_state": env["freshness_state"]}

    def emit_tick_observed(self, tick, *, logger=None, now=None):
        """Stream-path entrypoint — never raises; a tick-emit fault is counted + logged, never propagated."""
        try:
            return self.emit_tick(tick, now=now, logger=logger)
        except Exception as exc:  # noqa: BLE001 - bounded: live tick emit must never break the market-truth path
            self.faults += 1
            self.last_fault = repr(exc)[:200]
            if logger is not None:
                logger.warning("[LIVE_TICK_EMIT_FAIL] faults=%d error=%r", self.faults, exc)
            return {"emitted": False, "reason": "LIVE_TICK_EMIT_FAIL", "error": repr(exc)}

    def status(self):
        return {"enabled": True, "instruments": sorted(self.allowed_instruments),
                "attempts": self.attempts, "published": self.published, "faults": self.faults,
                "skipped_out_of_scope": self.skipped_out_of_scope}


def _default_canonical_redis_client():
    """Lazy canonical HERMES redis client (same target as every other canonical hermes:* key). Only ever built
    when the emitter is enabled+authorised; explicit env, no default host/port/db."""
    import redis
    from env_config import get_env, get_env_int
    return redis.Redis(host=get_env("HERMES_CANDLE_CANONICAL_REDIS_HOST", required=True),
                       port=get_env_int("HERMES_CANDLE_CANONICAL_REDIS_PORT", required=True),
                       db=get_env_int("HERMES_CANDLE_CANONICAL_REDIS_DB", required=True), socket_timeout=5)


def tick_live_gate_enabled():
    """True iff the LIVE tick emitter gate is enabled+authorised+validly-scoped. ENV READ ONLY — builds NO redis
    client, does NO I/O. Enabled-without-authorised -> SystemExit(101); enabled+authorised without a valid
    canonical scope -> fail-closed (GOV-HERMES-TICK-020/021). The catalog uses THIS to mark tick RUNTIME_PUBLISHED
    atomically with the emitter (same gate condition -> no split-brain)."""
    from env_config import get_env_bool
    if not get_env_bool(ENABLED_ENV, False):
        return False
    if not get_env_bool(AUTHORISED_ENV, False):
        raise SystemExit(HALT_CODE)
    return True   # instrument scope is now the registry's authority (validated at emitter build)


def build_tick_live_emitter_from_registry(records=None, *, redis_client=None, redis_client_factory=None):
    """Registry-driven authority (WO-...-XAU-MODULE-ADOPTION-0001). Selects tick instruments from the canonical
    registry seam (selection_for('tick', records)); records default to the loaded canonical registry. Zero
    selection -> DisabledTickEmitter (no publication, no fallback). One/many -> a generic LiveTickEmitter over
    the selected set (byte-identical XAU key preserved)."""
    from utils.hermes_advanced_v1_selection_v1 import selection_for
    from utils.hermes_instrument_registry_v1 import load_from_db
    if records is None:
        records = load_from_db()
    allowed = selection_for("tick", records)
    if not allowed:
        return DisabledTickEmitter()   # zero-selection: truthful no-op, never a fallback
    client = redis_client
    if client is None:
        client = (redis_client_factory or _default_canonical_redis_client)()
    return LiveTickEmitter(allowed_instruments=allowed, redis_client=client, records=records)


def build_tick_live_emitter_from_env(*, records=None, redis_client=None, redis_client_factory=None):
    """Boot entrypoint for main.py. DISABLED by default -> DisabledTickEmitter. Enabled-without-authorised ->
    SystemExit(101). Enabled+authorised -> registry-driven emitter. The canonical registry (`records`, default
    loaded) is the sole selection authority; env INSTRUMENTS is a transitional CONSISTENCY validator only (§16)."""
    from env_config import get_env, get_env_bool
    from utils.hermes_advanced_v1_selection_v1 import selection_for
    from utils.hermes_instrument_registry_v1 import load_from_db
    if not get_env_bool(ENABLED_ENV, False):
        return DisabledTickEmitter()
    if not get_env_bool(AUTHORISED_ENV, False):
        raise SystemExit(HALT_CODE)
    if records is None:
        records = load_from_db()
    raw = get_env(INSTRUMENTS_ENV, default=None)
    if raw is not None and str(raw).strip():
        parse_tick_instruments(raw, allowed=selection_for("tick", records))   # env<=registry consistency, fail-closed
    return build_tick_live_emitter_from_registry(records, redis_client=redis_client,
                                                 redis_client_factory=redis_client_factory)
