"""HERMES deployment runtime-configuration SCHEMA v1 — the single source of truth.
WO-HELM-HERMES-COMPLETE-TRACKED-RUNTIME-CONFIG-AND-XAU-PARITY-0001.

One machine-readable record per deployment-contract field. Drives the fail-closed preflight, the safe effective-config
renderer, the XAU-parity checker, the environment template and the tests — so there is no hand-maintained field list to
drift. NO secret VALUES ever appear here; secret/authority fields are represented by classification only.

Field record keys:
  name                canonical, environment-neutral variable name (clean names, never DEV_*).
  type               "bool" | "int" | "enum" | "csv" | "string" | "sha40" | "iso8601".
  required           True if the deployment contract must supply it (fail-closed if absent and no safe default).
  default            safe explicit default IFF one genuinely exists and equals current authorised behaviour, else None.
  enum / min / max    allowed values / integer bounds where applicable.
  must_equal          a value the governed dark/parity contract fixes (e.g. master=false); preflight fails otherwise.
  secret             True -> value is a secret, supplied EXTERNALLY, never tracked/logged.
  authority_bearing   True -> value grants authority (e.g. activation token); external, presence-checked only.
  host_specific      True -> environment-specific value supplied via the governed env file (not a code default).
  advanced_v1        True -> part of the Advanced-v1 expansion/family contract.
  xau_parity_critical True -> omission or change breaks XAU runtime parity (load-bearing in the parity gate).
  deprecated_aliases  legacy names (e.g. DEV_DB_PORT) that map to this canonical name (fail-loud on conflict).
  feature_group      grouping for docs / renderer.
  consequence        deployment consequence if omitted/wrong.
  current_effective   the CURRENT AUTHORISED non-secret value (or a classification token for secret/authority).
"""
from __future__ import annotations

# classification tokens for non-recordable values
PRESENT_BY_SECRET_REFERENCE = "PRESENT_BY_SECRET_REFERENCE"
EXTERNAL_REQUIRED = "EXTERNAL_REQUIRED"

_XAU = "XAU_USD"
_TF_FULL = "M1,M5,M15,H1,H4,D1"
_INSTR14 = "XAU_USD,XAG_USD,XPT_USD,XCU_USD,GBP_USD,EUR_USD,USD_JPY,AUD_USD,NZD_USD,USD_CAD,USD_CHF,EUR_GBP,WTICO_USD,SPX500_USD"


def _f(name, **kw):
    rec = {"name": name, "type": "string", "required": False, "default": None, "enum": None, "min": None, "max": None,
           "must_equal": None, "secret": False, "authority_bearing": False, "host_specific": False,
           "advanced_v1": False, "xau_parity_critical": False, "deprecated_aliases": [], "feature_group": "misc",
           "consequence": "", "current_effective": None}
    rec.update(kw)
    return rec


def _bool_pair(prefix, group, current="true", parity=True, adv1=False):
    """Emit the common ENABLED/AUTHORISED pair for a publisher control."""
    return [
        _f(f"{prefix}_ENABLED", type="bool", required=True, default=current, xau_parity_critical=parity,
           advanced_v1=adv1, feature_group=group, current_effective=current,
           consequence="publisher disabled -> XAU output regression"),
        _f(f"{prefix}_AUTHORISED", type="bool", required=True, default=current, xau_parity_critical=parity,
           advanced_v1=adv1, feature_group=group, current_effective=current,
           consequence="publisher unauthorised -> XAU output regression"),
    ]


