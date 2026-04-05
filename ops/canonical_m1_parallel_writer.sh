#!/bin/bash
# Canonical M1 Parallel Writer — WO-HERMES-PROOF-H
# Runs via cron every minute. Copies latest M1 candles to canonical_m1
# with provenance, to build the parallel-run dataset for equivalence proof.
# Setup: * * * * * /srv-dev/tradingSignals/ops/canonical_m1_parallel_writer.sh

PW=$(grep DEV_DB_PASSWORD /srv-dev/tradingSignals/.env | cut -d= -f2)
DB="mysql -h 127.0.0.1 -P 3307 -u root -p${PW} tradingSignals -N"

# Copy any candles_M1 rows from last 5 minutes that aren't yet in canonical_m1
$DB -e "
INSERT INTO canonical_m1
(instrument, minute_bucket_utc, open, high, low, close, volume,
 source_id, ingest_mode, arrival_utc, complete, description, llm_reasoning)
SELECT
    c.instrument,
    c.timestamp,
    c.open, c.high, c.low, c.close, c.volume,
    'oanda_stream_parallel',
    'LIVE_FIRST_ACCEPTED',
    NOW(3),
    1,
    CONCAT('Parallel-run copy from candles_M1: ', c.instrument, ' ', c.timestamp),
    '{\"rationale\": \"WO-H parallel-run: canonical_m1 populated alongside candles_M1 for equivalence proof\", \"source\": \"canonical_m1_parallel_writer\"}'
FROM candles_M1 c
WHERE c.timestamp >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 5 MINUTE)
AND NOT EXISTS (
    SELECT 1 FROM canonical_m1 cm
    WHERE cm.instrument = c.instrument
    AND cm.minute_bucket_utc = c.timestamp
)
" 2>/dev/null
