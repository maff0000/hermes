# GOLD-AUD-A: HELIOS consumer-side mapping and HERMES to HELIOS compatibility gaps (CP-1)

Source phase. Sources: `maff0000/hermes` @ `ca1901789f8c7a7e085d02ebde8f7264d90d669e`, `maff0000/HELIOS` @ `7f7193d55475e40583892b94dd461b1b64d6f1ef`. Citations `HELIOS:path:Lnn` refer to the HELIOS checkout; unprefixed refer to hermes. The column "PROD key and field" names the key family the source is coded to publish; whether it exists in production is `PENDING HELM PROTOCOL`.

## 1. What HELIOS consumes

HELIOS v1 ships no live HERMES client; every HERMES fact reaches it as a validated `MarketFactFrame` loaded from a checked-in fixture document (`HELIOS:helios/integration/hermes_boundary.py:L1-15`, `HELIOS:helios/runtime/feed.py:L1-10`). The frame is one instrument, one timeframe, one bar (`HELIOS:helios/contracts/market_fact.py:L197-215`): `instrument`, `timeframe`, `timestamp_utc` (bar open), `candle`, `indicators`, `provenance`.

The model is a closed world: an unknown field is a loud failure (`HELIOS:helios/integration/hermes_boundary.py:L49`), binary floats are refused (`HELIOS:helios/contracts/_fields.py:L5-9`, `L80-82`), and an absent indicator is `None`, never zero (`HELIOS:helios/contracts/market_fact.py:L100-108`).

## 2. Field-by-field mapping (`FACT_FIELDS`, 15 entries)

`FACT_FIELDS` = candle (open, high, low, close, volume, complete, source) + indicators (rsi_14, ema_9, ema_21, ema_50, ema_200, atr_14, regime, session) (`HELIOS:helios/contracts/market_fact.py:L27-43`).

| HELIOS fact | PROD key and field (governed source) | Transform needed | Source label | Observed (PROD) |
|---|---|---|---|---|
| open, high, low, close | `hermes:candles:XAU_USD:{TF}:latest:v1` -> `data.open/high/low/close` (6-decimal prices) | parse JSON numbers as exact decimals; HELIOS refuses floats | PROVEN | PENDING HELM PROTOCOL |
| volume | same -> `data.volume` | decimal; HERMES volume is tick count for live M1-derived bars and broker volume for repaired bars (`docs/hermes_h4_m30_candle_pipeline.md` volume section) | PROVEN | PENDING HELM PROTOCOL |
| complete | same -> `data.is_closed` | rename `is_closed` to `complete` | PROVEN | PENDING HELM PROTOCOL |
| source | same -> `provenance.source` ("oanda") | none (HELIOS source-name pattern is lower-case letters, digits, dot, underscore; "oanda" fits) | PROVEN | PENDING HELM PROTOCOL |
| timestamp_utc (frame) | same -> `data.timestamp_utc` (bar open, millisecond text, trailing Z) | none; HELIOS parses it (synthetic probe) | PROVEN | PENDING HELM PROTOCOL |
| rsi_14 | `hermes:indicators:XAU_USD:{TF}:v1` -> `indicators.rsi_14` | decimal parse; method is Cutler SMA 14, not Wilder (`utils/hermes_indicators_v1.py:L53`) | PROVEN | PENDING HELM PROTOCOL |
| ema_9, ema_21 | same -> `indicators.ema_9`, `ema_21` | decimal parse | PROVEN | PENDING HELM PROTOCOL |
| ema_50 | same -> `indicators.ema_50` | decimal parse; null below 50 closes; structurally null on D1 | PROVEN | PENDING HELM PROTOCOL |
| ema_200 | same -> `indicators.ema_200` | decimal parse; null below 200 closes; structurally null on D1 | PROVEN | PENDING HELM PROTOCOL |
| atr_14 | same -> `indicators.atr_14` | decimal parse; ATR is SMA of true range (`L55`) | PROVEN | PENDING HELM PROTOCOL |
| regime | no governed key. Legacy hash `signals:latest:{instrument}` field `regime` only, for M1, M5, M15 (`signal_builder.py:L932-933`, `main.py:L909-910`) | none possible from governed surfaces | MISSING (governed), PROVEN (legacy) | PENDING HELM PROTOCOL |
| session | `hermes:sessions:XAU_USD:v1` -> `current_session` (instrument-level, not per bar) or legacy hash `session` | uppercase it; map vocabulary; attach by bar time, not "now" | PARTIAL | PENDING HELM PROTOCOL |
| provenance.schema_version | not published (HERMES envelopes say "v1"; no `market_fact` string exists anywhere in the hermes tree, checked by grep) | adapter would have to stamp a string HERMES does not own | MISSING | not applicable |
| provenance.observed_at_utc | candle `generated_at_utc` (publish time) is the closest; bar close = open + timeframe is derivable | choose and document; HELIOS requires ingested >= observed | PARTIAL | PENDING HELM PROTOCOL |
| provenance.ingested_at_utc | none (consumer clock) | adapter clock | n/a | n/a |

