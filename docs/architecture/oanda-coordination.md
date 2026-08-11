# HERMES OANDA client coordination (DEV + PROD shared credential)

WO-HELM-HERMES-PROD-ACTIVATION-AND-LIVE-VALIDATION-0001 (+ activation addendum).

## Shared-credential model
DEV and PROD HERMES use the SAME authorised OANDA credential/account (live account). They are SEPARATE,
independent clients (own DB, own Redis, own state) that must coordinate as polite clients — not a single stream,
and NOT sharing mutable HERMES state. There is exactly ONE production-authoritative environment: PROD. DEV remains
a non-production live environment.

## OANDA v20 provider limits (external constraints — documented, not hardcoded assumptions)
- REST API: 120 requests/second per requesting IP.
- Streaming API: 20 active streams per requesting IP.
- New connections: max 2 per second.
- Persistent HTTP/stream connections recommended.
- Pricing stream: at most 4 prices/second per instrument; heartbeat ~every 5 seconds.

## Persistent streams
DEV and PROD each maintain their OWN persistent OANDA pricing stream: connect -> remain connected -> reconnect only
when required. No deliberate per-cycle stop/start. (Observed: both streams FLOWING concurrently on the shared account.)

## Scheduled REST phase offset (policy)
Scheduled OANDA REST work that exists in BOTH environments (candle hydration, reconciliation, historical/metadata
refresh, market-hours refresh, recovery polling) must not fire at the same second. Policy: DEV = existing schedule;
PROD = DEV + `OANDA_SCHEDULE_PHASE_OFFSET_SECONDS` (initial 20s). This is CONFIG, not a code constant, and offsets
only REST *request initiation* — it never alters market timestamps, candles, or stream timing (all remain source/UTC).
STATUS: the config value is declared externally; consuming it requires an application capability not yet in source
(current source has no phase-offset) — flagged as a follow-up code WO.

## Local request governor
Conservative local ceiling well below the provider max: initial policy <= 10 REST req/s per HERMES instance
(`OANDA_REST_MAX_RPS`), unless canonical code already enforces stricter pacing. Defensive engineering, not maximal
utilisation. STATUS: declared as config; enforcement capability to be verified/added in a follow-up code WO.

## Reconnect control
Bounded reconnect with backoff (adapters/base.py: 1s -> max 60s, reset on connect), staying comfortably below the
2-new-connections/sec limit. Must never become a rapid reconnect loop; a storm should fail visibly, not hammer OANDA.
Backoff jitter is a recommended enhancement.

## No cross-environment lock
No DEV<->PROD distributed lock. Coordination is deliberately simple: independent persistent streams + external
schedule phase offset + local per-instance pacing + reconnect backoff. Environments stay independent.

## Timestamps
Phase offset affects only request initiation. Market timestamps remain source/UTC truth — never distorted/delayed.
