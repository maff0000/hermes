# OANDA Instrument Session Evidence Pack — WTICO / SPX500 / XAG / XPT / XCU

WO-HELM-HERMES-OANDA-INSTRUMENT-SESSION-EVIDENCE-PACK-0001 · base canonical `71ea3bd` · HELM (HERMES market-data lane)
· **evidence + design only; config/market_hours_schedule.v1.json NOT changed; not deployed; UTC throughout.**

## Purpose
Resolve or sharply reduce the remaining market-session evidence uncertainty for the five instruments that were either
mapped-but-unobserved (XAG_USD, XPT_USD, XCU_USD) or explicitly fail-closed (WTICO_USD, SPX500_USD). Produce the strongest
available provider-specific evidence for each and a defensible disposition. No guessed schedules; where provider evidence is
single-source and unobserved the instrument stays fail-closed.

## Evidence sources consulted (strongest first)
1. **Official OANDA documentation** — Hours of Operation page (`oanda.com/bvi-en/cfds/hours-of-operation/`), fetched
   read-only 2026-07-16. Per-instrument weekly session + reference-market timezone + DST note. See
   `ops/evidence/.../raw_05_oanda_official_hours_of_operation.md`.
2. OANDA instrument metadata/API — **not reachable read-only from this node** (no fxTrade API credentials in scope); not used.
3. Provider market-hours metadata — subsumed by (1).
4. **HERMES SQL `hermes_market_hours`** (tradingSignals @ dell proteus-mariadb-dev:3307, read-only) — 12 governed rows.
   Metals (XAU/XAG/XPT/XCU) carry a fixed-UTC maintenance break 21:00-22:00 UTC; **SPX500_USD and WTICO_USD have NO row.**
   See `raw_01_hermes_market_hours_sql.txt`. NOTE: these columns are **DST-blind fixed-UTC** (21:00-22:00 UTC == 17:00-18:00
   EDT in summer but 16:00-17:00 EST in winter) — evidence of the break's EXISTENCE and its EDT UTC-position, not a
   DST-correct schedule. The governed config supersedes it with a DST-aware America/New_York 17:00-18:00 anchor.
5. **Historical HERMES observations (Redis proteus-redis db0, read-only)** — only **XAU_USD** is streamed in this deployment
   (64,909 candle keys; zero candle keys for WTICO/SPX500/XAG/XPT/XCU — see `raw_02`). On **2026-07-15** XAU M1 live snapshots
   ran every minute to **20:58Z** then a hard gap until **22:04Z** (= 17:00-18:00 ET metals break, EDT) — see `raw_03`.
   Independently reproduces Incident #1104. Backfilled REST history instead fills the break with FLAT volume=1 zero-range
   candles (`raw_04`) — i.e. the break is a LIVE-STREAM silence, while REST history returns degenerate filler.
6. Incident/health evidence — Incident #1104 (XAU no data 20:59-22:04Z 2026-07-15) corroborated by (5).
7. Conservative inference — used ONLY as clearly-labelled residual-ambiguity notes; never to justify a live schedule change
   or to suppress runtime truth.

## Timezone / DST truth (all instruments)
OANDA anchors sessions to the **reference-market local time** (New York for precious metals + WTI; Chicago/CME for the SPX500
index and copper) and shifts +1h across DST. America/New_York and America/Chicago DST transitions co-occur and Chicago is
always NY-1h, so in **UTC** a Chicago 16:00-17:00 break === a New York 17:00-18:00 break on every date. Consequence: the
deployed `metals` schedule (America/New_York, daily break 17:00-18:00, weekly Sun 18:00 -> Fri 17:00 ET) produces
UTC-correct daily-break and weekly boundaries for the Chicago-anchored XCU as well, purely by US DST co-movement.

## Evidence matrix

