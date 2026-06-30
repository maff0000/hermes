# NO-AUTH / NO-ACL / NO-NOAUTH / NO-REGIME PROOF
- No authentication added; no Redis ACL; no NOAUTH/credential gate; no security hardening. The forbidden
  field-key scan rejects password/secret/credential/apikey/acl/noauth tokens as field keys; payload blob
  contains none (test_no_auth_tokens_anywhere). redis_target is secrets-redacted (host:port/dbN; '@'/password rejected).
- No regime/risk DATA fields: _scan_no_forbidden_field_keys rejects regime/risk/order_block/liquidity as field
  KEYS (test_forbidden_field_key_rejected, test_health_no_regime_or_risk_fields, test_manifest_validates_and_no_regime_data_field).
  Ownership TEXT values may NAME regime/risk to declare them ARES-owned — HERMES declares NO regime ownership.