Numeric fields HERMES publishes that HELIOS does not model and would reject if passed through: candle geometry (`body_*`, `wick_*`, `range_size`, `candle_direction`), `ema_12`, `ema_26`, Bollinger and ADX values, and all envelope status fields. The adapter must project, not forward.

## 3. Timeframe, alignment and freshness mapping

| Aspect | HERMES source | HELIOS | Compatibility |
|---|---|---|---|
| Timeframe codes | `M1 M5 M15 H1 H4 D1` (`utils/candle_contract_v1.py:L32`; `D` legacy never published) | `M1 M5 M15 H1 H4 D1`, also parses `1m 5m 15m 1h 4h 1d` (`HELIOS:helios/contracts/timeframe.py:L112-130`) | match. `M30` exists in neither Redis grid nor HELIOS |
| H4 and D1 anchor | governed H4 buckets start 22, 02, 06, 10, 14, 18 UTC and D1 at 22:00 UTC (`utils/hermes_control_plane_v1.py:L291-292`; `docs/config/hermes-configuration-reference.md:L48-51`); older text says UTC-aligned (`docs/hermes_h4_m30_candle_pipeline.md` boundary note; `utils/candle_payload_builders.py:L17-19`) | checks only minute equals zero for hour-and-above bars, so either anchor loads (`HELIOS:helios/contracts/timeframe.py:L148-161`) | loads either way; documentation conflict is a HERMES defect (D-A-08) |
| Bar timestamp meaning | bar open | bar open | match |
| Freshness | candle: `valid_until_utc` = publish + one timeframe; key EX = timeframe + buffer | age from bar close at most duration times a configured multiplier plus grace (`HELIOS:helios/contracts/freshness.py:L1-60`) | HELIOS computes its own; it must not trust HERMES `freshness_state` on persistent keys (G9) |
| Completeness | `is_closed`; latest key written when a bar completes | refuses an incomplete bar unless policy allows | compatible if the adapter maps `is_closed` |

## 4. Compatibility gaps derivable from source

Severity is the PL's assessment for the GOLD MVP consumer path; the owner is a proposal.

