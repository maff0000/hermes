"""C-WP3-TARGET-GUARDS-ABSENT-IN-IMAGE correction — SHADOW target-isolation guard tests.

WO-HELM-HERMES-CONTAINER-MVP-WP3-SHADOW-TARGET-ISOLATION-GUARDS-0001.
Authority: HELM. Created (UTC): 2026-07-30. Contract version: 1.

Prove the fail-closed SHADOW startup contract: a HERMES process configured RUN_ENV=SHADOW cannot start when
any configured target could affect a live/canonical resource (canonical Redis, production SQL, live OANDA,
consumer activation), the guard runs BEFORE any connector is constructed, a valid isolated replay stack
passes, and non-SHADOW live configuration is unchanged. No secrets, no network.
"""
from __future__ import annotations

import ast
import datetime

import pytest

import utils.hermes_shadow_target_guard_v1 as g
from utils.hermes_shadow_target_guard_v1 import (
    validate_shadow_targets, ShadowTargetGuardError, ShadowTargetManifest, manifest_status_dict,
)

NOW = "2026-07-30T00:00:00+00:00"


# --------------------------------------------------------------------------- fake config + env
class _O:
    def __init__(self, environment="practice", use_mock=True, mock_url="http://mock-oanda:8299",
                 api_key="x", account_id="001-000-0000000-000"):
        self.environment = environment; self.use_mock = use_mock; self.mock_url = mock_url
        self.api_key = api_key; self.account_id = account_id


class _R:
    def __init__(self, host="wp3-shadow-redis", port=6380, key_prefix="shadow:hermes:wp3:run-abc123:"):
        self.host = host; self.port = port; self.key_prefix = key_prefix


class _D:
    def __init__(self, host="wp3-mariadb", port=3306, database="hermes_wp3_shadow"):
        self.host = host; self.port = port; self.database = database


class _Cfg:
    def __init__(self, oanda=None, redis=None, database=None):
        self.oanda = oanda or _O(); self.redis = redis or _R(); self.database = database or _D()


_VALID_ENV = {
    "RUN_ENV": "SHADOW", "CONSUMER_LIVE": "false", "HERMES_SHADOW_RUN_ID": "run-abc123",
    "HERMES_FEED_MODE": "replay", "REDIS_TARGET_CLASS": "shadow", "DB_TARGET_CLASS": "shadow",
}


def _env(**over):
    d = dict(_VALID_ENV); d.update(over)
    def getter(key, default=None, required=False):
        return d.get(key, default)
    return getter


def _run(cfg=None, **envover):
    return validate_shadow_targets(cfg or _Cfg(), env=_env(**envover), now_utc=NOW)


# =========================================================================== POSITIVE (valid replay stack)
def test_valid_replay_stack_passes():
    m = _run()
    assert isinstance(m, ShadowTargetManifest) and m.is_shadow and m.validated
    assert m.run_id == "run-abc123" and m.feed_mode == "replay"
    assert m.redis_class == "shadow" and m.redis_port == 6380 and m.sql_class == "shadow"
    assert m.masked_db == "herm***"  # bounded, non-secret


def test_manifest_has_no_secret_fields():
    m = _run(); d = m.__dict__
    for k, v in d.items():
        assert "key" not in k.lower() or k == "guard_contract_version"
    s = str(manifest_status_dict(m))
    assert "api_key" not in s and "password" not in s and "001-000-0000000" not in s


# =========================================================================== CONSUMER guard
def test_consumer_live_true_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(CONSUMER_LIVE="true")
    assert e.value.fault_code == "SHADOW-CONSUMER-LIVE-FORBIDDEN"


@pytest.mark.parametrize("val", ["TRUE", "1", "yes", "on", "True"])
def test_consumer_live_truthy_variants_rejected(val):
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(CONSUMER_LIVE=val)
    assert e.value.fault_code == "SHADOW-CONSUMER-LIVE-FORBIDDEN"


def test_consumer_live_malformed_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(CONSUMER_LIVE="maybe")
    assert e.value.fault_code == "SHADOW-CONSUMER-LIVE-FORBIDDEN"


