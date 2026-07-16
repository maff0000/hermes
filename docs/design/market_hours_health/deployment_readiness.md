# Market-Hours Stream-Health — Deployment Readiness & Hardening

WO-HELM-HERMES-PR101-MARKET-HOURS-STREAM-HEALTH-DEPLOYMENT-READINESS-0001 · base canonical `13122cf4` · dev-only (correction PR; not deployed)

## Configured instrument inventory (observed 2026-07-16, .env INSTRUMENTS — 14)
FX(8): AUD_USD, EUR_GBP, EUR_USD, GBP_USD, NZD_USD, USD_CAD, USD_CHF, USD_JPY · METALS(4): XAU_USD, XAG_USD, XPT_USD, XCU_USD
· SPX500_USD · ICO_USD. Only **XAU_USD** streams live (sole candle/catalog/gaps). DB `hermes_market_hours`: FX (no break),
metals (break 21:00-22:00 UTC = 17:00-18:00 EDT, DST-blind fixed-UTC); **SPX500_USD + ICO_USD have NO row (NO_POLICY)**.

## Schedule mapping per instrument (hardened config v2)
| Instrument(s) | Resolution |
|---|---|
| XAU/XAG/XPT/XCU_USD | `metals` (Sun18:00→Fri17:00 ET, daily break 17:00-18:00 ET, DST-aware) |
| 8 FX pairs | `fx` (Sun17:00→Fri17:00 ET, no daily break) |
| **SPX500_USD** | `fail_closed_unvalidated` → None → **fail loud (open)** |
| **ICO_USD** | `fail_closed_unvalidated` → None → **fail loud (open)** |
| any other (unknown) | unmapped → None → **fail loud (open)** — NO silent `_default_fx` |

## OANDA evidence hierarchy (used)
No live OANDA doc/API fetch available here. Evidence used, strongest first: (3) HERMES SQL `hermes_market_hours` (metals
daily break 21:00-22:00 UTC) + (2) observed Incident #1104 (XAU no data 20:59-22:04 UTC, 2026-07-15 EDT) → the metals
17:00-18:00 ET break is confirmed. FX: DB rows (Sun-open/Fri-close, no break). **SPX500/ICO: NO empirical evidence
(no DB row, not streamed) → cannot validate → fail closed.**

## SPX500_USD ruling (§4)
`SCHEDULE_AMBIGUOUS_FAIL_CLOSED`. OANDA index-CFD hours (esp. ~16:00-17:00 ET) are NOT the US cash-market hours and are not
validated here. The prior guessed SPX500 schedule (17:00-18:00 ET break, Sun 18:00 open) is REMOVED. SPX500 resolves to None
→ treated as open → genuine staleness still detected; it is NOT marked closed merely because the cash market closes.
Requires OANDA session evidence before a schedule is added.

## Unknown-instrument doctrine (§6)
Hardened: NO silent `_default_fx`. Every configured instrument must resolve via explicit `instrument_map`→`named_schedules`
or be explicitly `fail_closed_unvalidated`. Unmapped/unknown → `load_schedule` returns None → caller behaves as OPEN
(fail loud, normal detection). This closes the ICO_USD hazard (crypto ~24/7 must not inherit an FX weekend and suppress
genuine weekend staleness). Reason codes: `MARKET_HOURS_UNKNOWN_FAILCLOSED`, `MARKET_HOURS_MALFORMED_FAILCLOSED`.

## Configuration-completeness validator (§7)
`validate_config_completeness(cfg, configured_instruments)` (pure, no I/O/secrets): every configured instrument resolves
OR is explicitly fail-closed; named schedules exist; timezone valid; grace bounded; duplicate aliases rejected; unmapped
rejected; version/provenance/holiday_support present. Result on the real 14: ok=True, 12 resolved, 2 fail_closed, 0 unmapped.

## Holiday/exception semantics (§8)
`holiday_support=false`. No governed holiday calendar → NOT holiday-aware. On holidays/provider closures/shortened sessions/
exceptional shutdowns/off-break maintenance the normal weekly/daily rule applies; if the market is actually closed while the
rule says open, expected data is absent and a stale incident MAY fire (NOISE). Deliberate: **noise > fault-hiding**. A
governed holiday calendar is a separate future WO. No external holiday provider added here.

## Runtime-wiring truth (§9) — capabilities LIVE after a future deploy
LIVE (already wired via `main.py` + watchdog seam): `DstAwareMarketHours.is_market_open` (stream-level checker),
`.is_truth_expected` (per-instrument, replaces SQL policy when injected), schedule loader, fail-safe fallback to legacy.
LIBRARY-ONLY (ship in image, NOT live): 5-state model, full-stream quorum, gap classification, richer aggregation, the
config-completeness validator (available for a startup gate but not auto-invoked). The deploy plan must NOT claim these
become live merely by shipping.

## Fallback & failure semantics (§10) — proven
Absent file / malformed JSON / bad timezone / missing mapping / loader raises / naive time → fail OPEN (never suppress),
legacy fail-loud preserved, genuine faults eligible, explicit reason codes, no runner crash, no false GREEN. (Tests.)

