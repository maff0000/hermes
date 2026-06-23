-- 024: close the 12->14 instrument-config blind spot.
-- The aggregator tracks 14 instruments but the `instruments` SSOT had only 12 (SPX500_USD + WTICO_USD
-- absent), so only 12 configs were published to Redis. Additive; idempotent on the UNIQUE symbol key.
INSERT INTO instruments
  (symbol, name, category, pip_value_per_lot, contract_size, default_sl_distance,
   default_tp_multiplier, min_lot_size, max_lot_size, enabled, oanda_compatible, ibkr_compatible)
VALUES
  ('SPX500_USD', 'S&P 500 Index vs US Dollar', 'indices',     1.0000,    1, 25.0000, 1.50, 0.0100, 100.0000, 1, 1, 0),
  ('WTICO_USD',  'WTI Crude Oil vs US Dollar', 'base_metals', 10.0000, 1000,  0.5000, 1.50, 0.0100, 100.0000, 1, 1, 0)
ON DUPLICATE KEY UPDATE enabled = VALUES(enabled);