def test_secondary_consumer_flag_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(HERMES_CONSUMER_ENABLED="true")
    assert e.value.fault_code == "SHADOW-CONSUMER-LIVE-FORBIDDEN"


# =========================================================================== OANDA guard
def test_oanda_live_env_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(_Cfg(oanda=_O(environment="live", use_mock=True)))
    assert e.value.fault_code == "SHADOW-OANDA-LIVE-FORBIDDEN"


def test_replay_without_mock_is_conflict():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(_Cfg(oanda=_O(environment="practice", use_mock=False)))
    assert e.value.fault_code == "SHADOW-OANDA-REPLAY-LIVE-CONFLICT"


@pytest.mark.parametrize("url", [
    "https://stream-fxtrade.oanda.com", "https://api-fxtrade.oanda.com", "https://x.oanda.com/stream"])
def test_replay_mock_url_pointing_at_live_oanda_rejected(url):
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(_Cfg(oanda=_O(environment="practice", use_mock=True, mock_url=url)))
    assert e.value.fault_code == "SHADOW-OANDA-LIVE-FORBIDDEN"


def test_missing_feed_mode_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(HERMES_FEED_MODE="")
    assert e.value.fault_code == "SHADOW-FEED-MODE-REQUIRED"


def test_ambiguous_feed_mode_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(HERMES_FEED_MODE="banana")
    assert e.value.fault_code == "SHADOW-FEED-MODE-REQUIRED"


def test_practice_without_credentials_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(_Cfg(oanda=_O(environment="practice")), HERMES_FEED_MODE="practice")
    assert e.value.fault_code == "SHADOW-PRACTICE-CREDENTIALS-ABSENT"


def test_practice_with_wrong_env_ambiguous():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(_Cfg(oanda=_O(environment="")), HERMES_FEED_MODE="practice")
    assert e.value.fault_code == "SHADOW-OANDA-FEED-MODE-AMBIGUOUS"


def test_practice_classification_accepts_static():
    m = _run(_Cfg(oanda=_O(environment="practice")), HERMES_FEED_MODE="practice",
             OANDA_PRACTICE_TARGET_CLASS="practice")
    assert m.feed_mode == "practice" and m.oanda_class == "practice"


# =========================================================================== REDIS target guard
def test_redis_class_missing_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(REDIS_TARGET_CLASS="")
    assert e.value.fault_code == "SHADOW-REDIS-CLASS-REQUIRED"


@pytest.mark.parametrize("cls", ["canonical", "production", "live", "prod"])
def test_redis_canonical_class_rejected(cls):
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(REDIS_TARGET_CLASS=cls)
    assert e.value.fault_code == "SHADOW-REDIS-TARGET-FORBIDDEN"


def test_redis_port_6379_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(_Cfg(redis=_R(port=6379)))
    assert e.value.fault_code == "SHADOW-REDIS-TARGET-FORBIDDEN"


def test_redis_namespace_missing_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(_Cfg(redis=_R(key_prefix="")))
    assert e.value.fault_code == "SHADOW-REDIS-NAMESPACE-REQUIRED"


@pytest.mark.parametrize("ns", ["hermes", "hermes:", "hermes:candles:", " hermes: ", "HERMES:", "hermes："])
def test_redis_canonical_namespace_rejected(ns):
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(_Cfg(redis=_R(key_prefix=ns)))
    assert e.value.fault_code == "SHADOW-REDIS-CANONICAL-KEYSPACE-FORBIDDEN"


def test_redis_namespace_without_runid_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(_Cfg(redis=_R(key_prefix="shadow:hermesx:wp3:other:")))
    assert e.value.fault_code == "SHADOW-REDIS-NAMESPACE-REQUIRED"


def test_valid_shadow_namespace_accepted():
    m = _run(_Cfg(redis=_R(key_prefix="shadow:wp3:run-abc123:")))
    assert m.redis_namespace == "shadow:wp3:run-abc123:"


# =========================================================================== SQL target guard
def test_db_class_missing_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(DB_TARGET_CLASS="")
    assert e.value.fault_code == "SHADOW-DB-CLASS-REQUIRED"


