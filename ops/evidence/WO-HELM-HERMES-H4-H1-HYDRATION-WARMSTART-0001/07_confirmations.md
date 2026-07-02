# 07 — Confirmations (code-only)

- **No deploy / no restart / no enable** — code-only PR; warm-start gates default OFF; not enabled anywhere.
- **No supervisor activation** — PR #63 durable supervisor untouched; `HERMES_PUBLISHER_RUNTIME_*` not referenced.
- **No detached-loop stop; no `market_map.py` / `market-map-dev.service` touch** — none referenced.
- **No Redis writes** — hydration only READS H1 history + seeds in-memory; `hydrate()` has no `.set`; tests prove `client.sets == []`.
- **No SQL writes** — no SQL path in the new module.
- **No H4 history backfill / no D1 history backfill** — seeds the current in-memory block only; writes no history.
- **No D1 indicators/features/levels enablement** — untouched; hydration writes none.
- **No auth/ACL/NOAUTH/security change; no secrets; no hard-coded Redis target** — read client injected/from-writer (`test_no_hardcoded_redis_target_in_module`).
- **No regime/risk/decision/ARES interpretation** — deterministic H4/H1 facts only.
- **No H4/D1 GREEN from hydration** — `h4_published_by_hydration=false`; H4 seals only on live roll-over; D1 stays gated until a genuine 6/6 H4 live seal.
- **No import-time I/O** — no `import redis`, no client/socket/thread/SQL at import (`test_no_redis_sql_socket_thread_at_import`).
- **D1 warm-start unchanged** — only integrated cleanly (H4 warm-start runs before it in the lifespan).
- **Diff scope** — 4 files only (main.py, candle_h4_publish_wire_v1.py, candle_h4_hydration_v1.py, test). No cross-lane edits, no formatting churn.
