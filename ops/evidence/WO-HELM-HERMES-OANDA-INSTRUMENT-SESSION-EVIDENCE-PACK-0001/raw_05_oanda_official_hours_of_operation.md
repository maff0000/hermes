# OANDA official Hours of Operation — verbatim CFD session rows (read-only WebFetch)

Source (evidence tier 1, official OANDA documentation):
https://www.oanda.com/bvi-en/cfds/hours-of-operation/
Fetched (UTC): 2026-07-16 (read-only WebFetch; no auth; public page).

Verbatim per-instrument rows extracted (Instrument | Currency | Reference market / tz | Contract size | Session):

| Instrument | Currency | Ref market (tz) | Session (local tz of ref market) |
|---|---|---|---|
| West Texas Oil (WTICO/USD) | USD | New York | Sun - Fri 18:01 - 16:59 |
| US SPX 500 (SPX500/USD) | USD | Chicago | Sun - Fri 17:01 - 15:59 |
| Silver (XAG/USD) | USD | New York | Sun - Fri 18:05 - 16:59 |
| Platinum (XPT/USD) | USD | New York | Sun - Fri 18:01 - 16:59 |
| Copper (XCU/USD) | USD | Chicago | Sun - Fri 17:01 - 15:59 |
| Gold (XAU/USD) | USD | New York | Sun - Fri 18:05 - 16:59 |

DST note (verbatim intent): "Where indicated, the opening or closing time of the session moves forward
by one hour during daylight savings time." OANDA also states hours coincide with the major global markets,
Sunday 17:00 to Friday 17:00 NY time, and CFDs are NOT available during holidays in which the reference
markets are closed (holiday closures follow the reference exchange, not a single global calendar).

## Interpretation notes (labelled — not raw source)
- The "Sun - Fri HH:MM - HH:MM" notation encodes BOTH the weekly envelope AND the implied daily rollover
  break: each trading day the instrument is open from the open-time to the close-time of the following day,
  breaking between the daily close-time and the next open-time (e.g. XAU 16:59 -> 18:05 NY ~= 17:00-18:00 ET).
- Reference-market timezone matters: precious metals (XAU/XAG/XPT) reference **New York**; the index (SPX500)
  and base-metal copper (XCU) reference **Chicago** (CME). Chicago local time is always 1h behind New York and
  US Central/Eastern DST transitions co-occur, so in UTC a Chicago 16:00-17:00 break is identical to a
  New York 17:00-18:00 break on every date. This is why the deployed metals schedule (America/New_York,
  break 17:00-18:00) yields UTC-correct boundaries for XCU even though XCU's true anchor is Chicago.
- The hours page confirms the WEEKLY envelope and IMPLIES the daily break; it does not, on its own, prove the
  break manifests as a HERMES stream gap for a given instrument. For XAU that manifestation is independently
  proven empirically (raw_03). For WTICO/SPX500 it is not (they are not streamed here) -> see dispositions.