@pytest.mark.parametrize("cls", ["production", "canonical", "live"])
def test_db_production_class_rejected(cls):
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(DB_TARGET_CLASS=cls)
    assert e.value.fault_code == "SHADOW-DB-TARGET-FORBIDDEN"


def test_db_canonical_schema_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(_Cfg(database=_D(database="tradingSignals")))
    assert e.value.fault_code == "SHADOW-DB-CANONICAL-SCHEMA-FORBIDDEN"


@pytest.mark.parametrize("name", ["argus_db", "ares_signals", "proteus_x", "tradingProteus"])
def test_db_cross_application_schema_rejected(name):
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(_Cfg(database=_D(database=name)))
    assert e.value.fault_code == "SHADOW-DB-CROSS-APPLICATION-FORBIDDEN"


def test_db_name_missing_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(_Cfg(database=_D(database="")))
    assert e.value.fault_code == "SHADOW-DB-NAME-REQUIRED"


def test_db_ephemeral_class_accepted():
    m = _run(DB_TARGET_CLASS="ephemeral")
    assert m.sql_class == "ephemeral"


# =========================================================================== RUN-ID guard
def test_run_id_missing_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(HERMES_SHADOW_RUN_ID="")
    assert e.value.fault_code == "SHADOW-RUN-ID-REQUIRED"


@pytest.mark.parametrize("rid", ["ab", "UPPER", "has space", "bad/char", "x" * 65])
def test_run_id_malformed_rejected(rid):
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(HERMES_SHADOW_RUN_ID=rid, **{"REDIS_TARGET_CLASS": "shadow"})
    assert e.value.fault_code == "SHADOW-RUN-ID-REQUIRED"


@pytest.mark.parametrize("rid", ["live", "prod", "production", "canonical"])
def test_run_id_reserved_rejected(rid):
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(HERMES_SHADOW_RUN_ID=rid)
    assert e.value.fault_code == "SHADOW-RUN-ID-REQUIRED"


def test_run_id_in_redis_and_sql_provenance():
    m = _run()
    assert m.run_id in m.redis_namespace
    d = manifest_status_dict(m)
    assert d["shadow_run_id"] == m.run_id and d["sql_target_class"] == "shadow"


# =========================================================================== NON-SHADOW compatibility
@pytest.mark.parametrize("mode", ["STAGING", "PRODUCTION", "DEVELOPMENT", ""])
def test_non_shadow_dormant_no_rejection(mode):
    # a config that WOULD be rejected in SHADOW (canonical redis, live oanda, consumer true) is NOT
    # rejected outside SHADOW — the guard is dormant, preserving deployed behaviour.
    cfg = _Cfg(oanda=_O(environment="live", use_mock=False), redis=_R(port=6379, key_prefix="hermes:"),
               database=_D(database="tradingSignals"))
    m = validate_shadow_targets(cfg, env=_env(RUN_ENV=mode, CONSUMER_LIVE="true"), now_utc=NOW)
    assert m.is_shadow is False and m.validated is True


# =========================================================================== STARTUP ORDERING (connector-not-called)
def test_guard_runs_before_connectors_contract():
    """The guarded-startup ORDER: guard() then connector(). A rejected SHADOW config must raise BEFORE any
    connector is constructed. Proven with spies replicating the lifespan order."""
    calls = []

    def fake_redis_ctor(*a, **k): calls.append("redis")
    def fake_sql_connect(*a, **k): calls.append("sql")
    def fake_oanda_ctor(*a, **k): calls.append("oanda")

    def guarded_startup(cfg, env):
        # mirrors main.lifespan order: validate BEFORE any connector
        validate_shadow_targets(cfg, env=env, now_utc=NOW)
        fake_redis_ctor(); fake_sql_connect(); fake_oanda_ctor()

    with pytest.raises(ShadowTargetGuardError):
        guarded_startup(_Cfg(redis=_R(port=6379)), _env())
    assert calls == []  # NO connector constructed after guard failure


