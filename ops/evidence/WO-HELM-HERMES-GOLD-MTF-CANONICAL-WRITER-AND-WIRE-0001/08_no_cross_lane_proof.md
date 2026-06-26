# No-Cross-Lane Proof

All changes are in the HERMES candle writer/seam/aggregator lane only.

| Constraint | Status |
|------------|--------|
| Changed files | `utils/candle_publisher_v1.py`, `utils/candle_runtime_seam_v1.py`, `models/candle.py` + candle tests only |
| ARES / HELIOS / FALCON / SOLO / NEO | untouched — not referenced |
| Proteus / `signals:candle:*` | not emitted; seam source asserts `signals:candle` absent (`test_no_proteus_no_stale_sql_no_cross_lane`) |
| `structure_engine` | not referenced (tripwire test) |
| Shared-code / PYTHONPATH coupling | none added; cross-app import tripwire (`test_no_cross_app_or_legacy_imports`) still passes |
| Consumer cutover | none — consumers PULL; no consumer wiring changed |
