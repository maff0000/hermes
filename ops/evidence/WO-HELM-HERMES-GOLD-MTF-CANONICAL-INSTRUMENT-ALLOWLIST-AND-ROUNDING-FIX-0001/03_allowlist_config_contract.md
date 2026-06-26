# Allowlist Config Contract (fail-closed, no hidden defaults)

| Env | Required when `SINK=canonical` | Behaviour |
|-----|-------------------------------|-----------|
| `HERMES_CANDLE_CANONICAL_INSTRUMENTS` | **yes** | comma list of canonical ids (e.g. `XAU_USD`). Missing → `GOV-CANDLE-FWD-SEAM-007`; empty/whitespace → `GOV-CANDLE-FWD-SEAM-007`. Alias entries canonicalise (XAUUSD→XAU_USD). |

- Fail-closed: there is **no default** — canonical publishing cannot start without an explicit allowlist.
- Non-allowlisted instruments are skipped (`INSTRUMENT_NOT_ALLOWLISTED`), counted per-instrument, never written, never log-spammed.
- The allowlist authorises *matching* (canonical) only; output keys are always canonical, never the alias.
- For this WO the authorised set is `XAU_USD`.