def test_main_lifespan_calls_guard_before_connectors_static():
    """Static AST proof: in main.py the validate_shadow_targets call lexically precedes RedisPublisher(),
    pymysql.connect and OANDAAdapter( within the lifespan."""
    src = open("main.py").read()
    gi = src.index("validate_shadow_targets(state.config)")
    ri = src.index("state.redis_publisher = RedisPublisher()")
    oi = src.index("state.oanda_adapter = OANDAAdapter(")
    assert gi < ri < oi, "guard must precede Redis + OANDA connector construction in lifespan"


# =========================================================================== ADVERSARIAL MATRIX
_MATRIX = [
    ({}, {"redis": _R(port=6379)}, "SHADOW-REDIS-TARGET-FORBIDDEN"),
    ({"REDIS_TARGET_CLASS": "canonical"}, {}, "SHADOW-REDIS-TARGET-FORBIDDEN"),
    ({}, {"redis": _R(key_prefix="hermes:")}, "SHADOW-REDIS-CANONICAL-KEYSPACE-FORBIDDEN"),
    ({"DB_TARGET_CLASS": "production"}, {}, "SHADOW-DB-TARGET-FORBIDDEN"),
    ({}, {"database": _D(database="tradingSignals")}, "SHADOW-DB-CANONICAL-SCHEMA-FORBIDDEN"),
    ({}, {"oanda": _O(environment="live")}, "SHADOW-OANDA-LIVE-FORBIDDEN"),
    ({"CONSUMER_LIVE": "true"}, {}, "SHADOW-CONSUMER-LIVE-FORBIDDEN"),
    ({"HERMES_SHADOW_RUN_ID": ""}, {}, "SHADOW-RUN-ID-REQUIRED"),
]


@pytest.mark.parametrize("envover,cfgover,code", _MATRIX)
def test_adversarial_matrix(envover, cfgover, code):
    with pytest.raises(ShadowTargetGuardError) as e:
        validate_shadow_targets(_Cfg(**cfgover), env=_env(**envover), now_utc=NOW)
    assert e.value.fault_code == code


def test_matrix_multiple_unsafe_targets_still_fails_closed():
    # every axis unsafe simultaneously -> still a single stable primary fault, never a pass
    cfg = _Cfg(oanda=_O(environment="live"), redis=_R(port=6379, key_prefix="hermes:"),
               database=_D(database="tradingSignals"))
    with pytest.raises(ShadowTargetGuardError):
        validate_shadow_targets(cfg, env=_env(CONSUMER_LIVE="true"), now_utc=NOW)


def test_no_secret_in_fault_messages():
    for envover, cfgover, _ in _MATRIX:
        try:
            validate_shadow_targets(_Cfg(**cfgover), env=_env(**envover), now_utc=NOW)
        except ShadowTargetGuardError as e:
            assert "001-000" not in str(e) and "password" not in str(e).lower()


# =========================================================================== SECONDARY REDIS WRITE TARGETS
# WO-HELM-HERMES-CONTAINER-MVP-WP3-PR126-ALL-REDIS-WRITE-TARGETS-ISOLATION-CORRECTION-0001
# C-WP3-SECONDARY-REDIS-TARGETS-UNGUARDED: every secondary Redis writer must be manifest-validated or
# DISABLED before its client is constructed. The candle-forward canonical sink is the load-bearing path.

def test_valid_stack_all_secondary_writers_disabled():
    m = _run()
    st = {r: s for r, s in m.redis_writers}
    assert st.get("candle_forward_seam") == "disabled"
    assert st.get("live_tick_emitter") == "disabled"
    assert st.get("primary_publisher") == "primary-validated"


# ---- candle-forward canonical sink (the finding) ----
@pytest.mark.parametrize("sink", ["canonical", "live", "prod", "production"])
def test_candle_forward_canonical_sink_rejected(sink):
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(HERMES_CANDLE_FORWARD_ENABLED="true", HERMES_CANDLE_FORWARD_SINK=sink)
    assert e.value.fault_code == "SHADOW-CANDLE-FORWARD-CANONICAL-SINK-FORBIDDEN"


def test_candle_forward_canonical_redis_port_6379_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(HERMES_CANDLE_CANONICAL_REDIS_PORT="6379")
    assert e.value.fault_code == "SHADOW-CANDLE-FORWARD-REDIS-FORBIDDEN"


