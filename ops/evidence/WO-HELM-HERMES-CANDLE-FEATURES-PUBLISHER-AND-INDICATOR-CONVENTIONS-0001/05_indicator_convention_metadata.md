# Indicator Convention Metadata (Part A)
Every indicator payload now declares method conventions so ARES/Falcon cannot misinterpret values:
- ema_method = STANDARD_2_OVER_N_PLUS_1_SMA_SEED  (EMA multiplier 2/(n+1), seeded with SMA(n))
- rsi_method = CUTLER_SMA_14   (Cutler's RSI: simple average of gains/losses over n — NOT Wilder smoothing; matches utils/indicators.calculate_rsi)
- atr_method = SMA_14          (ATR = SMA of True Range over n; matches utils/atr_calculator.calculate_atr)
- macd_method/bands_method/vwap_method declared only if those families are present.
Computed VALUES are unchanged; only method metadata is added. validate_indicator_contract requires a method
declaration for every indicator family present (GOV-HERMES-IND-017). Not published live in this WO.