## Deployment-mode decision (§11): **Mode C — Live guarded activation** (recommended)
Rationale: SPX500/ICO now fail-closed (safe), all 12 governed instruments resolve, fallback is fail-loud, the seam is
opt-in and already R2D2-audited, no schema/runner change, rollback is immediate (revert deploy source + prior image). Mode A
(dark) would add ceremony without reducing risk (the seam is already inert unless the config loads and injects). Startup
SHOULD run `validate_config_completeness` and fail loud if a configured instrument is unmapped (recommend wiring in the
deploy WO). The activation is limited to the audited inactivity-derived watchdog path only.

## Packaging & config path (§12)
Source: `config/market_hours_schedule.v1.json` (project). Image path: `/app/config/market_hours_schedule.v1.json` via the
existing `COPY --chown=hermes:hermes . /app` (no new Dockerfile step; verify present post-build). Loaded by `main.py`
relative to `__file__` (`config/…`). Ownership hermes:hermes, read-only sufficient. Absent path → main.py logs + falls back
to legacy checker (fail-safe). No config in Python, no `/etc`, no host-global, no secrets, no env var required.

## Future deployment procedure (§13) — NOT executed here
1. Prereq: R2D2 GREEN on this correction PR consumed. 2. Deploy repo FF to the merged canonical SHA. 3. Build image; capture
id/digest. 4. Prove `/app/config/market_hours_schedule.v1.json` present in image; run `validate_config_completeness` at
startup (fail loud on unmapped). 5. Recreate ONLY hermes-signal. 6. Prove runners=9, schedule loaded, checker injected,
health healthy, consumer_live=false. 7. Observe (window below). 8. Rollback on any trigger.

## Rollback (§13)
Prior known-good: source `f69df68`, image `c79100851c05`, container `fa90360969f7`. Steps: redeploy prior image via
`docker compose up -d --no-deps --force-recreate hermes-signal` after retagging/checking out prior source; verify legacy
market-hours behaviour restored, runners=9, feed honest. **Rollback triggers:** startup failure; runner-count change;
unexpected incident suppression; unknown-schedule suppression; expected-open stale not escalating; auth/socket fault
suppressed; feed-health false GREEN; excessive reconnect churn; config-load ambiguity; cross-application mutation.

## Acceptance matrix (§14) — runtime checks for the deploy WO
| # | Scenario | Expected state | Incident | Recovery | Reason/log | Fail if |
|---|---|---|---|---|---|---|
|1|XAU open flow|OPEN_FLOWING|no|no|MARKET_OPEN_FLOWING|incident fires|
|2|XAU daily close (17:00-18:00 ET)|CLOSED_EXPECTED|no|no|EXPECTED_MARKET_CLOSED_NO_FLOW|incident fires|
|3|XAU reopen grace|REOPENING_GRACE|no|no|MARKET_REOPENING_GRACE|incident fires|
|4|XAU no flow after grace|MISSING_AFTER_GRACE|yes|yes|NO_DATA_AFTER_REOPEN_GRACE|no incident|
|5|SPX500 open flow|fail-open (OPEN)|per data|per data|MARKET_HOURS_...FAILCLOSED|marked CLOSED_EXPECTED|
|6|SPX500 16:00-17:00 ET|fail-open (OPEN)|per data|per data|fail-closed|marked closed by cash-market|
|7|SPX500 after-hours|fail-open|per data|per data|fail-closed|suppressed|
|8|FX open while XAU closed|FX open / XAU CLOSED_EXPECTED|per data|per data|—|FX suppressed|
|9|Open stale while closed quiet|open→eligible / closed→suppressed|mixed|mixed|—|closed contributes to full-stream|
|10|Genuine socket disconnect|OPEN_STALE|yes|yes|EXPLICIT_CONNECTION_FAULT|suppressed|
|11|Auth failure|shared_stream_fault|yes|full-stream|SHARED_STREAM_FAULT|suppressed|
|12|Shared-stream failure|full-stream eligible|yes|yes|SHARED_STREAM_FAULT|not eligible|
|13|Missing schedule file|fail-open legacy|per data|per data|fallback log|suppression|
|14|Unknown instrument|fail-open|per data|per data|UNKNOWN_FAILCLOSED|suppressed / inherits fx|
|15|Malformed schedule|fail-open|per data|per data|MALFORMED_FAILCLOSED|suppression / crash|
|16|REST quote fresh, stream stale|stale eligible|yes|yes|—|quote masks stream|
|17|No fabricated freshness|truthful timestamps|—|—|—|key artificially refreshed|
|18|Runner count|9|—|—|—|≠9|
|19|Redis/SQL contracts|unchanged|—|—|—|new key/family/write|
|20|consumer_live|false|—|—|—|true|

## Observation window (§15)
Minimum window must span: ≥1 normal open-market flow period; the daily XAU close (17:00-18:00 ET) + reopen; ≥1 SPX500
after-hours interval (to confirm fail-open, no suppression); and pure-decision fault tests (already covered by the suite —
socket/auth/shared-stream) with no runtime mutation. Practically: a window crossing one 17:00 ET boundary (~≥3h incl.
pre-close open flow + break + post-reopen) + the passing decision-fault tests. Defined as the evidence gate for the deploy WO.