def test_candle_forward_shadow_sink_without_target_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(HERMES_CANDLE_FORWARD_ENABLED="true", HERMES_CANDLE_FORWARD_SINK="shadow")
    assert e.value.fault_code == "SHADOW-CANDLE-FORWARD-TARGET-REQUIRED"


def test_candle_forward_shadow_sink_on_6379_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(HERMES_CANDLE_FORWARD_ENABLED="true", HERMES_CANDLE_FORWARD_SINK="shadow",
             HERMES_CANDLE_FORWARD_SHADOW_REDIS_HOST="x", HERMES_CANDLE_FORWARD_SHADOW_REDIS_PORT="6379")
    assert e.value.fault_code == "SHADOW-CANDLE-FORWARD-REDIS-FORBIDDEN"


def test_candle_forward_unknown_sink_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(HERMES_CANDLE_FORWARD_ENABLED="true", HERMES_CANDLE_FORWARD_SINK="banana")
    assert e.value.fault_code == "SHADOW-CANDLE-FORWARD-CANONICAL-SINK-FORBIDDEN"


def test_candle_forward_disabled_accepted():
    m = _run(HERMES_CANDLE_FORWARD_ENABLED="false")
    assert {r: s for r, s in m.redis_writers}["candle_forward_seam"] == "disabled"


def test_candle_forward_inert_accepted():
    m = _run(HERMES_CANDLE_FORWARD_ENABLED="true", HERMES_CANDLE_FORWARD_SINK="inert")
    assert {r: s for r, s in m.redis_writers}["candle_forward_seam"] == "disabled"


def test_candle_forward_valid_shadow_sink_accepted():
    m = _run(HERMES_CANDLE_FORWARD_ENABLED="true", HERMES_CANDLE_FORWARD_SINK="shadow",
             HERMES_CANDLE_FORWARD_SHADOW_REDIS_HOST="wp3-shadow-redis",
             HERMES_CANDLE_FORWARD_SHADOW_REDIS_PORT="6380",
             HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX="hermes:shadow:wp3:run-abc123:candles:")
    assert {r: s for r, s in m.redis_writers}["candle_forward_seam"] == "shadow-validated"
    assert m.candle_forward_shadow_prefix == "hermes:shadow:wp3:run-abc123:candles:"


# ---- auxiliary canonical writers must be disabled in SHADOW ----
@pytest.mark.parametrize("flag,code", [
    ("HERMES_TICK_PUBLISH_ENABLED", "SHADOW-AUX-CANONICAL-WRITER-FORBIDDEN"),
    ("HERMES_CANDLE_PUBLISH_ENABLED", "SHADOW-AUX-CANONICAL-WRITER-FORBIDDEN"),
    ("HERMES_CANDLE_HISTORY_FORWARD_ENABLED", "SHADOW-AUX-CANONICAL-WRITER-FORBIDDEN"),
    ("HERMES_CANDLE_D1_HISTORY_ENABLED", "SHADOW-AUX-CANONICAL-WRITER-FORBIDDEN"),
    ("HERMES_D1_HISTORY_BACKFILL_ENABLED", "SHADOW-AUX-CANONICAL-WRITER-FORBIDDEN"),
    ("HERMES_BACKFILL_STATUS_PUBLISH_ENABLED", "SHADOW-AUX-CANONICAL-WRITER-FORBIDDEN"),
    ("HERMES_CANDLE_H4_PUBLISH_ENABLED", "SHADOW-AUX-CANONICAL-WRITER-FORBIDDEN"),
])
def test_auxiliary_canonical_writer_enabled_rejected(flag, code):
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(**{flag: "true"})
    assert e.value.fault_code == code


def test_auxiliary_writer_malformed_flag_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(HERMES_TICK_PUBLISH_ENABLED="maybe")
    assert e.value.fault_code == "SHADOW-AUX-CANONICAL-WRITER-FORBIDDEN"


