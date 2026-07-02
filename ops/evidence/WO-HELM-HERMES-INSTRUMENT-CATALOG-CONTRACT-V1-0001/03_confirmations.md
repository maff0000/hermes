# 03 — Confirmations (code-only)

- No deploy / no restart / no supervisor activation — code-only PR; runtime untouched; D1-seal watch undisturbed.
- No detached-loop stop; no `market_map.py` / `market-map-dev.service` touch — nothing referenced.
- No Redis writes — builder pure; no collector I/O; no `.set`/`.zadd`/`.setex` anywhere (test asserts). Nothing published.
- No SQL writes — no SQL path.
- No manifest/control-plane live-key mutation — control-plane keys referenced as discovery facts only; no existing file changed (2 new files).
- No auth/ACL/NOAUTH/security change; no secrets; no hard-coded Redis/SQL host/port/credential (scan asserts no 192.168./6379/localhost/password=/requirepass).
- No regime/risk/decision/ARES-owned interpretation — forbidden-field-KEY scan rejects them at build/validate.
- UTC-only timestamps. Missing/gated/not-implemented EXPLICIT (never silently omitted).
- D1 truth preserved — latest PENDING, history BLOCKED, derived GATED; never ACTIVE just because code exists.
- No import-time I/O — no redis/pymysql/socket/urllib import; no client at import (test asserts + clean import).
- Diff scope — 2 new files; zero edits to existing live code; no cross-lane edits; no formatting churn.
