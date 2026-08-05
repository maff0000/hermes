-- Isolated-shadow capability activation — DATA ONLY (no schema change, no code change, no per-ticker branch).
-- WO-HELM-HERMES-ADVANCED-V1-EIGHT-INSTRUMENT-SHADOW-0001. Runs AFTER migration 025 in the isolated DB.
-- Enables the SEVEN new Advanced-v1 instruments for the shadow cohort by flipping their registry capability flags
-- (XAU_USD is already enabled by migration 025). The eight-instrument cohort = XAU_USD + these seven. The six
-- non-cohort instruments (XPT_USD, XCU_USD, USD_CHF, USD_CAD, NZD_USD, EUR_GBP) are deliberately left NOT_ENABLED.
UPDATE instruments
   SET tick_contract_enabled      = 1,
       indicator_contract_enabled = 1,
       gap_detection_enabled      = 1,
       registry_provenance        = 'WO-HELM-HERMES-ADVANCED-V1-EIGHT-INSTRUMENT-SHADOW-0001 (ISOLATED SHADOW)'
 WHERE symbol IN ('XAG_USD','EUR_USD','GBP_USD','AUD_USD','USD_JPY','SPX500_USD','WTICO_USD');