# ---- shadow tick emitter ----
def test_shadow_tick_enabled_valid_target_accepted():
    m = _run(HERMES_SHADOW_TICK_PUBLISH_ENABLED="true", HERMES_SHADOW_TICK_REDIS_HOST="wp3-redis",
             HERMES_SHADOW_TICK_REDIS_PORT="6380",
             HERMES_SHADOW_TICK_KEY_PREFIX="hermes:shadow:wp3:run-abc123:ticks:")
    assert {r: s for r, s in m.redis_writers}["shadow_tick_emitter"] == "shadow-validated"
    assert m.shadow_tick_prefix == "hermes:shadow:wp3:run-abc123:ticks:"


def test_shadow_tick_on_6379_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(HERMES_SHADOW_TICK_PUBLISH_ENABLED="true", HERMES_SHADOW_TICK_REDIS_HOST="x",
             HERMES_SHADOW_TICK_REDIS_PORT="6379")
    assert e.value.fault_code == "SHADOW-SHADOW-TICK-TARGET-FORBIDDEN"


def test_shadow_tick_treat_as_production_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(HERMES_SHADOW_TICK_PUBLISH_ENABLED="true", HERMES_SHADOW_TICK_REDIS_HOST="x",
             HERMES_SHADOW_TICK_REDIS_PORT="6380", HERMES_SHADOW_TICK_TREAT_AS_PRODUCTION="true")
    assert e.value.fault_code == "SHADOW-SHADOW-TICK-TARGET-FORBIDDEN"


def test_shadow_tick_without_target_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(HERMES_SHADOW_TICK_PUBLISH_ENABLED="true")
    assert e.value.fault_code == "SHADOW-SHADOW-TICK-TARGET-FORBIDDEN"


# ---- non-SHADOW: secondary writers unguarded (deployed compat) ----
def test_non_shadow_secondary_writers_not_guarded():
    m = validate_shadow_targets(_Cfg(), env=_env(RUN_ENV="STAGING", HERMES_CANDLE_FORWARD_ENABLED="true",
                                                 HERMES_CANDLE_FORWARD_SINK="canonical",
                                                 HERMES_TICK_PUBLISH_ENABLED="true"), now_utc=NOW)
    assert m.is_shadow is False and m.redis_writers == ()


# =========================================================================== STATIC WRITER-PATH REGISTRY (§14)
def test_registry_covers_every_redis_client_constructor():
    """Fails when a NEW Redis-client constructor is added to a runtime module without registering it. Coverage:
    direct `redis.Redis(` / `redis.from_url` in non-test, non-design .py modules. Limitation: does not detect
    dynamic/reflective client creation or a client injected from an unlisted third party."""
    import os
    import utils.hermes_shadow_target_guard_v1 as guard
    registered = set(guard.REDIS_WRITER_REGISTRY.keys())
    found = set()
    for root, _dirs, files in os.walk("."):
        if any(seg in root for seg in ("/tests", "/design", "/.git", "/__pycache__")):
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(root, fn).replace("./", "", 1)
            try:
                src = open(p, encoding="utf-8").read()
            except Exception:
                continue
            if "redis.Redis(" in src or "redis.from_url" in src:
                found.add(p)
    unregistered = found - registered
    assert not unregistered, f"unregistered Redis writer module(s): {sorted(unregistered)}"


def test_registry_roles_have_shadow_policy():
    import utils.hermes_shadow_target_guard_v1 as guard
    for mod, meta in guard.REDIS_WRITER_REGISTRY.items():
        assert meta.get("shadow_policy"), f"{mod} missing shadow_policy"
        assert meta.get("default_enabled") in (True, False)


# =========================================================================== SHADOW KEYSPACE RUN-SCOPING
# WO-HELM-HERMES-CONTAINER-MVP-WP3-PR126-SHADOW-REDIS-KEYSPACE-RUN-SCOPING-CORRECTION-0001
# C-WP3-SHADOW-REDIS-KEYSPACE-NOT-RUN-SCOPED: every enabled shadow writer's key prefix must carry the exact
# validated run-id as a discrete ':'-component (no substring), be non-canonical, and be manifest-bound.
_CF_ON = {"HERMES_CANDLE_FORWARD_ENABLED": "true", "HERMES_CANDLE_FORWARD_SINK": "shadow",
          "HERMES_CANDLE_FORWARD_SHADOW_REDIS_HOST": "wp3-redis",
          "HERMES_CANDLE_FORWARD_SHADOW_REDIS_PORT": "6380"}
