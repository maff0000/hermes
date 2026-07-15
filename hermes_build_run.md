# HERMES - Signal Service (tradingSignals)

> **Hermes** (Greek: messenger of the gods) - Single source of truth for market data.
> "HERMES signals. Zeus decides. Tyche executes."

> **Governance:** the binding MVP-closure mandate is [PID-HERMES-MVP-001](docs/governance/PID-HERMES-MVP-001.md)
> (HERMES is a pure market-data fact spine — no strategy/risk/trade authority; indicator DoD = code-in-main + deployed runner +
> fresh payload; canonical/deployed/operational recorded separately). See [docs/governance/](docs/governance/README.md).

**Version:** 1.0.0
**Built:** 2026-01-05
**Updated:** 2026-01-23
**EPIC:** EPIC-D002 (Standalone Signal Service), EPIC-D026 (HERMES SSOT Upgrade), EPIC-D027 (Phase 3 Breakout)
**Location:** `/srv-dev/tradingSignals/`

---

## CRITICAL RULES - READ FIRST

### 1. HERMES IS THE SSOT

- All market data flows through HERMES
- All indicators computed by HERMES
- Zeus/Apollo/Athena CONSUME signals, never compute them
- **"HERMES computes. Zeus decides."**

### 2. ENVIRONMENT-AWARE CONFIG (GOV-ENV-001)

Single `.env` with ENVIRONMENT tag:
```bash
ENVIRONMENT=DEV   # or PROD
```

All services use `{ENV}_*` prefixed settings automatically.

### 3. NO MOCK IN PRODUCTION

Mock data is **FORBIDDEN** in production. Safety check in `main.py:646-656`:
```python
if is_prod and state.config.oanda.use_mock:
    raise RuntimeError("MOCK SIGNALS BLOCKED IN PRODUCTION - SAFETY VIOLATION")
```

### 4. SELF-HEALING STREAM

OANDA stream auto-reconnects with exponential backoff. Gap detection runs on reconnect.

---

## Quick Start

### DEV Environment
```bash
cd /srv-dev/tradingSignals
source venv/bin/activate
python main.py
# Runs on port 8211 (DEV)
```

### PROD Environment
```bash
cd /srv/tradingSignals
source venv/bin/activate
python main.py
# Runs on port 8210 (PROD)
```

---

## Architecture

```
OANDA Streaming API
        │
        ▼
┌─────────────────┐
│  OANDAAdapter   │  (adapters/oanda.py)
│  - Stream ticks │
│  - Auto-reconnect│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ CandleAggregator│  (signal_builder.py)
│  - Ticks → M5   │
│  - Also M15/H1/D1│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ SignalComputer  │  (signal_builder.py)
│  - RSI, EMAs    │
│  - ATR, ADX, BB │
│  - Regime       │
│  - Levels       │
│  - Compression  │
│  - Break Quality│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ SignalPublisher │  (signal_builder.py)
│  - Write to DB  │
│  - Publish Redis│
└─────────────────┘
         │
         ├──────────────────┐
         ▼                  ▼
    ┌─────────┐       ┌──────────┐
    │  MySQL  │       │  Redis   │
    │ signals │       │ hermes:* │
    └─────────┘       └──────────┘
```

---

## Database Schema

### Database: `tradingSignals`

### Tables

| Table | Purpose | Rows (approx) |
|-------|---------|---------------|
| `signals` | M5 indicator signals | ~800K |
| `candles_M1` | 1-minute OHLCV | Not populated |
| `candles_M5` | 5-minute OHLCV | ~800K |
| `candles_M15` | 15-minute OHLCV | ~160K |
| `candles_H1` | Hourly OHLCV | ~27K |
| `candles_D1` | Daily OHLCV | ~1K |
| `instruments` | Instrument config (SSOT) | 12 |
| `hermes_levels` | Price levels (PDH/PDL, Asia) | ~100 |
| `regime_classifications` | Regime definitions | ~10 |
| `regime_config` | Regime detector config | ~20 |
| `trading_windows` | Session definitions | 4 |
| `service_stats` | Healthcheck metrics | ~1K |
| `ticks` | Raw ticks (optional) | 0 (disabled) |
| `decisions` | Legacy (unused) | 0 |

