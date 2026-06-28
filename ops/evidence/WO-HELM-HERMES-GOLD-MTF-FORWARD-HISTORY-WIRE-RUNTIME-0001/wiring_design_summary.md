# Wiring Design Summary — Forward History into Runtime
**WO-HELM-HERMES-GOLD-MTF-FORWARD-HISTORY-WIRE-RUNTIME-0001 · code-only · HELM/HERMES**

## Goal
Give the (already-merged, default-disabled) forward-history writer real runtime callers so closed XAU_USD
M1/M5/M15/H1/H4 candles can be appended to history after a LATER activation. No deploy/activation here.

## Two hook points (surgical, post-publish, never alters latest)
1. **M1/M5/M15/H1** — `CanonicalCandleForwardSeam.emit()` (candle_runtime_seam_v1.py): after the canonical
   latest `publish()` succeeds, `_forward_history(envelope, now)` calls `writer.on_canonical_close`. Only
   CLOSED candles reach the writer (a `is_closed` pre-check skips forming -> writer not even called).
2. **H4** — `CanonicalH4Producer._seal_and_publish()` (candle_h4_publish_wire_v1.py): when a sealed bucket is
   published with status OK (COMPLETE 4/4), `_forward_history(env)` calls `writer.on_h4_sealed`. SOURCE_INCOMPLETE
   / warmup partials are status != OK and are NEVER offered to history. H4 lineage stays H1->H4 (no synthesis).

## No-cycle, no-import-IO
The writer module imports the seam (for instrument canonicalisation), so the seam/producer DO NOT import the
writer at module top. They accept `history_forward_writer=None` (None/disabled -> no-op). Only the `from_env`
factories lazy-import `build_history_forward_writer_from_env()` on the canonical path. No Redis client/I-O at import.

## Fault isolation (fail-loud-visible, latest authoritative)
The latest write happens FIRST and is returned regardless. The history append runs after, wrapped: any fault
(incl. a same-epoch conflict GOV-CANDLE-HIST-FWD-020) is counted + rate-limited-logged and surfaced in the
emit result's `history_forward` block — but NEVER propagated, so a history fault cannot undo or block latest.

## Guards (unchanged, all still run before any history write)
validate_candle_contract -> build_history_write_plan -> assert_history_target(key+index) -> instrument/timeframe
allowlist -> no D1 / no regime / no shadow / no unversioned / no latest-key target. Idempotent SET(TTL 3,024,000s)
+ ZADD(score=member=open_epoch). Latest publication behaviour and payloads are unchanged (deep-copied for history).