FIELDS = [
    # ---------- identity / image ----------
    _f("SOURCE_SHA", type="sha40", required=True, feature_group="identity",
       consequence="UNKNOWN provenance; unpromotable candidate", current_effective=EXTERNAL_REQUIRED),
    _f("BUILD_UTC", type="iso8601", required=True, feature_group="identity",
       consequence="build identity invalid", current_effective=EXTERNAL_REQUIRED),
    _f("HERMES_IMAGE_REF", type="string", required=True, feature_group="identity",
       consequence="mutable/unpinned image; non-reproducible runtime", current_effective=EXTERNAL_REQUIRED),
    _f("HERMES_IMAGE_TAG", type="string", required=True, feature_group="identity",
       consequence="image tag unresolved", current_effective=EXTERNAL_REQUIRED),
    _f("HERMES_REQUIRE_DIGEST_PIN", type="bool", required=False, default="true", feature_group="identity",
       current_effective="true", consequence="mutable-tag deploy permitted"),

    # ---------- DB (canonical clean names; DEV_* are deprecated aliases) ----------
    _f("DB_HOST", type="string", required=True, host_specific=True, xau_parity_critical=True, feature_group="db",
       deprecated_aliases=["DEV_DB_HOST"], current_effective="192.168.11.10", consequence="DB unreachable -> boot-gate fail"),
    _f("DB_PORT", type="int", required=True, min=1, max=65535, host_specific=True, xau_parity_critical=True,
       feature_group="db", deprecated_aliases=["DEV_DB_PORT"], must_equal="3307", current_effective="3307",
       consequence="silent 3306 default -> DB unreachable -> restart loop"),
    _f("DB_NAME", type="string", required=True, xau_parity_critical=True, feature_group="db",
       current_effective="tradingSignals", consequence="wrong schema / boot-gate fail"),
    _f("DB_USER", type="string", required=True, feature_group="db", deprecated_aliases=["DEV_DB_USER"],
       current_effective=EXTERNAL_REQUIRED, consequence="auth fail"),
    _f("DB_PASSWORD", type="string", required=True, secret=True, feature_group="db",
       deprecated_aliases=["DEV_DB_PASSWORD"], current_effective=PRESENT_BY_SECRET_REFERENCE, consequence="auth fail"),

    # ---------- Redis (app canonical clean names) ----------
    _f("REDIS_HOST", type="string", required=True, host_specific=True, xau_parity_critical=True, feature_group="redis",
       deprecated_aliases=["DEV_REDIS_HOST"], current_effective="192.168.11.10", consequence="publication/routing to wrong Redis"),
    _f("REDIS_PORT", type="int", required=True, min=1, max=65535, host_specific=True, xau_parity_critical=True,
       feature_group="redis", deprecated_aliases=["DEV_REDIS_PORT"], default="6379", current_effective="6379", consequence="wrong Redis port"),
    _f("REDIS_DB", type="int", required=True, min=0, max=15, xau_parity_critical=True, feature_group="redis",
       deprecated_aliases=["DEV_REDIS_DB"], default="0", current_effective="0", consequence="wrong Redis DB -> split-brain"),
    _f("REDIS_KEY_PREFIX", type="string", required=True, xau_parity_critical=True, feature_group="redis",
       deprecated_aliases=["DEV_REDIS_KEY_PREFIX"], default="hermes:", current_effective="hermes:", consequence="key namespace drift"),
    _f("REDIS_PASSWORD", type="string", required=False, secret=True, feature_group="redis",
       deprecated_aliases=["DEV_REDIS_PASSWORD"], current_effective=PRESENT_BY_SECRET_REFERENCE, consequence="auth fail if required"),

    # ---------- canonical-Redis candle publishing route ----------
    _f("HERMES_CANDLE_CANONICAL_REDIS_HOST", type="string", required=True, host_specific=True, xau_parity_critical=True,
       feature_group="canonical_redis", current_effective="192.168.11.10", consequence="canonical candle publish misrouted/broken"),
    _f("HERMES_CANDLE_CANONICAL_REDIS_PORT", type="int", required=True, min=1, max=65535, host_specific=True,
       xau_parity_critical=True, feature_group="canonical_redis", default="6379", current_effective="6379", consequence="canonical publish wrong port"),
    _f("HERMES_CANDLE_CANONICAL_REDIS_DB", type="int", required=True, min=0, max=15, xau_parity_critical=True,
       feature_group="canonical_redis", default="0", current_effective="0", consequence="canonical publish wrong DB"),
    _f("HERMES_CANDLE_CANONICAL_ACTIVATION_APPROVAL", type="string", required=True, authority_bearing=True,
       xau_parity_critical=True, feature_group="canonical_redis", current_effective=EXTERNAL_REQUIRED,
       consequence="canonical activation refused -> candle canonical publish disabled"),
    _f("HERMES_CANDLE_CANONICAL_INSTRUMENTS", type="csv", required=True, default=_XAU, xau_parity_critical=True,
       feature_group="canonical_redis", current_effective=_INSTR14,
       consequence="base candle latest+history instrument scope (WO-...-CORE-CANDLE-WICK-HISTORY: full enabled set for M1/M5/M15/H1)"),
    # WO-...-CORE-CANDLE-WICK-HISTORY: H4 has its OWN allowlist, DECOUPLED from the base set above, so base
    # M1/M5/M15/H1 fan out to all enabled instruments while H4 stays on its governed XAU grid. Registered (required)
    # but NOT parity-compared here (no new compose-render plumbing); its XAU scope is asserted by unit tests.
    _f("HERMES_CANDLE_H4_INSTRUMENTS", type="csv", required=True, default=_XAU,
       feature_group="xau_operational", current_effective=_XAU,
       consequence="H4 derivation scope (kept XAU_USD; non-XAU H4/D1 held pending session-anchor ruling)"),

    # ---------- runtime context ----------
    _f("ENVIRONMENT", type="enum", required=True, enum=["DEV", "STAGING", "PROD"], xau_parity_critical=True,
       feature_group="runtime", current_effective="DEV", consequence="wrong env prefix resolution (DEV_* mapping)"),
    _f("RUN_ENV", type="enum", required=True, enum=["STAGING", "PROD"], default="STAGING", xau_parity_critical=True,
       feature_group="runtime", current_effective="STAGING", consequence="purge/backfill inertness changes"),
    _f("SIGNAL_PORT", type="int", required=True, min=1, max=65535, default="8211", feature_group="runtime",
       current_effective="8211", consequence="HTTP surface unreachable / healthcheck fail"),
    _f("SIGNAL_HOST", type="string", required=False, default="0.0.0.0", feature_group="runtime", current_effective="0.0.0.0"),
    _f("OANDA_ENVIRONMENT", type="enum", required=True, enum=["live", "practice"], xau_parity_critical=True,
       feature_group="oanda", current_effective="live", consequence="wrong OANDA endpoint"),
    _f("OANDA_API_KEY", type="string", required=True, secret=True, feature_group="oanda",
       current_effective=PRESENT_BY_SECRET_REFERENCE, consequence="no market data"),
    _f("OANDA_ACCOUNT_ID", type="string", required=True, secret=True, feature_group="oanda",
       current_effective=PRESENT_BY_SECRET_REFERENCE, consequence="no market data"),
    _f("INSTRUMENTS", type="csv", required=True, default=_INSTR14, xau_parity_critical=True, feature_group="stream",
       current_effective=_INSTR14, consequence="OANDA subscription scope change (must remain the 14-instrument set)"),

    # ---------- expansion DARK boundary (must be fixed) ----------
    _f("HERMES_ADVANCED_V1_MASTER_ENABLED", type="bool", required=True, must_equal="false", advanced_v1=True,
       xau_parity_critical=True, feature_group="expansion_dark", current_effective="false",
       consequence="expansion master ON -> seven-new publication risk"),
    _f("HERMES_ADVANCED_V1_PUBLISHER_MODE", type="enum", required=True, enum=["DISABLED", "SHADOW", "ACTIVE"],
       must_equal="DISABLED", advanced_v1=True, xau_parity_critical=True, feature_group="expansion_dark",
       current_effective="DISABLED", consequence="publisher SHADOW/ACTIVE -> expansion publication"),

    # ---------- hard safety invariants ----------
    _f("CONSUMER_LIVE", type="bool", required=True, must_equal="false", default="false", feature_group="safety",
       current_effective="false", consequence="consumer enabled -> boundary breach"),
    _f("HERMES_BACKFILL_EXECUTION_ENABLED", type="bool", required=True, must_equal="false", default="false",
       feature_group="safety", current_effective="false", consequence="prohibited backfill executor enabled"),

    # ---------- publisher runtime ownership / process model ----------
    _f("HERMES_PUBLISHER_RUNTIME_OWNER", type="enum", required=True, enum=["in_process", "external"], default="in_process",
       xau_parity_critical=True, feature_group="process", current_effective="in_process",
       consequence="process-topology change (second publisher)"),
]

