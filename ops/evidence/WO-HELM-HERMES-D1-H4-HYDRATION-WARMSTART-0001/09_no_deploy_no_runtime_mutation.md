# 09 — No-deploy / no-runtime-mutation confirmation

This WO is **CODE-ONLY**. Confirmations:

- **No deploy** — no image build, no `docker compose`, `/srv-dev` deploy worktree not touched.
- **No restart** — hermes-signal-dev service not restarted.
- **No supervisor activation** — PR #63 publisher supervisor untouched; `HERMES_PUBLISHER_RUNTIME_*` not set.
- **No detached loop stop** — the `/tmp` dev-loop publishers left running.
- **No `market_map.py` touch** — PID untouched; `market-map-dev.service` untouched.
- **No Redis writes** — warm-start only READS (bounded `ZRANGEBYSCORE` + `GET`) and seeds in-memory; proven by
  `client.sets == []` in `test_warmstart_reads_redis_h4_history_no_writes` and
  `test_warmstart_then_live_rollover_publishes_genuine_seal` (the only write is the later LIVE seal, not hydration).
- **No SQL writes** — no SQL is issued by hydration at all; no DB write path exists in the new module.
- **No D1 history backfill** — hydration seeds the current in-memory block only; it never writes D1 history and
  never reaches back to a prior day.
- **No D1 indicators/features/levels enablement** — those gates are untouched; hydration writes none of them.
- **No auth/ACL/NOAUTH/security change** — no credential/ACL/security code; read client injected/from-writer; no
  hard-coded target (`test_no_hardcoded_redis_target_in_module`).
- **No regime/risk/decision/ARES-owned interpretation** — hydration handles only deterministic H4→D1 facts; no
  interpretive field is produced or consumed.
- **No cross-lane edits** — only HERMES files changed; no ARES/HELIOS/Falcon touch.

## Runtime mutation
None. At default config (`HERMES_D1_WARMSTART_ENABLED` unset AND D1 publish gated off) the boot path is a logged
no-op cold-start. Even enabled+authorised, hydration mutates only the producer's in-memory buffer.
