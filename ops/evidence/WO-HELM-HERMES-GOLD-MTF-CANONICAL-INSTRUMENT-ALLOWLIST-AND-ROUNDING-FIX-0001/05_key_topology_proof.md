# Key Topology Proof

Canonical writer publishes ONLY (verified by tests + `01_blocker_reproduction_and_fix.txt`):
```
hermes:candles:XAU_USD:M1:latest:v1
hermes:candles:XAU_USD:M5:latest:v1
hermes:candles:XAU_USD:M15:latest:v1
hermes:candles:XAU_USD:H1:latest:v1
```
- Non-allowlisted instruments (AUD_USD/EUR_USD/…): **skipped**, no key.
- XAUUSD input: canonicalised → `XAU_USD` key; **no** `hermes:candles:XAUUSD:*`.
- H4/D1/D: `UNSUPPORTED_TIMEFRAME`, no key.
- Unversioned `…:latest`: rejected (`GOV-CANDLE-PUB-CANON-KEY-002`).
- No regime/regime_confidence (forbidden-token scan `GOV-CANDLE-CONTRACT-029`).