# ---------- Advanced-v1 five family controls (enable+authorise pairs) ----------
for p in ("HERMES_TICK_PUBLISH", "HERMES_INDICATOR_PUBLISH", "HERMES_GAPS_PUBLISH",
          "HERMES_BACKFILL_STATUS_PUBLISH", "HERMES_FEED_HEALTH_PUBLISH"):
    FIELDS += _bool_pair(p, "adv1_families", adv1=True)

# ---------- XAU operational publisher controls (enable+authorise pairs) ----------
for p in ("HERMES_CANDLE_PUBLISH", "HERMES_CANDLE_D1_PUBLISH", "HERMES_CANDLE_D1_HISTORY",
          "HERMES_CANDLE_FEATURE_PUBLISH", "HERMES_CANDLE_HISTORY_FORWARD", "HERMES_QUOTE_PUBLISH",
          "HERMES_SESSION_PUBLISH", "HERMES_LEVEL_PUBLISH", "HERMES_INSTRUMENT_CATALOG_PUBLISH",
          "HERMES_D1_WARMSTART", "HERMES_H4_WARMSTART", "HERMES_PUBLISHER_RUNTIME",
          "HERMES_D1_HISTORY_BACKFILL", "HERMES_REDIS_CONTROL_PLANE"):
    FIELDS += _bool_pair(p, "xau_operational")

