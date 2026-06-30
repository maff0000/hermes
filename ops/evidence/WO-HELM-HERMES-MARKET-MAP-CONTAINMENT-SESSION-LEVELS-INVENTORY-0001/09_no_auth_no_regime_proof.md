# NO-AUTH / NO-REGIME PROOF
- Neither new module adds authentication (no password=/requirepass/.auth(/ACL/username=/ssl=). Forbidden-token denylist
  rejects auth + regime/risk/decision field keys (defense).
- No regime/risk/liquidity/order_block/smart_money/decision/trade/bias/setup DATA fields: validators reject them as
  field keys (test_session_no_regime_risk_decision_rejected, test_level_interpretive_fields_rejected).
- regime_detector NOT imported; legacy hermes:signals:* / hermes:market_map:* not mutated (no .publish/.set; modules do no Redis I/O).
- market_map.py itself carries no regime/risk computation (only a "decision-daemon" CONSUMER name in a docstring).
