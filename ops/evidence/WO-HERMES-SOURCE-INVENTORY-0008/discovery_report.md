# WO-HERMES-SOURCE-INVENTORY-0008 — Discovery Report

**Date:** 2026-03-31
**Author:** Helm (Claude Code)
**Status:** DISCOVERY COMPLETE

---

## 1. Artifact Inventory Table

| Artifact | Instrument Scope | Timeframe | Source Type | Origin (Live) | Origin (Recovery) | Canonical? | Producer/Path | Notes |
|----------|-----------------|-----------|-------------|---------------|-------------------|------------|---------------|-------|
| Tick stream | PER_INSTRUMENT | N/A | Fetched | OANDA stream | N/A | Yes | `adapters/oanda.py:stream()` → `SignalTick` | bid/ask/mid/spread/timestamp. Volume always None. |
| candles_M1 | PER_INSTRUMENT | M1 | Derived (live), Fetched (recovery) | HERMES (tick aggregation) | OANDA REST M1 | Yes | `signal_builder.py:CandleAggregator.process_tick()` | OHLCV from mid price. Volume = tick count. |
| candles_M5 | PER_INSTRUMENT | M5 | Derived (live), Fetched (recovery) | HERMES (tick aggregation) | OANDA REST M5 | Yes | `signal_builder.py:CandleAggregator.process_tick()` | Same aggregator, same ticks, independent of M1. |
| candles_M15 | PER_INSTRUMENT | M15 | Derived (live), Fetched (recovery) | HERMES (tick aggregation) | OANDA REST M15 | Yes | `signal_builder.py:CandleAggregator.process_tick()` | Same pattern. |
| candles_H1 | PER_INSTRUMENT | H1 | Derived (live), Fetched (recovery) | HERMES (tick aggregation) | OANDA REST H1 | Yes | `signal_builder.py:CandleAggregator.process_tick()` | Same pattern. |
| candles_D1 | PER_INSTRUMENT | D1 | Derived | HERMES (tick aggregation) | **NO RECOVERY** | Yes | `signal_builder.py:CandleAggregator.process_tick()` | No recovery library entry. No OANDA REST fetch. |
| RSI-14 | PER_INSTRUMENT | M5, M15 | Derived | HERMES | HERMES (recompute) | Yes | `utils/indicators.py:calculate_rsi()` | From close prices in candle history. |
| EMA-9/12/20/21/26/50/200 | PER_INSTRUMENT | M5, M15 | Derived | HERMES | HERMES (recompute) | Yes | `utils/indicators.py:calculate_ema()` | 7 EMA variants, all from close prices. |
| EMA cross states | PER_INSTRUMENT | M5, M15 | Derived | HERMES | HERMES (recompute) | Yes | `utils/indicators.py:get_ema_state()` | 4 pairs: 9/21, 12/26, 20/50, 50/200. |
| ATR-14 | PER_INSTRUMENT | M5, M15 | Derived | HERMES | HERMES (recompute) | Yes | `utils/atr_calculator.py:calculate_atr()` | From OHLC candle history. |
| ADX-14 (+DI/-DI) | PER_INSTRUMENT | M5, M15 | Derived | HERMES | HERMES (recompute) | Yes | `utils/indicators.py:calculate_adx()` | From HLC prices. |
| Bollinger Bands | PER_INSTRUMENT | M5, M15 | Derived | HERMES | HERMES (recompute) | Yes | `utils/indicators.py:calculate_bollinger_bands()` | 20-period, 2 std dev. middle/upper/lower/width/squeeze. |
| ATR baseline/shock/ratio | PER_INSTRUMENT | M5, M15 | Derived | HERMES | HERMES (recompute) | Yes | `signal_builder.py:_compute_day_state_metrics()` | Rolling ATR history for day-state context. |
| Volume ratio | PER_INSTRUMENT | M5, M15 | Derived | HERMES | HERMES (recompute) | Yes | `signal_builder.py:compute_signal()` | tick_count / 20-bar average. |
| Regime | PER_INSTRUMENT | M5, M15 | Derived | HERMES | HERMES (recompute) | Yes | `utils/regime_detector.py:RegimeDetector.classify()` | From ADX, RSI, ATR, BB, EMAs. Data-driven classification. |
| Session | PER_INSTRUMENT | M5, M15 | Derived | HERMES | HERMES (recompute) | Yes | `signal_builder.py:_get_session()` | From UTC hour. Pure logic, no external source. |
| Compression score | PER_INSTRUMENT | M5, M15 | Derived | HERMES | HERMES (recompute) | Yes | `utils/compression_detector.py:CompressionDetector.detect()` | From ATR percentile, BB width, EMA convergence. |
| Break quality | PER_INSTRUMENT | M5, M15 | Derived | HERMES | HERMES (recompute) | Yes | `utils/break_detector.py:BreakDetector.get_break_for_signal()` | From OHLC + volume ratio + hermes_levels. |
| Nearest S/R levels | PER_INSTRUMENT | M5, M15 | Derived | HERMES | HERMES (recompute) | Yes | `utils/level_engine.py:get_nearest_levels()` | Reads from hermes_levels table. |
| PDH/PDL | PER_INSTRUMENT | D1 | Derived | HERMES | N/A | Yes | `utils/level_engine.py:compute_pdh_pdl()` | From candles_D1. Updated at 00:xx UTC. |
| Asia High/Low | PER_INSTRUMENT | H1 | Derived | HERMES | N/A | Yes | `utils/level_engine.py:compute_asia_range()` | From candles_H1 (22:00-07:00). Updated at 07:xx UTC. |
| M15 Swing H/L | PER_INSTRUMENT | M15 | Derived | HERMES | N/A | Yes | `utils/level_engine.py:compute_m15_swings()` | From candles_M15 (20-bar lookback). Every 15 min. |
| signals table | PER_INSTRUMENT | M5, M15 | Derived | HERMES | HERMES (recompute) | Yes | `signal_builder.py:SignalPublisher.publish_signal()` | Composite row: all indicators above in one row. |