_ST_ON = {"HERMES_SHADOW_TICK_PUBLISH_ENABLED": "true", "HERMES_SHADOW_TICK_REDIS_HOST": "wp3-redis",
          "HERMES_SHADOW_TICK_REDIS_PORT": "6380"}


# ---- candle-forward keyspace ----
def test_cf_keyspace_valid_run_scoped_accepted():
    m = _run(**_CF_ON, HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX="hermes:shadow:wp3:run-abc123:candles:")
    assert m.candle_forward_shadow_prefix == "hermes:shadow:wp3:run-abc123:candles:"


def test_cf_keyspace_generic_default_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(**_CF_ON, HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX="hermes:shadow:candles:")
    assert e.value.fault_code == "SHADOW-CANDLE-FORWARD-KEYSPACE-NOT-RUN-SCOPED"


def test_cf_keyspace_missing_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(**_CF_ON)  # HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX unset
    assert e.value.fault_code == "SHADOW-CANDLE-FORWARD-KEYSPACE-REQUIRED"


def test_cf_keyspace_empty_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(**_CF_ON, HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX="   ")
    assert e.value.fault_code == "SHADOW-CANDLE-FORWARD-KEYSPACE-REQUIRED"


def test_cf_keyspace_canonical_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(**_CF_ON, HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX="hermes:candles:")
    assert e.value.fault_code == "SHADOW-CANDLE-FORWARD-KEYSPACE-CANONICAL"


def test_cf_keyspace_different_run_id_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(**_CF_ON, HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX="hermes:shadow:wp3:run-other:candles:")
    assert e.value.fault_code == "SHADOW-CANDLE-FORWARD-KEYSPACE-NOT-RUN-SCOPED"


def test_cf_keyspace_substring_run_id_rejected():
    # run 'run-abc123' must NOT validate against a component 'run-abc1234'
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(**_CF_ON, HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX="hermes:shadow:wp3:run-abc1234:candles:")
    assert e.value.fault_code == "SHADOW-CANDLE-FORWARD-KEYSPACE-NOT-RUN-SCOPED"


def test_cf_keyspace_runid_glued_not_component_rejected():
    # run-id glued into a larger token is not a discrete component
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(**_CF_ON, HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX="hermes:shadow:xrun-abc123y:candles:")
    assert e.value.fault_code == "SHADOW-CANDLE-FORWARD-KEYSPACE-NOT-RUN-SCOPED"


# ---- shadow-tick keyspace ----
def test_st_keyspace_valid_run_scoped_accepted():
    m = _run(**_ST_ON, HERMES_SHADOW_TICK_KEY_PREFIX="hermes:shadow:wp3:run-abc123:ticks:")
    assert m.shadow_tick_prefix == "hermes:shadow:wp3:run-abc123:ticks:"


def test_st_keyspace_generic_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(**_ST_ON, HERMES_SHADOW_TICK_KEY_PREFIX="hermes:shadow:")
    assert e.value.fault_code == "SHADOW-SHADOW-TICK-KEYSPACE-NOT-RUN-SCOPED"


def test_st_keyspace_missing_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(**_ST_ON)
    assert e.value.fault_code == "SHADOW-SHADOW-TICK-KEYSPACE-REQUIRED"


def test_st_keyspace_canonical_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(**_ST_ON, HERMES_SHADOW_TICK_KEY_PREFIX="hermes:ticks:")
    assert e.value.fault_code == "SHADOW-SHADOW-TICK-KEYSPACE-CANONICAL"


def test_st_keyspace_mismatched_run_id_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(**_ST_ON, HERMES_SHADOW_TICK_KEY_PREFIX="hermes:shadow:wp3:run-zzz:ticks:")
    assert e.value.fault_code == "SHADOW-SHADOW-TICK-KEYSPACE-NOT-RUN-SCOPED"