# ---------- XAU operational scalar / scope / timeframe controls ----------
FIELDS += [
    _f("HERMES_CANDLE_FORWARD_ENABLED", type="bool", required=True, default="true", xau_parity_critical=True,
       feature_group="xau_operational", current_effective="true", consequence="candle forwarding off -> XAU regression"),
    _f("HERMES_CANDLE_FORWARD_SINK", type="enum", required=True, enum=["canonical", "shadow"], default="canonical",
       xau_parity_critical=True, feature_group="xau_operational", current_effective="canonical", consequence="sink change"),
    _f("HERMES_CANDLE_H4_PUBLISH_ENABLED", type="bool", required=True, default="true", xau_parity_critical=True,
       feature_group="xau_operational", current_effective="true", consequence="H4 publish off"),
    _f("HERMES_CANDLE_INSTRUMENTS_GROUP", type="csv", required=False, default=_XAU, feature_group="xau_operational",
       current_effective=_XAU),
    _f("HERMES_CANDLE_D1_INSTRUMENTS", type="csv", required=True, default=_XAU, xau_parity_critical=True,
       feature_group="xau_operational", current_effective=_XAU, consequence="D1 scope change"),
    _f("HERMES_CANDLE_D1_SOURCE_TIMEFRAME", type="enum", required=True, enum=["H4", "H1"], default="H4",
       xau_parity_critical=True, feature_group="xau_operational", current_effective="H4", consequence="D1 source tf change"),
    _f("HERMES_CANDLE_FEATURE_PUBLISH_INSTRUMENTS", type="csv", required=True, default=_XAU, xau_parity_critical=True,
       feature_group="xau_operational", current_effective=_XAU),
    _f("HERMES_CANDLE_FEATURE_PUBLISH_TIMEFRAMES", type="csv", required=True, default=_TF_FULL, xau_parity_critical=True,
       feature_group="xau_operational", current_effective=_TF_FULL, consequence="feature timeframe change"),
    _f("HERMES_CANDLE_FEATURE_D1_AUTHORISED", type="bool", required=True, default="true", xau_parity_critical=True,
       feature_group="xau_operational", current_effective="true"),
    _f("HERMES_CANDLE_HISTORY_FORWARD_INSTRUMENTS", type="csv", required=True, default=_XAU, xau_parity_critical=True,
       feature_group="xau_operational", current_effective=_INSTR14,
       consequence="forward candle-history instrument scope (WO-...-CORE-CANDLE-WICK-HISTORY: full enabled set)"),
    _f("HERMES_CANDLE_HISTORY_WARMSTART_ENABLED", type="bool", required=False, default="true",
       feature_group="xau_operational", current_effective="true",
       consequence="bounded MariaDB->Redis base candle-history warm-start seed on boot"),
    _f("HERMES_CANDLE_HISTORY_WARMSTART_AUTHORISED", type="bool", required=False, default="true",
       feature_group="xau_operational", current_effective="true"),
    _f("HERMES_CANDLE_HISTORY_WARMSTART_HOURS", type="int", required=False, default="24", min=1, max=168,
       feature_group="xau_operational", current_effective="24", consequence="warm-start lookback window (bounded)"),
    _f("HERMES_CANDLE_HISTORY_FORWARD_TIMEFRAMES", type="csv", required=True, default="M1,M5,M15,H1,H4",
       xau_parity_critical=True, feature_group="xau_operational", current_effective="M1,M5,M15,H1,H4",
       consequence="history forward timeframe change"),
    _f("HERMES_INDICATOR_PUBLISH_INSTRUMENTS", type="csv", required=True, default=_XAU, xau_parity_critical=True,
       feature_group="xau_operational", current_effective=_XAU),
    _f("HERMES_INDICATOR_PUBLISH_TIMEFRAMES", type="csv", required=True, default=_TF_FULL, xau_parity_critical=True,
       feature_group="xau_operational", current_effective=_TF_FULL, consequence="indicator timeframe change (M1..D1)"),
    _f("HERMES_INDICATOR_D1_AUTHORISED", type="bool", required=True, default="true", xau_parity_critical=True,
       feature_group="xau_operational", current_effective="true"),
    _f("HERMES_QUOTE_PUBLISH_INSTRUMENTS", type="csv", required=True, default=_XAU, xau_parity_critical=True,
       feature_group="xau_operational", current_effective=_XAU),
    _f("HERMES_SESSION_PUBLISH_INSTRUMENTS", type="csv", required=True, default=_XAU, xau_parity_critical=True,
       feature_group="xau_operational", current_effective=_XAU),
    _f("HERMES_LEVEL_PUBLISH_INSTRUMENTS", type="csv", required=True, default=_XAU, xau_parity_critical=True,
       feature_group="xau_operational", current_effective=_XAU),
    _f("HERMES_LEVEL_PUBLISH_SCOPES", type="csv", required=True, default="session,intraday,daily", xau_parity_critical=True,
       feature_group="xau_operational", current_effective="session,intraday,daily", consequence="level scope change"),
    _f("HERMES_LEVEL_D1_AUTHORISED", type="bool", required=True, default="true", xau_parity_critical=True,
       feature_group="xau_operational", current_effective="true"),
    _f("HERMES_INSTRUMENT_CATALOG_PUBLISH_INSTRUMENTS", type="csv", required=True, default=_XAU, xau_parity_critical=True,
       feature_group="xau_operational", current_effective=_XAU),
    _f("HERMES_FEED_HEALTH_PUBLISH_INSTRUMENTS", type="csv", required=True, default=_XAU, xau_parity_critical=True,
       feature_group="xau_operational", current_effective=_XAU),
    _f("HERMES_TICK_PUBLISH_INSTRUMENTS", type="csv", required=True, default=_XAU, xau_parity_critical=True,
       feature_group="xau_operational", current_effective=_XAU),
    _f("HERMES_PUBLISHER_RUNTIME_OWNER", type="enum", required=True, enum=["in_process", "external"], default="in_process",
       xau_parity_critical=True, feature_group="process", current_effective="in_process"),
    _f("HERMES_D1_HISTORY_BACKFILL_DRY_RUN", type="bool", required=True, default="false", xau_parity_critical=True,
       feature_group="xau_operational", current_effective="false", consequence="history warm-start mode change"),
    _f("HERMES_D1_HISTORY_BACKFILL_MAX_CANDLES", type="int", required=True, min=1, max=10000, default="60",
       xau_parity_critical=True, feature_group="xau_operational", current_effective="60"),
    _f("HERMES_D1_HISTORY_BACKFILL_MIN_DEPTH", type="int", required=True, min=1, max=10000, default="26",
       xau_parity_critical=True, feature_group="xau_operational", current_effective="26"),
]

