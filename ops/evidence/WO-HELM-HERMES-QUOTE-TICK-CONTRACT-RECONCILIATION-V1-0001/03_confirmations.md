# 03 — Confirmations (code-only)

- No deploy / no restart / no supervisor activation — code-only PR; runtime untouched; D1-seal watch undisturbed.
- No detached-loop stop; no `market_map.py` / `market-map-dev.service` touch — nothing referenced.
- No Redis writes — builder + reconcilers pure; no `.set`/`.zadd`/`.setex` anywhere (test asserts). Nothing published.
- No SQL writes — no SQL path.
- Legacy `hermes:signals:*` / `hermes:market_map:*` NOT deleted/altered — only READ-shaped snapshots reconciled (in-memory), legacy surfaces untouched.
- No existing live contract behaviour altered — 2 new files; `tick_contract_v1` REFERENCED, not modified/rebuilt.
- No manifest/control-plane mutation.
- No auth/ACL/NOAUTH/security change; no secrets; no hard-coded Redis/SQL host/port/credential (scan asserts).
- No regime/risk/decision/signal/entry-exit/ARES interpretation — forbidden-field-KEY scan rejects them; reconcilers drop all interpretation.
- UTC-only timestamps. Missing/stale EXPLICIT (RED_MISSING / AMBER_STALE / UNKNOWN_NEEDS_PROBE); never a false GREEN.
- No import-time I/O — no redis/pymysql/socket/urllib import; no client at import (test asserts + clean import).
- Inverted quote fails loud (GOV-HERMES-QT-006 at build, -018 at validate).
- Diff scope — 2 new files; zero edits to existing live code; no cross-lane edits; no formatting churn.
- D1 seal watch undisturbed (no runtime action taken).
