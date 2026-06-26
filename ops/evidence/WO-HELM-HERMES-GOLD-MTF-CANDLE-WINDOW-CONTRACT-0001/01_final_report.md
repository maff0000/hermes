# Final Report — WO-HELM-HERMES-GOLD-MTF-CANDLE-WINDOW-CONTRACT-0001
**Verdict sought:** `GREEN_PR_OPEN_GOLD_MTF_CANDLE_WINDOW_CONTRACT_CODE_ONLY`

Implemented the governed Redis candle history/window contract (`utils/candle_history_v1.py`) — key schema,
target guards, payload+history block, 35-day retention, idempotent ZSET index, and the dry-run per-day/
per-TF gap-profile format. Additive module + 18 tests; **156 candle-lane tests pass**. No Redis I/O, no
execution, no deploy/activation. Hard-separated from live `:latest:v1` (guard fails loud). Ready for the
gated dry-run → execute backfill WOs.
