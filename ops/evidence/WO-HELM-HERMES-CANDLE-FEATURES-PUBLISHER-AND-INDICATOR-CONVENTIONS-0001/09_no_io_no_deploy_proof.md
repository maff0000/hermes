# NO-I/O / NO-DEPLOY / NO-REDIS-WRITE / NO-SQL-WRITE PROOF
- Code + tests + evidence only. No deploy/restart/activation/Redis write/SQL write.
- Zero I/O at import in hermes_candle_features_v1.py: no import redis / redis.Redis( / .zadd( / requests / socket /
  urllib / pymysql / open( (test_no_redis_io_at_import). All builders pure.
- Candle-feature publisher DISABLED by default; ENABLED+AUTHORISED -> builds payloads, NO Redis I/O. D1 gated.
- Indicator method metadata is a payload-builder change only (no value change, no live publish).
