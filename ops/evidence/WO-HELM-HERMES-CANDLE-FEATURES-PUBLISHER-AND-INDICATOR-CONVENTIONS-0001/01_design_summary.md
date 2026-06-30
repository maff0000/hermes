# Design Summary — Candle-Features Publisher + Indicator Convention Metadata
**WO-HELM-HERMES-CANDLE-FEATURES-PUBLISHER-AND-INDICATOR-CONVENTIONS-0001 · code-only · HELM/HERMES**

Part A (utils/hermes_indicators_v1.py): every indicator payload now declares a `methods` block — ema_method,
rsi_method (CUTLER_SMA_14), atr_method (SMA_14), macd/bands/vwap if present. Values UNCHANGED. validate requires
a method declaration for every indicator family present. No live publish.
Parts C+D (utils/hermes_candle_features_v1.py new): per-TF versioned key hermes:candle_features:XAU_USD:{TF}:v1;
build_candle_feature_contract (publisher HERMES, contract_version v1, XAU_USD, source_candle_key + source_candle_
open_time_utc, source_candle_contract v1, feature_set_version, deterministic_only=true, features{}, method_config,
freshness_state); validator rejects ARES interpretive feature keys + regime/risk/decision/trade/signal/auth.
Disabled publisher foundation (enabled-without-authorised SystemExit(101); XAU_USD only; D1 gated until D1 latest GREEN;
zero Redis I/O at import). Part E (utils/hermes_control_plane_v1.py): build_health_summary gains candle_features_built/
candle_features_active -> BUILT_NOT_ACTIVE / ACTIVE / NOT_IMPLEMENTED; D1 candle_features gated; never false ACTIVE.
