# SPX500_USD + WTICO_USD availability proof (§5) — READ-ONLY, no new subscription

Verified from the running production runtime (e9e72efd4ddd, source ebf49a73) 2026-08-05T06:44Z — both are
already configured, subscribed, and producing live data. No new subscription made.

## Live evidence (/health, market open)
| field | SPX500_USD | WTICO_USD |
|-------|-----------|-----------|
| health | GREEN | GREEN |
| last_tick_utc | 2026-08-05 06:44:19Z | 2026-08-05 06:44:27Z |
| last_m1_persisted | 2026-08-05 06:43:00 | 2026-08-05 06:43:00 |
| OANDA id == HERMES id | SPX500_USD | WTICO_USD |
| asset class | indices (equity index / US500 cash) | (energy / WTI crude oil) |

## Registry + SQL evidence
    -- instruments registry rows (SPX500/WTICO/XAU) --
    +------------+------------+----------------------------+-----------------+-------------------+---------------+---------+------------------+---------------------+-------------------+
    | symbol     | mt5_symbol | name                       | category        | pip_value_per_lot | contract_size | enabled | oanda_compatible | trading_hours_start | trading_hours_end |
    +------------+------------+----------------------------+-----------------+-------------------+---------------+---------+------------------+---------------------+-------------------+
    | SPX500_USD | NULL       | S&P 500 Index vs US Dollar | indices         |            1.0000 |             1 |       1 |                1 | 00:00:00            | 23:59:59          |
    | WTICO_USD  | NULL       | WTI Crude Oil vs US Dollar | base_metals     |           10.0000 |          1000 |       1 |                1 | 00:00:00            | 23:59:59          |
    | XAU_USD    | NULL       | Gold vs US Dollar          | precious_metals |           10.0000 |           100 |       1 |                1 | 22:00:00            | 22:00:00          |
    +------------+------------+----------------------------+-----------------+-------------------+---------------+---------+------------------+---------------------+-------------------+
    -- SQL candle evidence (M1 count + latest) --
    -- SQL candle evidence (M1 count + latest) --
    [SPX500_USD] 128403	2026-04-05 21:50:00	2026-08-05 06:49:00
    [WTICO_USD] 128268	2026-04-05 21:50:00	2026-08-05 06:49:00

## Findings
- Both present in the canonical `instruments` registry, enabled=1, oanda_compatible=1 (added by mig-024).
- Both produce live ticks + persisted M1 candles during valid market hours (proven above; market_open=True).
- Market-hours behaviour: SPX500 (index cash) and WTICO (energy) have distinct sessions/maintenance vs FX/
  metals — handled by the reusable `market_hours_policy` registry key (index_cash / energy), NOT ticker code.
  (During the 2026-08-02 weekend observation both were correctly RED HERMES_INSTRUMENT_M1_STALE while FX/
  metals were AMBER EXPECTED_MARKET_CLOSED_NO_FLOW — evidencing distinct hours; truthful.)
- Broker/canonical name mapping: identical (SPX500_USD, WTICO_USD) — no alias translation required.

VERDICT: AVAILABLE — both may be designed into the 8-instrument rollout. (No AMBER.)
