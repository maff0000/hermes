# Contract ratifications — tick, candle/wick, indicators, gap, backfill, SQL, keys (§13-21)

All contracts already exist and are `{instrument}`-parameterised; this ratifies them as the generic engine.

## Redis key factory (§13, §19, §24) — ONE module
Consolidate the scattered per-module `canonical_key()` into `utils/hermes_redis_keys_v1.py`. Existing key
STRINGS preserved byte-for-byte (no consumer break). One factory produces ALL instrument/timeframe keys:
```
tick:        hermes:ticks:<INST>:latest:v1                 (+ aggregate hermes:ticks:latest:v1)
candle:      hermes:candles:<INST>:<TF>:latest:v1
candle_hist: hermes:candles:<INST>:<TF>:history:v1:<open_epoch>  (+ :index ZSET)
indicators:  hermes:indicators:<INST>:<TF>:v1
features:    hermes:candle_features:<INST>:<TF>:v1
quote:       hermes:quote:<INST>:v1
gaps:        hermes:gaps:<INST>:v1        (currently literal XAU_USD -> parameterise)
backfill:    hermes:backfill:status:<INST>:v1   (currently literal -> parameterise)
```
One serializer (JSON envelope, ISO-8601 ms UTC). No instrument-specific schema.

## Tick contract (§13) — RATIFY existing `utils/tick_contract_v1.py`
Generic key `hermes:ticks:<INST>:latest:v1`; envelope: schema_version/contract/contract_version, generated_at,
valid_until, ttl_seconds(5), freshness_state(FRESH≤5s/DEGRADED 5-15s/STALE>15s/UNAVAILABLE), status,
reason_codes, provenance(publisher/source=oanda/received/published/registry_version), data(instrument, bid,
ask, mid, spread, source, seq[null in v1], contract_version). market-closed -> UNAVAILABLE envelope. TTL 5s
/ EX 10s. One key factory. **Already generic** — only the pilot allowlist gate widens.

## Candle + wick model (§15, §16) — RATIFY `utils/candle_contract_v1.py`; add explicit alias names
One shared schema for every instrument/timeframe. Present geometry fields: `body_high, body_low, body_size,
range_size, wick_high, wick_low, candle_direction` + completeness{source_count, expected_count}.
Invariants (identical for ALL instruments):
`high>=max(open,close)`; `low<=min(open,close)`; `high>=low`;
`wick_high(=upper_wick_size)=high-max(open,close)`; `wick_low(=lower_wick_size)=min(open,close)-low`;
`range_size=high-low`; `body_size=abs(close-open)`.
**Naming:** add explicit `upper_wick_size`/`lower_wick_size` as documented aliases of `wick_high`/`wick_low`
for forward clarity; retain legacy names for compatibility (semantics documented here). Only **precision**
and **price metadata** vary, by registry (`price_precision`, `tick_size`).
**Price authority (§16):** governed **MID = (bid+ask)/2**, uniform for OHLC (matches `signals:price.mid`).
Open=first-in-bucket mid, High/Low=max/min mid, Close=last-in-bucket mid; inclusion boundary = wall-clock TF
bucket; late/duplicate/out-of-order ticks handled by the one candle engine (dedupe by source ts, drop late
past seal, keep max/min); rounding = registry `price_precision`. Any variation is registry policy, not code.
*Validation item V-1: confirm the live aggregator's price source is MID before ratifying (currently derived
from tick mid); if it is bid or ask, set `price_authority` accordingly — still uniform + registry-driven.*

## Indicator engine (§17, §18) — RATIFY `utils/hermes_indicators_v1.py` methods (make explicit)
One generic engine: `calculate_indicators(instrument_metadata, timeframe, candle_history)`. Methods (as
implemented; documented to remove ambiguity):
- EMA 12/26/50: multiplier 2/(n+1), **SMA(n) seed**.
- RSI 14: **Cutler SMA-14** (simple average of gains/losses — NOT Wilder). *(Ratify Cutler explicitly; if
  Wilder is desired that is a deliberate future change, not an accident.)*
- ATR 14: **SMA of True Range (14)**.
- Bollinger: SMA(20) ± 2·StdDev. *Ratify sample-vs-population stddev explicitly in the oracle.*
- +DI/-DI 14, ADX 14: **Wilder** directional movement + smoothing (settle at 2·period).
- warm-up: values only emitted once enough completed candles exist; **completed candles only** (forming
  candle excluded). Precision = registry `price_precision`.
No per-instrument formula. Timeframes: **uniform M1/M5/M15/H1/H4/D1** for all 8 via registry
`enabled_timeframes` (default all six); vary only by real operational reason (registry, not ticker branch).

## Indicator Redis contract (§19) — RATIFY `hermes:indicators:<INST>:<TF>:v1`
One schema: contract_version, instrument, timeframe, source candle ts, completed state, close, indicator
fields, method metadata, warm-up state, source, calc ts, publish ts, freshness, fault, TTL(TF-scaled).
One key factory + serializer.

## Gap detection (§20) — one generic detector; per-instrument key
`hermes:gaps:<INST>:v1` (currently single literal XAU key → parameterise). Detector inputs: instrument
metadata, `market_hours_policy`, observed timestamps/sequences, expected cadence, `expected_freshness`.
Classifies: expected-closure / maintenance-break / sparse-valid / provider-pause / instrument-stall /
connection-loss / timestamp-gap / sequence-gap / late-tick / rejected-tick. No per-instrument detector.

## Backfill (§21) — one generic governed state machine
Stages separated: detection → proposal → approval → execution → verification → publication. Per-instrument
key `hermes:backfill:status:<INST>:v1`. Policy fields (eligible gaps, max range/records, source, provenance,
idempotency, dup control, SQL txn, candle+indicator recalc, redis refresh, retries, failure, **kill switch**,
audit) selected via registry `backfill_policy`. Execution is **separately gated** (WO-9). No ticker fork.
**No production backfill authorised under this WO.**

## Raw-tick SQL decision (§14) — RECOMMENDATION: **BOUNDED RETENTION**, one shared table
R2D2 found raw-tick persistence inactive. Recommend **bounded retention** on the existing single `ticks`
table, partitioned/indexed by `(instrument, timestamp)` (+ existing `seq`/`contract_version` from mig-015/016),
warm-tier days from `hermes_config` (mig-016). Rationale: enables candle high/low reconciliation, wick
auditability, sequence-gap forensics, incident replay — at bounded storage via batched writes + retention +
backpressure kill-switch. Reject full-unbounded (cost) and no-persistence (loses forensics). **No table per
ticker. No migration executed under this WO** — design only; activation is a separate gated WO.

## SQL candle model (§13 SQL) — one shared set
Existing `candles_M1/M5/M15/H1/H4/D1` shared tables keyed `UNIQUE(instrument,timestamp)`. All 8 instruments
persist to the same tables; no per-ticker table. Geometry recomputable from OHLC; optionally denormalise
wick/body columns later (backlog). UTC only.
