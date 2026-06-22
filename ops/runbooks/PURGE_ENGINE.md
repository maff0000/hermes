# HERMES-OPS-PURGE-001 — Out-of-Band DB Purge Engine

Standalone production-only house-cleaning for high-volume market-data tables. Runs entirely
out-of-band from the hot-path loop; **never imported by `main.py`**.

## Invocation
```bash
python3 ops/purge.py        # honours the two gates below; exit code reflects outcome
```

## Gates (BOTH required before any delete)
| Gate | Condition | Behaviour |
|------|-----------|-----------|
| 1. Environment-blind | `RUN_ENV != PRODUCTION` | `[PURGE_BYPASS]` to stdout, **exit 0** (dev/staging ledger preserved) |
| 2. Explicit enable | `RUN_ENV=PRODUCTION` **and** `HERMES_PURGE_ENABLED != TRUE` | fail-loud `GOV-PURGE-001` to SIEM, **exit 1** |
| Proceed | `RUN_ENV=PRODUCTION` **and** `HERMES_PURGE_ENABLED=TRUE` | runs the purge cycle |

`RUN_ENV` is intentionally separate from `ENVIRONMENT` (the latter drives DB host resolution). Prod
must set `RUN_ENV=PRODUCTION` explicitly; otherwise the engine safely bypasses.

## Configuration (no destructive defaults — unset = nothing deleted)
| Var | Req | Default | Meaning |
|-----|-----|---------|---------|
| `PURGE_RETENTION_DAYS` | yes | — | rows strictly older than `now_utc - N days` are eligible |
| `PURGE_TABLES` | yes | — | `table:timestamp_column` list; **hardcoded allowlist** `{ticks, candles_M5, candles_H1}` (else `GOV-PURGE-004`) |
| `PURGE_BATCH_SIZE` | no | 5000 | rows per chunk (`LIMIT`) |
| `PURGE_BATCH_SLEEP_MS` | no | 200 | micro-sleep between chunks (lock yield); **floor=1**, `0` -> fail-loud |
| `PURGE_MAX_BATCHES_PER_TABLE` | no | 100000 | runaway backstop per table (0 = unlimited) |

## Mechanics
- Chunked sliding window: `DELETE ... WHERE ts < cutoff ORDER BY ts ASC LIMIT batch`, **commit per
  batch**, micro-sleep **between** batches (never after the last). Oldest rows first.
- Cutoff computed in **UTC**.
- Telemetry to Graylog SIEM: `PURGE_CYCLE_START`, per-batch `PURGE_BATCH`, `PURGE_CYCLE_COMPLETE`
  (+ `PURGE_BATCH_CAP` if a table hits the backstop).

## Reason codes
`GOV-PURGE-001` enable-toggle missing · `GOV-PURGE-002` config missing/invalid · `GOV-PURGE-003` DB error · `GOV-PURGE-004` target table outside the hardcoded allowlist.

## Doctrine
Deletes outside PLUTUS are guarded. This engine is the mechanism; executing it against production is a
separately-authorised act, enforced here by the two gates. Build/verify is files-only — no deploy.
