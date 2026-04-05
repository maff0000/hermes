-- WO-HERMES-MACRO-001: Add macro instruments for C-OP trading context
-- WTICO_USD (WTI Oil) and SPX500_USD (S&P 500)
-- EUR_USD already exists in HERMES with full history

-- Add instrument health rows for the new instruments
INSERT INTO hermes_instrument_health
    (instrument, truth_expected, health_state, reason_code, updated_at_utc, description, llm_reasoning)
VALUES
    ('WTICO_USD', 1, 'RED', 'NEW_INSTRUMENT', NOW(),
     'WTI Crude Oil vs USD — macro risk/volatility proxy',
     'Added for C-OP macro context. Oil spikes correlate with gold selloffs (Apr 2 evidence: +13% oil alongside 250pt gold drop). Provides risk/volatility signal for drift enrichment.'),
    ('SPX500_USD', 1, 'RED', 'NEW_INSTRUMENT', NOW(),
     'S&P 500 index vs USD — risk-on/risk-off regime proxy',
     'Added for C-OP macro context. SPX diverges from gold in stress environments. Risk-on/risk-off classification enriches ARES drift module and Trader decision context.')
ON DUPLICATE KEY UPDATE
    description = VALUES(description),
    llm_reasoning = VALUES(llm_reasoning);

-- Add config entries documenting the rate limit governance
INSERT INTO hermes_config (config_key, config_value, value_type, description, llm_reasoning, enabled)
VALUES
    ('oanda_rest_rate_limit_per_second', '4', 'int',
     'Maximum Oanda REST API requests per second. Enforced via sleep(0.3) between sequential instrument calls.',
     'Oanda practice/live API rate limit is 4 requests/second. With 14 instruments, sequential backfill without throttling would exceed this on startup/reconnect. 0.3s sleep between calls keeps us at ~3.3 req/s with safety margin.',
     1)
ON DUPLICATE KEY UPDATE
    config_value = VALUES(config_value),
    description = VALUES(description);