| Instrument | Provider id | Evidence source | Evidence ts (UTC) | Confidence | Weekly session | Daily break | Timezone | DST handling | Holiday handling | Observed runtime behaviour | Unresolved ambiguity | Disposition |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **XAG_USD** | OANDA `XAG_USD` (Silver) | (1) OANDA doc NY 18:05-16:59 + (4) SQL metals row + (5/6) XAU sibling empirical | 2026-07-16 (doc/SQL); 2026-07-15 (XAU empirical) | **MEDIUM** | Sun 18:00 -> Fri 17:00 ET (doc 18:05/16:59) | 17:00-18:00 ET (≈16:59-18:05 NY) | America/New_York | DST-aware (config); SQL row DST-blind | Not governed (`holiday_support=false`); follows NY precious-metal reference; noise>hiding | **Not streamed here** — no XAG candle keys; inherits precious-metals cluster proven via XAU | No DIRECT XAG runtime observation; relies on OANDA doc session-identity to XAU + SQL row | **VALIDATED_SCHEDULE_CANDIDATE** (already deployed `metals`; mapping correct) |
| **XPT_USD** | OANDA `XPT_USD` (Platinum) | (1) OANDA doc NY 18:01-16:59 + (4) SQL metals row | 2026-07-16 | **MEDIUM** | Sun 18:00 -> Fri 17:00 ET (doc 18:01/16:59) | 17:00-18:00 ET (≈16:59-18:01 NY) | America/New_York | DST-aware (config); SQL row DST-blind | Not governed; follows NY precious-metal reference | Not streamed here — no XPT candle keys | doc open 18:01 vs config break-end 18:00 (≤60s, absorbed by 300s reopening grace); no direct obs | **VALIDATED_SCHEDULE_CANDIDATE** (already deployed `metals`; mapping correct) |
| **XCU_USD** | OANDA `XCU_USD` (Copper) | (1) OANDA doc **Chicago** 17:01-15:59 + (4) SQL metals row | 2026-07-16 | **MEDIUM** | Sun 17:00 CT (=18:00 ET) -> Fri 16:00 CT (=17:00 ET) | Chicago 16:00-17:00 CT === NY 17:00-18:00 ET (UTC-identical) | America/Chicago (true) / America/New_York (deployed, UTC-equivalent) | DST-aware; CT/ET co-move so deployed NY anchor is UTC-correct | Not governed; copper follows CME (Chicago) reference holidays, which differ from precious metals | Not streamed here — no XCU candle keys | Deployed `metals`(NY) anchor is UTC-correct only via US DST co-movement; true anchor is Chicago/base-metal cluster (with SPX500), a provenance smell; no direct obs | **VALIDATED_SCHEDULE_CANDIDATE** (deployed mapping UTC-correct; see INERT Chicago-anchor proposal) |
| **WTICO_USD** | OANDA `WTICO_USD` (WTI crude) | (1) OANDA doc NY 18:01-16:59 ONLY (no SQL row; not streamed) | 2026-07-16 | **LOW** | Sun 18:00 -> Fri 17:00 ET (doc 18:01/16:59) | implied 17:00-18:00 ET (≈16:59-18:01 NY) — unobserved | America/New_York | doc: +1h across DST | Not governed; WTI follows NYMEX/CME energy holidays (differ from metals/FX) | **Not streamed here**; no SQL row; break manifestation UNOBSERVED | Single documentary source; daily-break manifestation as a HERMES stream gap unconfirmed for energy; energy settlement micro-halts not covered by hours page | **MORE_EMPIRICAL_OBSERVATION_REQUIRED** (stays fail-closed; INERT candidate provided) |
| **SPX500_USD** | OANDA `SPX500_USD` (US SPX 500 index) | (1) OANDA doc **Chicago** 17:01-15:59 ONLY (no SQL row; not streamed) | 2026-07-16 | **LOW** | Sun 17:00 CT (=18:00 ET) -> Fri 16:00 CT (=17:00 ET) | Chicago 16:00-17:00 CT === NY 17:00-18:00 ET — unobserved | America/Chicago | doc: +1h across DST | Not governed; SPX index CFD follows CME equity-index holidays / early closes | Not streamed here; no SQL row; UNOBSERVED | Single documentary source; prior explicit index-CFD concern; possible extra CME index halts (e.g. 16:00-17:00 CT settlement, plus a brief mid-session halt) not resolved by the hours page; unobserved | **MORE_EMPIRICAL_OBSERVATION_REQUIRED** (stays fail-closed; INERT Chicago-anchored candidate provided) |
| _XAU_USD (reference)_ | OANDA `XAU_USD` (Gold) | (1) OANDA doc NY 18:05-16:59 + (4) SQL metals row + (5/6) DIRECT empirical gap 20:58->22:04Z 2026-07-15 | 2026-07-15/16 | **HIGH** | Sun 18:00 -> Fri 17:00 ET | 17:00-18:00 ET (empirically 20:58->22:04Z EDT) | America/New_York | DST-aware; empirically summer(EDT) | Not governed | Streamed; live gap reproduced read-only | winter(EST) break position asserted by config, empirically shown for summer only | **VALIDATED_SCHEDULE_CANDIDATE** (deployed, validated) |