### Signals Table (53 Fields)

See HRM-001-INDICATOR-CATALOG.md for complete field documentation.

Key categories:
- **Core:** id, instrument, timestamp, timeframe, version
- **Price:** price_close
- **Volume:** volume, volume_ratio
- **Momentum:** rsi_14
- **Trend:** ema_9, ema_12, ema_20, ema_21, ema_26, ema_50, ema_200
- **EMA States:** ema_9_21_state, ema_12_26_state, ema_20_50_state, ema_50_200_state
- **Volatility:** atr_14, atr_baseline, atr_opening_shock, atr_day_ratio
- **ADX:** adx_14, plus_di, minus_di
- **Bollinger:** bb_middle, bb_upper, bb_lower, bb_width_pct, bb_squeeze
- **Regime:** regime, regime_confidence, regime_indicators, session
- **Levels:** nearest_resistance_*, nearest_support_*
- **Compression:** compression_score, compression_atr_pctl, compression_range_score, compression_bb_squeeze, compression_ema_converging
- **Break Quality:** break_quality_long, break_quality_short, break_level_id
- **Retest:** in_retest_zone, retest_level_id

---

## Instruments

| Symbol | Name | Category | Status |
|--------|------|----------|--------|
| XAU_USD | Gold | Precious Metals | Active |
| XAG_USD | Silver | Precious Metals | Active |
| XPT_USD | Platinum | Precious Metals | Active |
| XCU_USD | Copper | Base Metals | Active |
| EUR_USD | Euro | Forex Major | Active |
| GBP_USD | British Pound | Forex Major | Active |
| USD_JPY | Japanese Yen | Forex Major | Active |
| AUD_USD | Australian Dollar | Forex Major | Active |
| NZD_USD | New Zealand Dollar | Forex Major | Active |
| USD_CAD | Canadian Dollar | Forex Major | Active |
| USD_CHF | Swiss Franc | Forex Major | Active |
| EUR_GBP | Euro/Pound Cross | Forex Minor | Active |

---

## Data Availability

### Full Coverage (Dec 2024 - Present)
- XAU_USD
- EUR_USD, GBP_USD, USD_JPY, AUD_USD, NZD_USD, USD_CAD, USD_CHF

### Partial Coverage (Gap: Jun-Nov 2025)
- XAG_USD
- XPT_USD
- XCU_USD
- EUR_GBP

**Impact:** Apollo backtests for these instruments cannot cover Jun-Nov 2025 period.

---

## Environment Configuration

### DEV (`/srv-dev/tradingSignals`)
```
ENVIRONMENT=DEV
DEV_DB_PORT=3307
DEV_REDIS_PORT=6380
DEV_SIGNAL_PORT=8211
```

### PROD (`/srv/tradingSignals`)
```
ENVIRONMENT=PROD
PROD_DB_PORT=3306
PROD_REDIS_PORT=6379
PROD_SIGNAL_PORT=8210
```

### Key Settings
| Setting | Purpose | Default |
|---------|---------|---------|
| OANDA_API_KEY | OANDA v20 API key | Required |
| OANDA_ACCOUNT_ID | OANDA account ID | Required |
| OANDA_ENVIRONMENT | live or practice | live |
| INSTRUMENTS | Comma-separated list | All 12 |
| CANDLE_TIMEFRAMES | Timeframes to aggregate | M5,M15,H1,D1 |
| BACKFILL_ON_STARTUP | Auto-backfill gaps | true |
| BACKFILL_GAP_THRESHOLD_MINUTES | Gap threshold | 10 |

---

## Redis Publishing

### Keys Published
| Pattern | Purpose | TTL |
|---------|---------|-----|
| `hermes:signals:latest:{instrument}` | Latest signal hash | 10 min |
| `hermes:ticks:{instrument}` | Latest tick | 60 sec |
| `hermes:instruments:config:{instrument}` | Instrument config | 1 hour |
| `hermes:instruments:list` | Enabled instruments | 1 hour |

### Channels Published
| Channel | Purpose |
|---------|---------|
| `hermes:signals:stream:{instrument}` | Real-time signal updates |
| `hermes:ticks:stream:{instrument}` | Real-time tick updates |

