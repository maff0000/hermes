#!/usr/bin/env python3
"""GOLD-AUD-A synthetic HERMES -> HELIOS mapping probe (SOURCE PHASE ONLY).

SYNTHETIC: every number below is invented for the probe. Nothing here is an observed production value, and no
network, Redis or database is touched. The probe imports the pure builders of a HERMES checkout and the contract
models of a HELIOS checkout, builds envelopes the way the HERMES source builds them, and tries to load them
into the HELIOS MarketFactFrame models, to show which compatibility gaps are real at the code level.

usage: python3 -I synthetic_mapping_probe.py <hermes_checkout> <helios_checkout>
"""
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal

hermes_root, helios_root = sys.argv[1], sys.argv[2]
sys.path.insert(0, hermes_root)
sys.path.insert(0, helios_root)

from utils import candle_contract_v1 as cc            # noqa: E402  (HERMES, pure)
from utils import hermes_indicators_v1 as ind         # noqa: E402  (HERMES, pure)
from helios.contracts.market_fact import Candle, IndicatorSet, Provenance, MarketFactFrame  # noqa: E402
from helios.errors import ContractViolationError      # noqa: E402

UTC = timezone.utc
open_t = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)       # synthetic bar open
gen_t = datetime(2026, 1, 5, 9, 0, 1, tzinfo=UTC)     # synthetic publish time

results = []


def record(name, ok, detail=""):
    results.append({"case": name, "result": "ACCEPTED" if ok else "REJECTED", "detail": detail})


# 1. governed candle envelope as built by HERMES source, round-tripped through JSON text
env = cc.build_candle_contract(
    instrument="XAU_USD", timeframe="H1", timestamp_utc=open_t,
    ohlc={"open": 2399.0, "high": 2401.5, "low": 2397.5, "close": 2400.0, "volume": 600},
    is_closed=True, generated_at_utc=gen_t, source_timeframe="H1", source_count=1,
    expected_source_count=1, derivation=cc.DERIVATION_DIRECT, derivation_policy=cc.DERIVATION_POLICY_DIRECT,
    source_policy_epoch="DIRECT_NATIVE_V1")
cc.validate_candle_contract(env)
text = json.dumps(env)
doc = json.loads(text, parse_float=Decimal)           # adapter must parse floats as Decimal
d = doc["data"]

try:
    Candle(open=d["open"], high=d["high"], low=d["low"], close=d["close"], volume=d["volume"],
           complete=d["is_closed"], source=doc["provenance"]["source"])
    record("candle: map is_closed->complete, provenance.source->source, Decimal-parsed", True)
except ContractViolationError as exc:
    record("candle: mapped with Decimal parse", False, str(exc))

try:
    Candle(open=json.loads(text)["data"]["open"], high=2401.5, low=2397.5, close=2400.0, volume=600,
           complete=True, source="oanda")
    record("candle: raw JSON floats passed straight through", True)
except ContractViolationError as exc:
    record("candle: raw JSON floats passed straight through", False, "binary float refused")

try:
    Candle(open=d["open"], high=d["high"], low=d["low"], close=d["close"], volume=d["volume"],
           complete=d["is_closed"], source=doc["provenance"]["source"], is_closed=True)
    record("candle: HERMES field name is_closed passed through unmapped", True)
except Exception as exc:  # noqa: BLE001
    record("candle: HERMES field name is_closed passed through unmapped", False, type(exc).__name__)

# 2. governed indicator payload as built by HERMES source (ema_200 explicit null below depth)
ip = ind.build_indicator_contract(
    instrument="XAU_USD", timeframe="H1", generated_at_utc=gen_t, value_open_time_utc=open_t,
    indicators={"rsi_14": 55.1234, "ema_9": 2399.5, "ema_21": 2398.5, "ema_50": 2402.0, "ema_200": None,
                "atr_14": 3.0, "ema_12": 2399.0, "ema_26": 2398.0},
    freshness_state="FRESH")
idoc = json.loads(json.dumps(ip), parse_float=Decimal)
inds = idoc["indicators"]
facts = ("rsi_14", "ema_9", "ema_21", "ema_50", "ema_200", "atr_14")
try:
    s = IndicatorSet(**{k: inds[k] for k in facts})
    record("indicators: projection to the six HELIOS numeric facts, null kept as absent", True,
           "available=%s" % sorted(s.available))
except ContractViolationError as exc:
    record("indicators: projection to the six HELIOS numeric facts", False, str(exc))
try:
    IndicatorSet(**{k: inds[k] for k in facts}, ema_12=inds["ema_12"])
    record("indicators: extra published fields (ema_12) passed through", True)
except Exception as exc:  # noqa: BLE001
    record("indicators: extra published fields (ema_12) passed through", False, type(exc).__name__)

# 3. session vocabularies
for label, val in (("governed lowercase 'london'", "london"), ("governed 'overlap_ldn_ny'", "overlap_ldn_ny"),
                   ("legacy 'late_ny'", "late_ny"), ("uppercase 'LONDON'", "LONDON")):
    try:
        IndicatorSet(session=val)
        record("session: " + label, True)
    except ContractViolationError:
        record("session: " + label, False, "value pattern requires uppercase")

# 4. regime: legacy values accepted; governed surfaces publish none
try:
    IndicatorSet(regime="BULL_TREND")
    record("regime: legacy value BULL_TREND (source: legacy signal path only)", True)
except ContractViolationError as exc:
    record("regime: legacy value", False, str(exc))

# 5. legacy zero-fill: absent ema_200 published as 0 is accepted as a real value
try:
    z = IndicatorSet(ema_200=Decimal("0"))
    record("legacy zero-fill: ema_200 = 0 treated as present", True, "available=%s" % sorted(z.available))
except ContractViolationError as exc:
    record("legacy zero-fill: ema_200 = 0", False, str(exc))

# 6. provenance schema_version
for label, val in (("HERMES envelope schema_version 'v1'", "v1"), ("qualified 'hermes.market_fact/1.0.0'",
                                                                  "hermes.market_fact/1.0.0")):
    try:
        Provenance(source="oanda", schema_version=val, observed_at_utc=gen_t, ingested_at_utc=gen_t)
        record("provenance: " + label, True)
    except ContractViolationError:
        record("provenance: " + label, False, "schema_version must be '<namespace>/<x.y.z>'")

# 7. timestamp text emitted by HERMES (millisecond precision, trailing Z)
try:
    from helios.clock import from_iso8601_utc
    t = from_iso8601_utc(env["data"]["timestamp_utc"])
    record("timestamp: HERMES ms-precision text parses to aware UTC", True, t.isoformat())
except ContractViolationError as exc:
    record("timestamp: HERMES ms-precision text", False, str(exc))

print(json.dumps({"synthetic": True, "hermes_key_example": env["key"], "results": results}, indent=2))