# de-duplicate by name keeping the RICHER (last) definition — some names appear in both a pair loop and the scalar list
_seen = {}
for rec in FIELDS:
    _seen[rec["name"]] = rec
FIELDS = list(_seen.values())

# every parity-critical field must document a deployment consequence (fail-loud completeness)
for rec in FIELDS:
    if rec["xau_parity_critical"] and not rec["consequence"]:
        rec["consequence"] = "XAU parity-critical control; omission/change regresses the authorised XAU runtime"

BY_NAME = {r["name"]: r for r in FIELDS}
PARITY_CRITICAL = [r["name"] for r in FIELDS if r["xau_parity_critical"]]
SECRET_FIELDS = [r["name"] for r in FIELDS if r["secret"]]
AUTHORITY_FIELDS = [r["name"] for r in FIELDS if r["authority_bearing"]]
HOST_FIELDS = [r["name"] for r in FIELDS if r["host_specific"]]
MUST_EQUAL = {r["name"]: r["must_equal"] for r in FIELDS if r["must_equal"] is not None}
ALIAS_TO_CANONICAL = {a: r["name"] for r in FIELDS for a in r["deprecated_aliases"]}
SCHEMA_VERSION = "v1"


def to_json():
    import json
    return json.dumps({"schema_version": SCHEMA_VERSION, "fields": FIELDS}, indent=2, sort_keys=True)


if __name__ == "__main__":
    print(to_json())
