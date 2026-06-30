# NO-AUTH / NO-REGIME PROOF
- No authentication implementation (no password=/requirepass/.auth(/ACL/username=/ssl= in the indicator module).
  The forbidden-token denylist REJECTS auth field keys (defense), it does not add auth.
- No regime/regime_confidence/risk/order_block/liquidity/decision/trade DATA fields: _scan_no_forbidden_field_keys
  rejects them as field keys (test_forbidden_indicator_field_rejected, test_indicator_payload_no_regime_risk_decision_fields).
- regime_detector / regime_classifications NOT imported into the indicator module (test_indicators_module_does_not_pull_in_regime_or_signals).
- deterministic_only=true enforced. HERMES publishes no regime/risk/trade decisions.
