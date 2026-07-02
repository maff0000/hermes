# 03 — Confirmations (code-only)

- **No deploy / no restart / no supervisor activation** — code-only PR; no runtime touched; D1-seal watch undisturbed.
- **No detached-loop stop; no `market_map.py` / `market-map-dev.service` touch** — nothing referenced.
- **No Redis writes** — builder is pure; collector is READ-ONLY (`.get` only); no `.set`/`.zadd`/`.setex` anywhere (test asserts). Nothing published.
- **No SQL writes** — no SQL path at all.
- **No manifest/control-plane live-key mutation** — heartbeat read only; no existing file changed (2 new files only).
- **No auth/ACL/NOAUTH/security change; no secrets; no hard-coded Redis/SQL host/port/credential** — client injected; scan asserts no `192.168.`/`6379`/`localhost`/`password=`/`requirepass`.
- **No regime/risk/decision/ARES-owned interpretation** — forbidden-field-KEY scan rejects regime/risk/liquidity/order_block/smart_money/decision/trade/signal/bias/setup/go_no_go/gating/conclusion at build/validate time (test_ares_owned_fields_rejected).
- **UTC-only** — all timestamps via `_utc()` → ISO `Z`; no local/broker time.
- **Missing/stale never hidden** — explicit RED_MISSING / AMBER_STALE / missing_/stale_timeframes.
- **D1 stays GATED (not RED)** until D1 latest GREEN.
- **No import-time I/O** — no redis/pymysql/socket/urllib import; no client at import (test asserts + module imports cleanly).
- **Diff scope** — 2 new files (`utils/hermes_feed_health_v1.py`, `tests/test_hermes_feed_health_v1.py`); zero edits to existing live code; no cross-lane edits; no formatting churn.
