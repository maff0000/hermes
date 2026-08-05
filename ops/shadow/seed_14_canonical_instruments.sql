-- Isolated-shadow seed: the 14 canonical HERMES instruments as they exist BEFORE migration 025.
-- WO-HELM-HERMES-ADVANCED-V1-EIGHT-INSTRUMENT-SHADOW-0001. Representative of the production `instruments` SSOT
-- (12 base symbols from config/market_hours_schedule.v1.json instrument_map + SPX500_USD/WTICO_USD from migration 024).
-- Base columns ONLY — no Advanced-v1 metadata (migration 025 adds it). WTICO starts base_metals (025 corrects to energy).
-- pip/contract seeds are representative; not used by the Advanced-v1 registry (which reads metadata columns only).
INSERT INTO instruments
  (symbol, name, category, pip_value_per_lot, contract_size, default_sl_distance, enabled, oanda_compatible, ibkr_compatible)
VALUES
  ('XAU_USD',   'Gold vs US Dollar',              'precious_metals', 1.0000,     1, 5.0000,  1, 1, 0),
  ('XAG_USD',   'Silver vs US Dollar',            'precious_metals', 1.0000,     1, 0.2000,  1, 1, 0),
  ('XPT_USD',   'Platinum vs US Dollar',          'precious_metals', 1.0000,     1, 5.0000,  1, 1, 0),
  ('XCU_USD',   'Copper vs US Dollar',            'base_metals',     1.0000,     1, 0.1000,  1, 1, 0),
  ('EUR_USD',   'Euro vs US Dollar',              'forex_major',     1.0000,100000, 0.0050,  1, 1, 0),
  ('GBP_USD',   'Pound vs US Dollar',             'forex_major',     1.0000,100000, 0.0050,  1, 1, 0),
  ('USD_JPY',   'US Dollar vs Yen',               'forex_major',     1.0000,100000, 0.5000,  1, 1, 0),
  ('USD_CHF',   'US Dollar vs Swiss Franc',       'forex_major',     1.0000,100000, 0.0050,  1, 1, 0),
  ('USD_CAD',   'US Dollar vs Canadian Dollar',   'forex_major',     1.0000,100000, 0.0050,  1, 1, 0),
  ('AUD_USD',   'Aussie vs US Dollar',            'forex_major',     1.0000,100000, 0.0050,  1, 1, 0),
  ('NZD_USD',   'Kiwi vs US Dollar',              'forex_major',     1.0000,100000, 0.0050,  1, 1, 0),
  ('EUR_GBP',   'Euro vs Pound',                  'forex_minor',     1.0000,100000, 0.0050,  1, 1, 0),
  ('SPX500_USD','S&P 500 Index vs US Dollar',     'indices',         1.0000,     1, 25.0000, 1, 1, 0),
  ('WTICO_USD', 'WTI Crude Oil vs US Dollar',     'base_metals',    10.0000,  1000, 0.5000,  1, 1, 0);