## Per-instrument rulings

### XAG_USD, XPT_USD — VALIDATED_SCHEDULE_CANDIDATE (MEDIUM)
Official OANDA docs place silver and platinum in the **New York precious-metals cluster** with sessions identical (XAG) or
one-minute-adjacent (XPT open 18:01) to gold's NY 18:05-16:59. The deployed `metals` schedule (America/New_York, break
17:00-18:00, Sun 18:00 -> Fri 17:00 ET) is corroborated by the SQL metals row and by gold's DIRECTLY-observed break. The
deployed mapping is correct; confidence is MEDIUM rather than HIGH only because neither instrument is streamed in this
deployment, so there is no per-instrument runtime confirmation. **No config change required.** To reach HIGH: stream them and
observe one 17:00 ET boundary each (see §Observation).

### XCU_USD — VALIDATED_SCHEDULE_CANDIDATE (MEDIUM, anchor caveat)
Copper references **Chicago/CME** (base metal), not the New York precious-metals desk: OANDA doc Chicago 17:01-15:59. In UTC
this is identical to a New York 17:00-18:00 break on every date (US Central/Eastern DST co-move), so the deployed `metals`
(NY-anchored) mapping yields **UTC-correct** daily-break and weekly boundaries for XCU. The mapping is therefore validated for
runtime purposes and needs **no live change**. Caveat (labelled): the schedule *name* and *anchor* are semantically wrong
(copper is grouped with the Chicago index cluster, not precious metals); a future config SHOULD express copper (and SPX500)
under an explicit `America/Chicago` schedule for provenance clarity — see the INERT proposal below. No per-instrument runtime
observation exists here.

### WTICO_USD — MORE_EMPIRICAL_OBSERVATION_REQUIRED (LOW)
Provider evidence now **exists** (official OANDA doc: New York 18:01-16:59), which is a genuine upgrade from the prior
"no evidence" state and rules out `REMAIN_FAIL_CLOSED_UNVALIDATED` in the strict "absent evidence" sense. But it is a
**single documentary source**: no `hermes_market_hours` row, the instrument is not streamed here, and the daily-break
manifestation as a HERMES stream gap is unobserved for energy (energy CFDs can carry settlement micro-halts the hours page
does not enumerate). This is **not** enough to flip a fail-closed instrument to a live schedule. **Keep WTICO_USD fail-closed**
(the deployed config already does; this WO does not change it). An INERT candidate schedule + fixtures are provided so a future
streaming+observation WO can promote it after one observed 17:00 ET boundary confirms the break.

### SPX500_USD — MORE_EMPIRICAL_OBSERVATION_REQUIRED (LOW)
Provider evidence now exists (official OANDA doc: Chicago 17:01-15:59; effective UTC break === NY 17:00-18:00). Same standing
as WTICO: single documentary source, no SQL row, not streamed, unobserved, and the prior explicit index-CFD concern is only
partially retired — the hours page does not resolve possible additional CME equity-index halts (e.g. the brief daily
settlement halt) that could produce short expected-silence intervals distinct from the main rollover break. **Keep
SPX500_USD fail-closed.** INERT Chicago-anchored candidate + fixtures provided for a future observation WO. Critically, the
prior guessed 16:00-17:00 ET "cash-market" break is still **rejected**: nothing here justifies marking SPX500 closed merely
because the US cash equity market closes.

