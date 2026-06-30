# 06 — Levels closed-H1 semantics (Part C)

## R2D2 finding
Governed session/intraday levels are computed on **closed H1 candles** → they can lag live price by up to one
hour. ARES/Falcon must not mistake a governed level for a live-tick high/low.

## Metadata added (values UNCHANGED)
`utils/hermes_levels_v1.py` — every level payload now carries a `level_semantics` block, built by
`level_semantics(scope, as_of_candle_close_utc=None)`:

```
"level_semantics": {
    "level_source_granularity": "H1",          # session/intraday closed H1 ("D1" for daily/weekly scopes)
    "level_source_policy": "CLOSED_CANDLES_ONLY",
    "as_of_candle_close_utc": "<ISO Z>|null",  # close of the last CLOSED H1 candle used (sessions_levels_step sets it)
    "forming_candle_excluded": true,
    "live_tick_included": false,
    "note": "HERMES governed levels are AS-OF the last CLOSED source candle (forming candle excluded, no live
             tick); session/intraday derive from closed H1 and lag live price by up to 1h."
}
```

- New optional `as_of_candle_close_utc` param on `build_level_contract` (UTC-normalised). The durable
  `sessions_levels_step` computes it as `last_closed_H1_open + 1h` from today's bounded H1 set.
- **Validation hardened** (`validate_level_contract`): `level_semantics` is required (`GOV-HERMES-LVL-015`);
  policy must be `CLOSED_CANDLES_ONLY` (`-016`); granularity must match scope (`-017`); forming candle excluded +
  live tick excluded (`-018`). A governed level can no longer be published claiming live-tick inclusion.
- **No computed value changed** — only metadata added; the `levels` dict is untouched. The token scan still passes
  (none of the new keys contain regime/risk/decision/auth tokens).

## ARES/Falcon discovery
The semantics ride on the level payload itself (per-key), so any consumer reading `hermes:levels:XAU_USD:{scope}:v1`
sees the as-of policy inline. `LEVEL_SEMANTICS_NOTE` is also exported for control-plane/catalog documentation reuse.

## Tests
`test_levels_payload_carries_closed_h1_semantics`, `test_levels_as_of_close_recorded_when_supplied`,
`test_daily_scope_semantics_granularity_d1`, `test_validate_rejects_missing_semantics`,
`test_validate_rejects_live_tick_included`, plus all pre-existing sessions/levels tests (22) still pass.
