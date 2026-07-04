# H4 prior-completed-block warm-start reseal (code-only)

WO: WO-HELM-HERMES-H4-PRIOR-COMPLETED-BLOCK-WARMSTART-RESEAL-0001 · Persona: HELM (HERMES) · Mode: CODE-ONLY
Base: 75065840885404a229cf321094aad94cb1cbb416 (main) · R2D2 gap audit: AMBER_..._GAP_CONFIRMED (2026-07-03T17:41Z)

## Fix (2 files, no main.py change — reseal runs inside warmstart_h4_from_env)
- utils/candle_h4_hydration_v1.py: +reseal_prior_completed_h4(producer, *, redis_client, now) — bounded ONE-block
  deterministic recovery of the IMMEDIATELY-PRIOR completed H4 block. Wired into warmstart_h4_from_env (runs after
  current-block hydrate, BEFORE the D1 warm-start called next in the lifespan) with fault isolation.
- tests: +14.

## Behaviour (deterministic, fail-closed, idempotent, bounded)
- current forming H4 block = h4_bucket_open(now); prior block = current - 4h (ONE block only; no backfill engine).
- Reads the four expected on-the-hour H1 children for the prior block from GOVERNED H1 history (bounded). Recovers
  ONLY if all four exist + are complete + status OK + grid-aligned + no dup/gap; ELSE fail-closed (never fabricate).
- Writes via the SAME governed seal path: history_forward_writer.on_h4_sealed (H4 history, idempotent) + writer.publish
  (H4 latest, GUARDED so it never regresses a newer latest). derive_h4 (4xH1) enforces status OK / 4-of-4 / coverage 1.0.
- D1 integration: the recovered H4 lands in H4 HISTORY, and the subsequent D1 warm-start reconstructs the D1 buffer
  from H4 history including it — exactly once (NO direct on_h4_close -> no double-count). NEVER derives D1 from H1;
  NEVER a 24xH1 shortcut; D1 still seals only from 6 complete OK H4; fixed 22:00 UTC anchor preserved; NEVER marks D1
  ACTIVE from a recovered H4.
- Idempotency: already-present matching H4 -> already_present (no write, no re-feed); existing-but-different H4 ->
  failed_closed (never overwrite). Skips when producer disabled / no enabled history writer. XAU_USD only; no XAUUSD.

## Tests
150 passed (14 new reseal + existing H4/D1 hydration + H4/D1 wire + H4/D1 derivation + PR#63 supervisor + PR#71
catalog-wiring). New coverage: happy path (4xH1 -> OK 4/4), fail-closed (missing/non-OK/incomplete/off-grid/malformed/
existing-mismatch), idempotency (already_present, no second write, index count 1), boundaries (prior in current vs
prev D1 day; 22:00 anchor; no D1 seal <6; no D1-from-H1), D1-receives-recovered-H4-exactly-once, skip guards, warmstart
integration.

## Zero-runtime-mutation certification
No deploy; no restart; no image build/pull; no runtime env/config change; no Redis write; no SQL write; no runtime-
process touch; no instrument-catalog/feed-health/quote/tick activation (PR#71 catalog remains dark by default); no
market_map touch; no auth/security change; no config in code; no secrets; no broad backfill; no manual repair of the
live missing H4; no duplicate keys; no :XAUUSD: output. Pure code + tests. (The reseal writes Redis only at RUNTIME
on a future deploy; this WO does not execute it live — tests use in-memory fake Redis.)
