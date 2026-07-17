# July 16 2026 metals-break transition — reconnect chronology (fixture of record)

Metals break NY 17:00-18:00 EDT = **21:00-22:00Z**. Raw log + analysis:
`/srv-dev/hermes-evidence-mirror/WO-HELM-HERMES-MODE-C-LIVE-TRANSITION-FINALISATION-0001/`.

| UTC | Event | Transport truth | Under contract v1 |
|---|---|---|---|
| 21:00 | Metals break begins; XAU classified MARKET_CLOSED_EXPECTED | socket CONNECTED, fault NONE, heartbeats flowing | XAU suppressed (governed_closed) — no incident, no vote |
| 21:01 | OANDA-initiated disconnect #1 | provider disconnect event | RECONNECT_AUTHORISED (emergency) — handled by outer-except path (correct today) |
| 21:03 | OANDA-initiated disconnect #2 | provider disconnect event | RECONNECT_AUTHORISED (emergency) |
| 21:00-22:00 | SPX500_USD + WTICO_USD (UNVALIDATED) stale, RED | socket CONNECTED, fault NONE, heartbeats flowing | **RECONNECT_NOT_AUTHORISED / RECOVERY_PROPOSAL_ONLY** (UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY) |
| 21:09 | Forced full-stream reconnect #1 (authority = SPX500_USD sustained_red) | transport healthy | **PREVENTED** under contract |
| 21:19 | Forced full-stream reconnect #2 | transport healthy | **PREVENTED** |
| 21:29 | Forced full-stream reconnect #3 | transport healthy | **PREVENTED** |
| 21:39 | Freshness limiter SUPPRESSED (3/hr reached) | transport healthy | n/a — no authority in the first place |
| 22:04:00 | XAU first real M1 after reopen | flow resuming | MARKET_REOPENING_GRACE — absence tolerated |
| 22:16:14 | XAU feed GREEN after sustained flow | healthy | TRANSPORT_HEALTHY |

**Net effect of contract v1:** the 3 spurious full-stream reconnects (21:09/21:19/21:29Z) do NOT happen because
no transport-authority condition existed (socket CONNECTED + heartbeat flowing + no all-validated-stale). The 2
genuine OANDA-initiated disconnects (21:01/21:03Z) STILL reconnect. XAU stays truthfully closed then reopens
cleanly. No false GREEN, no fabricated freshness, no schedule guessing.

Proven by fixture #1 (`test_fixture_01_july16_..._NOT_AUTHORISED`) and fixtures #3/#12/#16 (genuine disconnect
still reconnects).