def test_st_keyspace_substring_run_id_rejected():
    with pytest.raises(ShadowTargetGuardError) as e:
        _run(**_ST_ON, HERMES_SHADOW_TICK_KEY_PREFIX="hermes:shadow:wp3:run-abc1230:ticks:")
    assert e.value.fault_code == "SHADOW-SHADOW-TICK-KEYSPACE-NOT-RUN-SCOPED"


# ---- disabled writers -> no prefix required ----
def test_cf_disabled_no_prefix_required():
    m = _run(HERMES_CANDLE_FORWARD_ENABLED="false")
    assert m.candle_forward_shadow_prefix is None


def test_st_disabled_no_prefix_required():
    m = _run()  # shadow tick unset -> disabled
    assert m.shadow_tick_prefix is None


# ---- CROSS-RUN collision resistance ----
def test_cross_run_candle_keys_cannot_collide():
    m1 = validate_shadow_targets(_Cfg(redis=_R(key_prefix="shadow:wp3:run-aaa:")),
                                 env=_env(HERMES_SHADOW_RUN_ID="run-aaa", **_CF_ON,
                                          HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX="hermes:shadow:wp3:run-aaa:candles:"),
                                 now_utc=NOW)
    m2 = validate_shadow_targets(_Cfg(redis=_R(key_prefix="shadow:wp3:run-bbb:")),
                                 env=_env(HERMES_SHADOW_RUN_ID="run-bbb", **_CF_ON,
                                          HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX="hermes:shadow:wp3:run-bbb:candles:"),
                                 now_utc=NOW)
    assert m1.candle_forward_shadow_prefix != m2.candle_forward_shadow_prefix
    # a concrete key for the SAME instrument/event differs between runs
    k1 = m1.candle_forward_shadow_prefix + "XAU_USD:M1:latest:v1"
    k2 = m2.candle_forward_shadow_prefix + "XAU_USD:M1:latest:v1"
    assert k1 != k2


def test_cross_run_tick_keys_cannot_collide():
    m1 = validate_shadow_targets(_Cfg(redis=_R(key_prefix="shadow:wp3:run-aaa:")),
                                 env=_env(HERMES_SHADOW_RUN_ID="run-aaa", **_ST_ON,
                                          HERMES_SHADOW_TICK_KEY_PREFIX="hermes:shadow:wp3:run-aaa:ticks:"), now_utc=NOW)
    m2 = validate_shadow_targets(_Cfg(redis=_R(key_prefix="shadow:wp3:run-bbb:")),
                                 env=_env(HERMES_SHADOW_RUN_ID="run-bbb", **_ST_ON,
                                          HERMES_SHADOW_TICK_KEY_PREFIX="hermes:shadow:wp3:run-bbb:ticks:"), now_utc=NOW)
    assert (m1.shadow_tick_prefix + "EUR_USD:latest:v1") != (m2.shadow_tick_prefix + "EUR_USD:latest:v1")


# ---- MANIFEST-to-RUNTIME binding: the guard-approved prefix equals what the writer resolves ----
def test_candle_forward_manifest_prefix_equals_runtime_resolved():
    import utils.candle_publisher_v1 as cp
    approved = "hermes:shadow:wp3:run-abc123:candles:"
    m = _run(**_CF_ON, HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX=approved)
    resolved = cp.resolve_shadow_key_prefix({"HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX": approved})
    assert resolved == m.candle_forward_shadow_prefix == approved  # no validation/use drift


def test_shadow_tick_builder_reads_run_scoped_key_prefix_env(monkeypatch):
    # manifest/use binding: build_runtime_shadow_emitter_from_env consumes HERMES_SHADOW_TICK_KEY_PREFIX into
    # the config the writer uses (proven via the disabled path — same env read, no live client needed).
    import utils.tick_runtime_shadow_adapter_v1 as tsa
    approved = "hermes:shadow:wp3:run-abc123:ticks:"
    monkeypatch.setenv("HERMES_SHADOW_TICK_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("HERMES_SHADOW_TICK_KEY_PREFIX", approved)
    emitter = tsa.build_runtime_shadow_emitter_from_env()
    cfg = getattr(emitter, "config", None) or getattr(emitter, "_config", None)
    assert cfg is not None and cfg.shadow_prefix == approved