---

## 2. Dependency Chain

```
OANDA Stream API
│
│  ONLY provides: bid, ask, timestamp per instrument
│  NO candles. NO indicators. NO volume.
│
▼
SignalTick (bid/ask/mid/spread)
│
▼
CandleAggregator.process_tick()
│  Independently aggregates SAME ticks into ALL timeframes:
│
├──▶ candles_M1  ─┐
├──▶ candles_M5  ─┤
├──▶ candles_M15 ─┤  (all from ticks, NOT from each other)
├──▶ candles_H1  ─┤
└──▶ candles_D1  ─┘
      │
      ▼  (M5 and M15 only)
SignalComputer.compute_signal(candle, history_from_DB)
      │
      ├──▶ RSI-14, EMA-{9,12,20,21,26,50,200}, ATR-14
      ├──▶ ADX-14 (+DI/-DI), Bollinger Bands
      ├──▶ Regime, Day State, Session
      ├──▶ Compression, Break Quality
      ├──▶ Nearest S/R (from hermes_levels)
      │
      ▼
signals table + Redis pub/sub

LevelEngine (background, every 15min / daily):
      candles_D1  ──▶ PDH/PDL
      candles_H1  ──▶ Asia Range
      candles_M15 ──▶ M15 Swings
      ──▶ hermes_levels table
```

**Where OANDA influence ends:** At the `SignalTick` boundary. Everything after that — every candle, every indicator, every signal, every level — is HERMES-owned computation.

---

## 3. Answers to Discovery Questions

### A. Raw Source Ingestion

| Question | Answer | Evidence |
|----------|--------|----------|
| Does HERMES ingest tick/pricing stream from OANDA? | **Yes.** Ticks only (bid/ask). | `oanda.py:stream()` yields `SignalTick` |
| Does HERMES ingest M1 candles from OANDA? | **No (live). Yes (recovery only).** | Live: `CandleAggregator`. Recovery: `recovery_executor.py:BROKER_FETCH` |
| Does HERMES ingest M5/M15/H1 from OANDA? | **No (live). Yes (recovery only).** | Same pattern — tick aggregation live, OANDA REST for recovery |
| Non-candle broker-sourced artifacts? | **No.** Zero indicators from OANDA. | All indicator functions in `utils/indicators.py` take internal data |

### B. Internal Derivation

| Question | Answer | Evidence |
|----------|--------|----------|
| Are RSI/EMA/ATR computed internally? | **Yes, 100% internal.** | `indicators.py`, `atr_calculator.py` — pure math on candle history |
| Are session ranges internal? | **Yes.** PDH/PDL from candles_D1, Asia from candles_H1. | `level_engine.py` queries HERMES's own tables |
| Are regime/compression/break internal? | **Yes.** Derived from internal indicators. | `regime_detector.py`, `compression_detector.py`, `break_detector.py` |

### C. Canonical Truth

**Every artifact is canonical.** There is no case where both a fetched and derived variant exist simultaneously in the live path. OANDA REST candles are used ONLY for recovery/backfill — they overwrite the same tables via `ON DUPLICATE KEY UPDATE`.

### D. Dependency Structure

The real chain today is:

```
OANDA ticks → [M1, M5, M15, H1, D1] (parallel, from ticks) → [signals M5, signals M15] (from candle history)
```

NOT:

```
OANDA M1 → M5 → M15 → H1 (hierarchical)
```

