-- ============================================================================
-- WO-HERMES-PER-INSTRUMENT-RECOVERY-CONFIG-PROMOTION-0001
-- Migration 012: Promote per-instrument recovery thresholds from code
-- constants to governed hermes_config rows.
-- ============================================================================
-- Promotes the 3 thresholds that were added inline (as Python module constants)
-- by WO-HERMES-SIGNAL-SERVICE-DEV-PER-INSTRUMENT-RESUBSCRIBE-REPAIR-0001 into
-- the canonical tradingSignals.hermes_config governance surface. Same values,
-- no behavioural change — only the source-of-truth moves.
--
-- Append-only per project doctrine. Idempotent ON DUPLICATE KEY UPDATE follows
-- the existing pattern (see 011_macro_instruments.sql), where re-apply preserves
-- values and refreshes description/llm_reasoning. UNIQUE constraint on
-- config_key prevents duplicate rows.
--
-- All three values mirror the constants currently live in /srv-dev/tradingSignals/utils/watchdog.py
--   PER_INSTRUMENT_SUSTAINED_RED_THRESHOLD_SEC = 300
--   PER_INSTRUMENT_RECOVERY_COOLDOWN_SEC = 600
--   PER_INSTRUMENT_MAX_RECOVERY_ATTEMPTS_PER_HOUR = 3
-- ============================================================================

INSERT INTO hermes_config
    (config_key, config_value, value_type, description, llm_reasoning, enabled)
VALUES
    (
      'per_instrument_sustained_red_threshold_sec',
      '300',
      'int',
      'Seconds an instrument must remain in health_state=RED in tradingSignals.hermes_instrument_health before the HermesWatchdog raises a per-instrument recovery request. Set the same way as the original WO-HERMES-SIGNAL-SERVICE-DEV-PER-INSTRUMENT-RESUBSCRIBE-REPAIR-0001 in-code constant.',
      '{"rationale":"5 minutes is short enough to detect a real per-instrument feed stall (per the 2026-05-25 XAU_USD incident which lasted 152 min) without firing on transient micro-flaps that recover within seconds. Longer than the 180s candle_staleness threshold to ensure the per-instrument classifier has settled before triggering. Mirrors the value live in code since 2026-05-26 restart at 15:24:22Z.","authorising_wo":"WO-HERMES-PER-INSTRUMENT-RECOVERY-CONFIG-PROMOTION-0001","source_prior":"WO-HERMES-SIGNAL-SERVICE-DEV-PER-INSTRUMENT-RESUBSCRIBE-REPAIR-0001 in-code constant"}',
      1
    ),
    (
      'per_instrument_recovery_cooldown_sec',
      '600',
      'int',
      'Minimum seconds between consecutive per-instrument recovery requests, globally across all instruments. Prevents reconnect storms when an upstream issue affects multiple instruments at once.',
      '{"rationale":"10 minutes is long enough that a forced full-stream reconnect (OANDA v20 has no per-instrument resubscribe) plus the proof window (30s) plus a recovery-and-bars-resume cycle has had time to complete normally. Re-firing inside this window would imply the previous recovery did not work; the max-attempts-per-hour cap protects against that case.","authorising_wo":"WO-HERMES-PER-INSTRUMENT-RECOVERY-CONFIG-PROMOTION-0001","source_prior":"WO-HERMES-SIGNAL-SERVICE-DEV-PER-INSTRUMENT-RESUBSCRIBE-REPAIR-0001 in-code constant"}',
      1
    ),
    (
      'per_instrument_max_recovery_attempts_per_hour',
      '3',
      'int',
      'Maximum per-instrument recovery requests allowed in any rolling 1-hour window. When reached the watchdog logs a fail-loud WARNING and suppresses further requests until the window slides.',
      '{"rationale":"3 attempts/hour caps reconnect storms while still allowing recovery to be attempted multiple times for legitimate sustained issues. Combined with the 600s cooldown this allows up to 3 recovery cycles per hour, each of ~5-10 minutes total elapsed time, which covers the practical recovery space without endless thrashing. If an instrument is RED beyond 3 recoveries in an hour, operator escalation (Discord alert via Trinity alerter) is the correct path.","authorising_wo":"WO-HERMES-PER-INSTRUMENT-RECOVERY-CONFIG-PROMOTION-0001","source_prior":"WO-HERMES-SIGNAL-SERVICE-DEV-PER-INSTRUMENT-RESUBSCRIBE-REPAIR-0001 in-code constant"}',
      1
    )
ON DUPLICATE KEY UPDATE
    config_value = VALUES(config_value),
    value_type = VALUES(value_type),
    description = VALUES(description),
    llm_reasoning = VALUES(llm_reasoning),
    enabled = VALUES(enabled);