| ID | Gap | Severity | Evidence | Label | Proposed owner and closing item |
|---|---|---|---|---|---|
| G1 | No single HERMES key carries the HELIOS atom. Candle and indicators live in separate keys written by different paths (candle on bar completion; indicators on a 60 second loop from the history index) and are not written atomically. The adapter must join on bar-open time and tolerate indicators trailing the candle by up to a loop interval. | HIGH | `main.py:L880-891`; `utils/hermes_publisher_runtime_v1.py:L26`; `utils/hermes_runtime_publisher_steps_v1.py:L612-641` | PROVEN | HERMES-FACT-001 (contract), HELIOS-T2 (adapter) |
| G2 | `regime` is not published by any governed HERMES surface; governed code forbids regime field names and states regime belongs to another system. HELIOS documents HERMES as the owner and lists regime values as "observed on the real HERMES feed". Regime exists only on the legacy hash (M1, M5, M15) and in SQL, produced by rules loaded from the database, with values beyond HELIOS's known set (RANGING, HIGH_VOLATILITY, SAFE_FALLBACK also appear). | HIGH for any strategy that declares regime required | `utils/hermes_control_plane_v1.py:L98-109`; `utils/hermes_indicators_v1.py:L38-39`; `HELIOS:helios/contracts/_tokens.py:L133-140`; `HELIOS:docs/INTEGRATION.md:L216`; `utils/regime_detector.py:L599`, `L619-632` | PROVEN | HERMES-FACT-001 with Central Architecture (ownership decision) |
| G3 | `session` vocabulary and case. Governed: asia, london, newyork, overlap_ldn_ny, off_hours. Legacy: asia, london, newyork, late_ny. HELIOS requires upper-case values; lower-case is rejected as malformed. Session is also instrument-level, not tied to a bar. | MEDIUM | `utils/hermes_sessions_v1.py:L24`; `signal_builder.py:L381-392`; `HELIOS:helios/contracts/_tokens.py:L119-122`; probe cases "session" | PROVEN | HELIOS-T2 (normalise) and HERMES-FACT-001 (vocabulary) |
| G4 | Legacy surface zero-fills absent indicators (`emas.get(200, 0)`), so a missing ema_200 is stored as "0". HELIOS accepts 0 as a present value, defeating its "absent is never zero" rule. The governed indicator key publishes null correctly. | HIGH if the legacy hash were ever used | `signal_builder.py:L679-685`, L926; `utils/hermes_runtime_publisher_steps_v1.py:L451-476`; `HELIOS:helios/contracts/market_fact.py:L100-108`; probe case "legacy zero-fill" | PROVEN | HERMES-FACT-001 (forbid legacy surface for GOLD) |
| G5 | Persistent indicator, feature, session and level keys have no expiry or validity window; `freshness_state` is a snapshot of the candle status at write time. A stopped publisher leaves a payload still labelled FRESH. | HIGH | `utils/hermes_runtime_publisher_steps_v1.py:L111-133`, L631-637, L819; `utils/hermes_indicators_v1.py:L135-147` | PROVEN | HERMES-FACT-001 (validity), HELIOS-T2 (judge by bar-open time and own clock) |
| G6 | Float handling. HERMES writes JSON numbers; HELIOS refuses binary floats. | MEDIUM, adapter-solvable | `HELIOS:helios/contracts/_fields.py:L80-82`; probe case "raw JSON floats" | PROVEN | HELIOS-T2 (parse with exact decimals) |
| G7 | Closed-world rejection of extra fields and the name `is_closed` (HELIOS expects `complete`). | MEDIUM, adapter-solvable | probe cases "is_closed passed through", "extra published fields" | PROVEN | HELIOS-T2 |
| G8 | Schema string. HELIOS requires `<namespace>/<x.y.z>` and advertises `helios.market_fact/1.0.0`, fixtures use `hermes.market_fact/1.0.0`; HERMES publishes `schema_version: "v1"`, which HELIOS rejects. No HERMES source or document defines a market-fact schema. | MEDIUM | `HELIOS:helios/contracts/market_fact.py:L47`, `L163-185`; `utils/candle_contract_v1.py:L21`; grep result (no `market_fact` in hermes) | PROVEN | HERMES-FACT-001 (this is the contract it is meant to define) |
| G9 | Provenance times. HELIOS needs observed and ingested times; HERMES gives publish time and bar open only. | LOW | `HELIOS:helios/contracts/market_fact.py:L163-185` | PROVEN | HELIOS-T2 (document the mapping) |
| G10 | Indicator method differences that affect strategy parity with any external backtest: RSI is Cutler SMA 14, ATR is SMA 14, EMA seeds from SMA. HELIOS treats facts as given and states no method. | MEDIUM | `utils/hermes_indicators_v1.py:L52-60` | PROVEN | HERMES-GR-001 (replay parity), HERMES-FACT-001 (declare methods in the contract) |
| G11 | Depth caps. D1 history keeps 35 bars, so ema_50 and ema_200 are structurally null on D1. H4 keeps 250 bars so ema_200 is attainable only after about 200 H4 bars (about 33 trading days) of retained history. | MEDIUM | `utils/candle_d1_history_v1.py:L41-46`; `utils/candle_history_v1.py:L76`; `utils/hermes_runtime_publisher_steps_v1.py:L63-64` | PROVEN | HERMES-FACT-001, HERMES-CUR-001 |
| G12 | Legacy hash is one key per instrument overwritten by M1, M5 and M15 signals, and carries no H1 or H4 at all. It cannot supply a multi-timeframe chain. | HIGH if used | `signal_builder.py:L906`; `main.py:L909-910` | PROVEN | HERMES-FACT-001 |
| G13 | Gated publishers: whether the indicator, feature and session keys exist in production depends on deployment gates this phase cannot read. If the indicator gate is off in production the HELIOS indicator facts are all absent. | UNKNOWN | `utils/hermes_indicators_v1.py:L256-278` | UNVERIFIED | HELM protocol (existence scan) |
| G14 | HELIOS has no live HERMES transport at all in v1; a Redis adapter outside the pure engine does not yet exist. | structural | `HELIOS:helios/integration/hermes_boundary.py:L1-15` | PROVEN | HELIOS-T2 |

## 5. Synthetic probe (code-level confirmation)

`scripts/synthetic_mapping_probe.py` builds a candle envelope and an indicator payload with the HERMES source builders (pure, in memory, invented numbers) and feeds mapped and unmapped variants to the HELIOS models. Output: `synthetic_probe_result.json`. It is labelled SYNTHETIC. Result in one line: with an adapter that parses decimals, renames `is_closed`, projects the six numeric facts and stamps a qualified schema string the frame loads; without those steps it is rejected; lower-case session values are rejected; a legacy-style 0 for ema_200 is accepted silently.

## 6. What stays open until the HELM protocol has run

Which of F03 to F14 exist in production, their real field sets (source builders may differ from a deployed older image), real cadence and lag, real retention depth, whether the legacy hash is present and which prefix it uses, and whether the HELIOS-required indicator facts are populated on M5, M15, H1 and H4 today.
