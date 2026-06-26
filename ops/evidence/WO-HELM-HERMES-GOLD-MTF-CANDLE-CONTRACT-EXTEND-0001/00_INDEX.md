# Evidence Index — WO-HELM-HERMES-GOLD-MTF-CANDLE-CONTRACT-EXTEND-0001

**WO:** GOLD MTF Candle Contract Extension (shadow-first, code-build only)
**Owner:** HELM (HERMES lane) · **Branch:** `wo/WO-HELM-HERMES-GOLD-MTF-CANDLE-CONTRACT-EXTEND-0001`
**Base:** `af2679b` (origin/main) · **Posture:** SHADOW-FIRST, canonical DARK, NOT activated

## What this PR does
Extends the governed HERMES candle contract + dev-shadow seam to the DIRECT-NATIVE grid **M1 / M5 / M15 / H1**
for `XAU_USD` and every configured instrument, adds a deterministic candle-geometry block
(`body_high/body_low/body_size/range_size/wick_high/wick_low/candle_direction`), and **fixes the wick
semantics** so wick fields are true sizes — never aliased to the high/low price. Canonical publish stays
dark; no regime; no deploy/restart/Redis/SQL/backfill.

## Files
| # | File | Proves |
|---|------|--------|
| 00 | `00_INDEX.md` | this index |
| 01 | `01_scope_and_constraints.md` | scope + the full forbidden-list compliance |
| 02 | `02_code_diff.patch` | the exact code diff (utils/tests/config/.env.example) |
| 03 | `03_timeframe_grid.md` | M1/M5/M15/H1 TTL + redis_ex grid |
| 04 | `04_wick_semantics_proof.txt` | worked wick example + no-alias proof |
| 05 | `05_geometry_and_direction.md` | geometry/direction field semantics |
| 06 | `06_sample_payloads.json` | built+validated payloads, one per grid TF |
| 07 | `07_validation_failloud_matrix.txt` | GOV-CANDLE-CONTRACT-* tamper rejections |
| 08 | `08_instrument_canonicalisation.md` | XAUUSD→XAU_USD, no dual-publish |
| 09 | `09_shadow_key_topology.md` | versioned `:v1` shadow keys only; canonical dark |
| 10 | `10_regime_exclusion.md` | architect Option-A; no regime; forbidden-token scan |
| 11 | `11_no_activation_proof.md` | flags unchanged; no Redis/SQL/backfill/restart |
| 12 | `12_test_results.txt` | full pytest run (172 passed) |
| 13 | `13_test_inventory.md` | test list + what each proves |
| 14 | `14_deferred_scope.md` | M15 aggregator / H4 / D / canonical deferred |
| 15 | `15_final_report.md` | 22-point structured final report |

## Headline result
`172 passed` across the candle + model suites. Verdict sought: **GREEN_PR_OPEN_GOLD_MTF_CONTRACT_EXTEND_SHADOW_FIRST**.
