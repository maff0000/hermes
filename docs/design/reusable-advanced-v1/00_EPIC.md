# EPIC — HERMES Reusable Advanced-v1 Contract Engine

**WO:** WO-HELM-HERMES-REUSABLE-ADVANCED-V1-CONTRACT-ENGINE-DESIGN-0001
**Base SHA:** ebf49a7360f4ed3201df7b886b5ee3c8db4e24be · **Design only — no production activation.**

## Problem
The advanced-v1 contracts (tick, indicators, gap, backfill, levels, feed-health, candle-features,
sessions, D1 history, candle history) are a **structural `XAU_USD` pilot**: 15 modules hard-code
`CANONICAL_INSTRUMENT = "XAU_USD"` and their `parse_*_instruments()` functions **reject** any non-XAU
value (GOV-HERMES-*-021) and always return `frozenset({"XAU_USD"})`. Base ingestion + candle persistence
+ health already run generically for all 14 configured instruments, but the *advanced* contracts do not.
There are also **17 independent instrument lists** across code/tests/scripts/mock.

## Goal
Exactly **one reusable pipeline** driven by **one canonical registry**. A supported instrument is added
by **one registry record** — no application-code change. The XAU pilot becomes the generic engine; it is
**not** copied seven times.

## Scope — 8 instruments (rollout set)
`XAU_USD` (pilot reference) + `XAG_USD, EUR_USD, GBP_USD, AUD_USD, USD_JPY, SPX500_USD, WTICO_USD`.
Deliberately spans metals / standard-FX / JPY-precision / equity-index / energy. Other configured HERMES
instruments are **out of scope** and remain unchanged.

## Key insight (grounds the whole design)
The **data contracts and Redis key factories are already parameterised by `{instrument}`** (see
`utils/tick_contract_v1.py`, `utils/candle_contract_v1.py`, indicator/feature key builders). The pilot
boundary is a **module constant + fail-loud parser**, not scattered `if instrument ==` logic and not
per-ticker modules. Therefore generalisation is a **bounded lift**, not a rebuild:
1. ratify one registry authority (the SQL `instruments` table + policy columns);
2. lift `CANONICAL_INSTRUMENT` in the 15 modules to a registry-driven allowlist;
3. redesign the `parse_*_instruments()` guards to accept the registry-enabled set (still fail-closed on
   unknown/alias);
4. consolidate the scattered `canonical_key()` functions into one key factory + one serializer;
5. remove/registry-drive the 17 duplicate lists;
6. parameterise tests over `enabled_instruments()`.

## Implementation WO sequence (design → build, each separately authorised)
- **WO-1 Registry authority + policy columns** (append-only migration; loader; validator). Design in `02`.
- **WO-2 Key factory + serializer consolidation** (one module; no behaviour change).
- **WO-3 Generalise the 15 advanced modules** (lift constant + parser; keys already generic).
- **WO-4 Parameterised test suite + independent oracle** (`23`).
- **WO-5 Add-one-instrument acceptance harness** (isolated; registry-only onboarding).
- **WO-6 Isolated 8-instrument replay proof** (shadow Redis + ephemeral SQL; WP3 pattern).
- **WO-7 Shadow-live observation** (consume existing live feed via the running runtime; shadow keyspace only).
- **WO-8 Staged canonical activation by registry flags** (cohorts A→B→C).
- **WO-9 Backfill executor** (separately gated; historical-mutation authority required).
- **WO-10 Independent R2D2 compliance audit** (denominator in `compliance_manifest.yaml`).

## Held actions (this WO authorises NONE of these)
production activation · second OANDA stream · Redis/SQL mutation · schema migration execution ·
backfill execution · consumer/order · label rebuild · any other-application change.

## Next gate
One independent **R2D2 design-assurance audit** (see `31`). Only R2D2 GREEN permits implementation.
