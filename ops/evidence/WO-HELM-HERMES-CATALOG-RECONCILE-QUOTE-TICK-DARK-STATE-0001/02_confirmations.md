# 02 — Confirmations (code-only)

- No deploy / no restart / no supervisor activation — code-only PR; runtime untouched.
- No quote/tick/feed-health/instrument-catalog publisher activation — declaration change only; no publisher wired.
- No `hermes:quote:XAU_USD:v1` / `hermes:instrument_catalog:XAU_USD:v1` published (merge/build writes no Redis).
- No mutation of existing `hermes:ticks:XAU_USD:latest:v1`; no duplicate `hermes:tick:*` key created.
- No control-plane/manifest live-key mutation; no detached-loop stop; no `market_map.py`/service touch.
- No Redis writes; no SQL writes; no auth/ACL/NOAUTH/security change; no secrets; no hard-coded Redis/SQL target.
- No regime/risk/decision/signal/entry-exit/ARES-owned interpretation (forbidden-field-KEY scan still enforces).
- UTC-only; deterministic serialization; no import-time Redis/SQL/network I/O.
- D1 GREEN observed but UNDISTURBED — no runtime action; catalog D1 default remains PENDING (not hard-coded ACTIVE).
- Diff scope — 2 files (utils/hermes_instrument_catalog_v1.py, tests/test_hermes_instrument_catalog_v1.py); no cross-lane edits; no unrelated churn.