Each timeframe is independently derived from ticks. There is no inter-timeframe derivation.

### E. Epic Impact Assessment

**Is "collect canonical M1 only, derive everything else" already true?**

**No.** But it is architecturally close.

| Aspect | Current State | M1-Only Target | Gap |
|--------|--------------|----------------|-----|
| Live tick ingestion | OANDA ticks → all TFs from ticks | OANDA ticks → M1 from ticks → derive rest | CandleAggregator change: only produce M1, add derivation module |
| Recovery | Each TF fetched independently from OANDA REST | OANDA REST M1 only → derive rest | Recovery library change: only M1 is BROKER_FETCH, rest become DERIVE_FROM_LOWER_TIMEFRAME |
| Indicators | Already 100% internal | No change needed | None |
| Signals | Already 100% internal | No change needed | None |
| Levels | Already internal (from own candle tables) | No change | None |
| D1 | No recovery strategy | Add D1 recovery | Missing entry in recovery library |

---

## 4. Assessment

### Current Reality

HERMES is already **almost entirely self-contained**. OANDA provides only raw price ticks. Every candle, indicator, signal, and level is computed internally. The only OANDA dependency beyond the tick stream is the recovery path, which fetches candles at native granularity for gap repair.

### What Would Change for M1-Only Canonical

Three things:

1. **CandleAggregator refactor** — Stop aggregating M5/M15/H1/D1 directly from ticks. Instead: aggregate M1 from ticks, then derive higher TFs from completed M1 candles. This is a moderate refactor — the aggregator currently runs all TFs in parallel from ticks.

2. **Recovery library update** — Change CANDLE_M5/M15/H1 from `BROKER_FETCH` to `DERIVE_FROM_LOWER_TIMEFRAME`. Only CANDLE_M1 remains `BROKER_FETCH`. Add CANDLE_D1 to the library.

3. **Derivation module** — New module to produce M5 from 5 consecutive M1 candles, M15 from 15 M1s, H1 from 60 M1s, D1 from M1s within a day boundary. This does not exist today.

### What Would NOT Change

- Signal computation — already reads from per-TF candle tables, doesn't care how they got there
- Indicators — all internal, input-agnostic
- Levels — already derived from internal candle tables
- Redis pub/sub — downstream, unaffected
- Health/watchdog — monitors output, not source

---

## 5. Recommendation for Future Epic

### Is "canonical collected timeframe = M1 only" valid?

**Yes.** It is architecturally sound and simplifying. The current architecture already proves HERMES can produce all indicators and signals from internal candle data. The only question is whether candles are aggregated from ticks or derived from lower TFs — both produce equivalent output.

### Recommended Epic Scope

1. **Derivation engine** — M1 → M5/M15/H1/D1 candle derivation module
2. **CandleAggregator simplification** — M1 from ticks only, remove M5/M15/H1/D1 tick aggregation
3. **Recovery library migration** — M1 = BROKER_FETCH, all others = DERIVE_FROM_LOWER_TIMEFRAME
4. **D1 recovery** — Add CANDLE_D1 to recovery library (currently missing)
5. **Source abstraction** — If multi-source resilience (IBKR/MT5 as fallback) is desired, the abstraction point becomes the tick/M1 boundary, which is much simpler than abstracting at every timeframe
6. **Validation** — Prove derived candles match OANDA native candles within acceptable tolerance (mid-price rounding, volume semantics)

### Key Simplification Benefit

With M1-only canonical, source resilience becomes a single-point problem: "can I get M1 candles from somewhere?" Rather than "can I get M1+M5+M15+H1 from somewhere?" This is the real architectural win.

---

## 6. Notable Findings

### Dead/Duplicate Paths
- `main.py:fetch_oanda_candles()` is hardcoded to M5 only. `recovery_executor.py:fetch_oanda_candles()` handles all granularities. The main.py version is legacy from before the recovery engine.
- `utils/db_writer.py:DBWriter` class exists but is NOT used in the main tick→candle path. The live path uses `main.py:save_candle()` directly. DBWriter appears to be legacy.

### Broker-Coupled Recovery
- Recovery currently couples to OANDA for M5/M15/H1 candle fetch. Under M1-only, this coupling reduces to M1 only.

### Volume Semantics Mismatch
- Live candles: `volume = tick count` (OANDA stream has no volume)
- Recovery candles: `volume = OANDA reported volume` (REST API provides it)
- This means the same candle can have different volume values depending on whether it was produced live or recovered. Not a functional issue (nothing uses volume for decisions), but worth noting for data integrity.

### D1 Recovery Gap
- D1 candles have no recovery library entry and no OANDA REST fetch path. If D1 data is lost, it cannot currently be recovered. This should be addressed regardless of the M1-only epic.