---

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Basic health check |
| `/ready` | GET | Readiness (receiving ticks?) |
| `/metrics` | GET | Prometheus metrics |
| `/prices` | GET | All latest prices |
| `/prices/{instrument}` | GET | Specific instrument price |
| `/fx/{pair}` | GET | FX rate for conversion |
| `/status` | GET | Detailed service status |
| `/failover/{source}` | POST | Force failover (admin) |

---

## Healthcheck & Monitoring

HERMES reports to IRIS (tradingReport) every 60 seconds:

```json
{
  "service": "hermes",
  "environment": "DEV",
  "status": "healthy",
  "uptime_seconds": 12345,
  "metrics": {
    "ticks_per_minute": 120,
    "candles_per_minute": 2,
    "signals_per_minute": 2,
    "active_instruments": 12
  }
}
```

---

## Self-Healing Features

### Stream Reconnection
- Exponential backoff: 5s → 10s → 20s → ... → 300s (max)
- Unlimited retries by default
- Alerts IRIS on disconnect and reconnect

### Gap Detection & Backfill
- Checks for gaps > 10 minutes on startup and reconnect
- Fetches missing candles from OANDA Historical API
- Computes signals for backfilled candles
- Logs backfill count to IRIS

---

## Troubleshooting

### No Ticks Received
1. Check OANDA API key validity
2. Check OANDA account status
3. Verify INSTRUMENTS list is correct
4. Check network connectivity

### Stale Signals
1. Check `/ready` endpoint for stale tick warnings
2. Check OANDA stream status
3. Verify market is open (forex closes weekends)

### Redis Connection Failed
1. Check Redis is running on correct port
2. Verify REDIS_HOST and REDIS_PORT settings
3. Check Redis password if configured

### Database Write Failures
1. Check MySQL connection
2. Verify DB_HOST and DB_PORT
3. Check table schemas match expected

---

## File Structure

```
/srv-dev/tradingSignals/
├── main.py                    # FastAPI application
├── config.py                  # Config loader
├── env_config.py              # Environment-aware config
├── signal_builder.py          # Candle aggregation & signal computation
├── .env                       # Environment configuration
├── requirements.txt           # Dependencies
├── adapters/
│   ├── base.py               # Adapter base class
│   └── oanda.py              # OANDA streaming adapter
├── models/
│   ├── tick.py               # SignalTick model
│   └── candle.py             # Candle model
├── utils/
│   ├── indicators.py         # RSI, EMA, ADX, BB calculations
│   ├── atr_calculator.py     # ATR calculation
│   ├── regime_detector.py    # Regime classification
│   ├── level_engine.py       # PDH/PDL, Asia range, swings
│   ├── compression_detector.py # Compression detection
│   ├── break_detector.py     # Break quality calculation
│   ├── redis_publisher.py    # Redis pub/sub
│   ├── db_writer.py          # Database writes
│   ├── iris_client.py        # IRIS alerting
│   ├── healthcheck.py        # Health metrics
│   ├── trading_hours.py      # Market hours logic
│   └── discord_alerts.py     # Discord notifications
├── scripts/
│   ├── backfill_oanda.py     # Historical backfill
│   ├── backfill_signals.py   # Signal recomputation
│   └── archive_old_data.py   # Data archival
├── tests/
│   ├── conftest.py
│   ├── test_api.py
│   ├── test_models.py
│   └── test_redis_publisher.py
└── services/
    └── market-map/           # Market overview service
```

---

## Dependencies

```
fastapi>=0.100.0
uvicorn>=0.22.0
httpx>=0.24.0
redis>=4.5.0
pymysql>=1.0.0
python-dotenv>=1.0.0
numpy>=1.24.0
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-01-05 | Initial EPIC-D002 implementation |
| 1.1.0 | 2026-01-15 | EPIC-D026: ADX, Bollinger, regime confidence |
| 1.2.0 | 2026-01-20 | EPIC-D027: Level engine, compression, break quality |
| 1.2.1 | 2026-01-23 | HRM-007: build_run.md documentation |

---

*hermes_build_run.md - HERMES Signal Service Documentation*
*P9: build_run.md is sacred - keep this file up to date*
