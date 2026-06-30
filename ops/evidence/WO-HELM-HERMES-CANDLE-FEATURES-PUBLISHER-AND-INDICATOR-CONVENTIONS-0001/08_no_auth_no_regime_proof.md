# NO-AUTH / NO-REGIME PROOF
- No authentication implementation in either module (no password=/requirepass/.auth(/ACL/username=/ssl=).
- No regime/risk/decision/trade/signal/order_block/liquidity/smart_money DATA field keys: forbidden-key scan
  rejects them (test_ares_interpretive_features_rejected, test_feature_payload_no_regime_risk_decision_and_legacy_not_touched).
- regime_detector NOT imported; legacy hermes:signals:* / market_map:* not referenced/mutated. deterministic_only=true.
