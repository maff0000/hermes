# §28 Deliverables index (traceability for R2D2 assurance)
1 epic .......................... 00_EPIC.md
2 architecture decision record .. 01_ADR_reusable_engine.md
3 canonical registry design ..... 02_registry_and_onboarding.md + instrument_registry_schema.yaml
4 SPX500/WTICO availability proof  06_spx500_wtico_availability.md
5 instrument metadata schema ..... instrument_registry_schema.yaml
6 add-one-instrument contract .... 02_registry_and_onboarding.md (+ proof plan 07_test_plan.md)
7 reusable pipeline design ....... 01_ADR_reusable_engine.md + 03_contracts.md
8 XAU-specific-code inventory .... 04_inventory.md (A)
9 duplicate-instrument-list inv .. 04_inventory.md (C)
10 tick contract ................. 03_contracts.md (Tick)
11 raw-tick SQL recommendation ... 03_contracts.md (Raw-tick SQL decision = BOUNDED RETENTION)
12 candle/wick contract .......... 03_contracts.md (Candle+wick)
13 price-authority decision ...... 03_contracts.md (Price authority = MID, registry-driven; V-1)
14 indicator specification ....... 03_contracts.md (Indicator engine + methods)
15 timeframe matrix .............. 03_contracts.md (uniform M1..D1 via enabled_timeframes)
16 indicator Redis schema ........ 03_contracts.md (hermes:indicators:<INST>:<TF>:v1)
17 gap contract .................. 03_contracts.md (hermes:gaps:<INST>:v1)
18 backfill design ............... 03_contracts.md (state machine; separately gated)
19 health model ................. 05_rollout_health_risks.md (Health & observability)
20 parameterised test plan ....... 07_test_plan.md + tests/design/test_reusable_engine_contract_v1.py
21 add-one-instrument proof plan . 07_test_plan.md (Add-one-instrument proof)
22 isolated eight-instrument plan  05_rollout_health_risks.md (Stage 3)
23 rollout stages ................ 05_rollout_health_risks.md (Stages 0-7)
24 cohort plan ................... 05_rollout_health_risks.md (Cohorts A/B/C)
25 rollback plan ................. 05_rollout_health_risks.md (Rollback)
26 compliance manifest ........... compliance_manifest.yaml
27 implementation WO sequence .... 00_EPIC.md (WO-1..WO-10)
28 risk register ................. 05_rollout_health_risks.md (Risk register)
29 held-actions register ......... 00_EPIC.md + 05_rollout_health_risks.md (Held-actions)

VERDICT TARGET: GREEN_HERMES_REUSABLE_ADVANCED_V1_CONTRACT_ENGINE_DESIGN_READY_FOR_INDEPENDENT_ASSURANCE
Next gate: independent R2D2 design-assurance audit. No implementation/activation authorised.
