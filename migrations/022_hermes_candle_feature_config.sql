-- ============================================
-- Migration 022: hermes_candle_feature_config — governed candle-classification thresholds
-- WO: WO-HELM-HERMES-CANDLE-H4-M30-FORWARD-DERIVATION-AND-FEATURES-0001
-- Database: tradingSignals
-- Date (UTC): 2026-06-13
--
-- WHY (R2D2 B-CONST): candle classification thresholds (doji/full-body/long-wick/pin-bar/
--   swing_lookback) CHANGE classification output and are trading-sensitive — they are NOT
--   mathematical invariants. The no-config-in-code rule forbids them as hidden module constants.
--   This table makes them GOVERNED config: utils/candle_features.load_config() reads the enabled
--   row and FAILS LOUD if missing/disabled/malformed; there is NO module-constant fallback.
--   Append-only INSERT (re-apply fails loud on UNIQUE config_key). CREATE-only / inert here.
--   All timestamps UTC.
-- ============================================

CREATE TABLE IF NOT EXISTS hermes_candle_feature_config (
  id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  config_key       VARCHAR(64)  NOT NULL,
  config_value_json LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL
                       COMMENT 'JSON of the 6 governed thresholds'
                       CHECK (json_valid(config_value_json)),
  enabled          TINYINT(1)   NOT NULL DEFAULT 1,
  description      TEXT         NOT NULL,
  llm_reasoning    TEXT         NOT NULL,
  created_at_utc   DATETIME(3)  NOT NULL,
  updated_at_utc   DATETIME(3)  NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_config_key (config_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Governed HERMES candle-feature classification thresholds (no config-in-code).';

INSERT INTO hermes_candle_feature_config
  (config_key, config_value_json, enabled, description, llm_reasoning, created_at_utc)
VALUES
('candle_feature_classification:v1',
 '{"doji_body_to_range_max": 0.10, "full_body_min_body_to_range": 0.80, '
 '"long_wick_ratio_min": 0.50, "pin_bar_wick_to_body_min": 2.0, '
 '"pin_bar_body_to_range_max": 0.35, "swing_lookback": 2}',
 1,
 'v1 governed candle-classification thresholds for HERMES candle_features. All values bounded '
 '0..1 except pin_bar_wick_to_body_min (>0) and swing_lookback (int>=1). Loaded explicitly; '
 'fail-loud if missing/disabled/malformed/out-of-range. No module-constant fallback.',
 '{"rationale": "Classification thresholds change candle-feature output (doji/full-body/long-wick/pin-bar/swing) and are trading-sensitive, so they must be governed config not code constants (R2D2 B-CONST). Seeded with the prior values to preserve behaviour while making them governable.", "source": "WO-HELM-HERMES-CANDLE-H4-M30-FORWARD-DERIVATION-AND-FEATURES-0001"}',
 UTC_TIMESTAMP(3));