## INERT design proposal (NOT applied — no config edit in this WO)
Should a future WO obtain per-instrument runtime observation, the following schedules are the evidence-backed candidates.
They are expressed here only; `config/market_hours_schedule.v1.json` is unchanged and WTICO_USD/SPX500_USD remain in
`fail_closed_unvalidated`.

- **WTICO_USD** -> reuse an America/New_York schedule with daily break 17:00-18:00 ET, weekly Sun 18:00 -> Fri 17:00 ET
  (identical shape to `metals`; distinct `energy` name recommended for provenance + future energy-specific halts).
- **XCU_USD, SPX500_USD** -> an explicit **America/Chicago** `chicago_index` schedule, daily break 16:00-17:00 CT,
  weekly Sun 17:00 CT -> Fri 16:00 CT. UTC-identical to the NY 17:00-18:00 metals break on every date; the Chicago anchor is
  the correct provenance. (XCU already resolves correctly under deployed `metals`; this proposal is provenance-only for XCU.)
- Activation of ANY of these requires: (a) the instrument actually streamed, (b) ≥1 observed 17:00 ET / 16:00 CT boundary with
  the break manifesting as expected silence and a clean reopen, (c) R2D2 GREEN, (d) `validate_config_completeness` still ok.
- Until then the live loader keeps WTICO_USD/SPX500_USD -> None -> fail-loud (behave as OPEN; genuine staleness still detected).
  Fail-closed is safe: it never suppresses; at worst it emits benign noise across the (unobserved) break.

## Fixtures (deterministic, using utils/hermes_market_hours_health_v1.py)
`tests/test_oanda_instrument_session_fixtures_v1.py` proves, against the UNCHANGED deployed config, the VALIDATED candidates
(XAG/XPT/XCU) classify correctly across: winter DST (EST), summer DST (EDT), Sunday open, Friday close, daily break, reopening
grace, holiday uncertainty (fail-open on any absent governed holiday), and provider-session ambiguity; and that WTICO_USD /
SPX500_USD **still resolve to None (fail-closed) and fail OPEN** under the real config. The INERT WTICO (NY) and SPX500/XCU
(Chicago) candidate schedules are validated in-test via in-memory candidate configs (NOT the repo config), demonstrating the
proposed schedules are coherent and UTC-equivalent to the metals break — without touching live config.

## Disposition summary
| Instrument | Disposition | Confidence | Live config action |
|---|---|---|---|
| XAG_USD | VALIDATED_SCHEDULE_CANDIDATE | MEDIUM | none (already `metals`, correct) |
| XPT_USD | VALIDATED_SCHEDULE_CANDIDATE | MEDIUM | none (already `metals`, correct) |
| XCU_USD | VALIDATED_SCHEDULE_CANDIDATE | MEDIUM (anchor caveat) | none (deployed UTC-correct); future provenance-only Chicago anchor |
| WTICO_USD | MORE_EMPIRICAL_OBSERVATION_REQUIRED | LOW | none — stays fail-closed |
| SPX500_USD | MORE_EMPIRICAL_OBSERVATION_REQUIRED | LOW | none — stays fail-closed |

## Observation window to promote WTICO/SPX500 (future WO)
Stream each instrument and capture ≥1 crossing of the 17:00 ET (WTI, NY) / 16:00 CT (SPX500, Chicago) boundary: confirm
(a) expected pre-break flow, (b) break manifests as stream silence (not a genuine fault), (c) clean reopen within grace,
(d) no spurious incident, (e) for SPX500, check for any additional short settlement halt. Two clean boundary crossings
(one summer, one winter, or two same-season with a fault-free reopen) lift LOW->MEDIUM and support promotion to
VALIDATED_SCHEDULE_CANDIDATE with a live (guarded) schedule change under a separate config WO.
