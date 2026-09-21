# HMT-0 — Legacy migration roadmap

**Initiative:** `HMT-0 — Market Truth v2 Architecture Closure`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`hmt0-closure-report.md`](hmt0-closure-report.md).

## Purpose

Two real, existing legacy surfaces are affected by this programme's forward direction and need a
governed retirement sequence recorded — without either surface actually being touched during HMT-0. Both
retirement sequences below are **roadmap statements**, not actions. No shutdown, no deprecation flag, no
code change, and no consumer cutover of any kind is performed by this document or by this pack.

## (a) Legacy `candles_D1`

### Current state (verified, `current-state-reconciliation.md` §2.2)

`schema.sql` line 27 defines the real `candles_D1` table (`CREATE TABLE IF NOT EXISTS candles_D1 LIKE
candles_M1`). Per migration `027_darwin_canonical_historical_authority.sql`'s own header comment: this
table is **UTC-midnight-anchored** and **materially gapped** (119 gaps greater than 1 day, max gap 5
days).

### Canonical authority (already shipped)

The canonical XAU D1 authority is **PR #166's 22:00Z-boundary contract**: `canonical_candles_d1`, the
real physical table created by `migrations/027_darwin_canonical_historical_authority.sql`, anchored to
the 22:00Z NY-5PM fixed daily boundary and populated exclusively via `candle_d1_derivation_v1.derive_d1`.
This is already the authoritative D1 surface for any new consumer (e.g. `darwin_ro`, see
`darwin-ro-provenance-chronology.md`).

### Retirement classification

> Legacy 00:00Z-boundary `candles_D1` is **`LEGACY / NON_AUTHORITATIVE / RETIRE_PENDING`**.

### Roadmap sequence (recorded, not performed)

1. **Now (HMT-0 and for the duration of any HMT-1 work):** legacy `candles_D1` remains untouched, unread
   by any new canonical consumer, and unreferenced by any new derived fact in this programme. It is
   frozen in place exactly as migration 027 already left it.
2. **Consumer inventory** (future, separately-scoped WO): identify every existing reader of legacy
   `candles_D1` (if any remain, beyond historical/legacy purposes) before any retirement step proceeds.
3. **Consumer cutover** (future, separately-scoped WO, governed): any identified consumer migrates to
   `canonical_candles_d1` (or the unified `canonical_candles` view) under its own authorised WO — not as a
   side effect of this pack.
4. **Retirement** (future, separately-scoped WO, governed): only after step 3 is complete and verified,
   legacy `candles_D1` may be formally deprecated (e.g. renamed, archived, or dropped) under its own
   explicit authorisation — never implied by this roadmap document.

**Explicit statement per this pack's binding brief: no shutdown of the legacy path happens during HMT-0.**
Steps 2–4 above are not scheduled, not started, and not implied to be imminent by this document's
existence.

## (b) `market-map-dev.service`

### Current state (verified, `current-state-reconciliation.md` §2.8)

`market-map-dev.service` is a real, currently-active host systemd unit running `market_map.py`
(historically observed at PID 6843, cwd `/srv-dev/tradingSignals/services/market-map`), publishing
`hermes:market_map:*` and `hermes:signals:*`. This is confirmed by direct search of this repository's own
`ops/evidence/` history — at least ten separate HERMES work orders explicitly record it as untouched
(`WO-HELM-HERMES-MARKET-MAP-CONTAINMENT-SESSION-LEVELS-INVENTORY-0001`,
`WO-HELM-HERMES-FEED-HEALTH-CONTRACT-V1-0001`, `WO-HELM-HERMES-D1-H4-HYDRATION-WARMSTART-0001`,
`WO-HELM-HERMES-DURABLE-PUBLISHER-WIRING-MAINPY-0001`, and others), consistently describing it as
host-loose (running outside the containerised HERMES application) and explicitly frozen pending its own
containerisation/cutover plan.

### Retirement classification

> `market-map-dev.service` is **`LEGACY / RETIRE_PENDING_CONSUMER_CUTOVER`**, with **in-process HERMES
> sessions/levels as the intended future authority**.

This intended future authority is not invented by this document — it is a direct restatement of the real
existing containerisation/cutover plan already recorded in this repository's own evidence
(`ops/evidence/WO-HELM-HERMES-MARKET-MAP-CONTAINMENT-SESSION-LEVELS-INVENTORY-0001/07_containerisation_containment_plan.md`,
which describes bringing host-loose `market_map.py` inside the HERMES container/application, with a
separate, authorised consumer-cutover window to `stop market-map-dev.service` and retire
`hermes:market_map:*` only after in-process session/level publishing is proven).

### Roadmap sequence (recorded, not performed)

1. **Now:** `market-map-dev.service` remains untouched, exactly as every prior HERMES WO touching this
   area has already deliberately left it. This pack does not add, remove, or modify anything about this
   service, its process, its systemd unit, or its published Redis keys.
2. **In-process session/level publishing** (future, per the existing containerisation plan already on
   record, referenced above, not re-authored here): HERMES's own containerised application becomes able
   to publish equivalent session/level facts in-process.
3. **Proof/soak** (future, governed, per existing HERMES R2D2-assurance discipline generally): the
   in-process path is proven equivalent/superior before any cutover is considered.
4. **Consumer cutover** (future, separately authorised WO): only after step 3, `market-map-dev.service`
   is stopped/disabled and `hermes:market_map:*` retired, per the existing plan already on record.

**Explicit statement per this pack's binding brief: no shutdown of `market-map-dev.service` happens
during HMT-0.**

## What this document does not do

Does not schedule either roadmap. Does not assign either roadmap to a specific future WO number, sprint,
or date. Does not imply either legacy surface is at risk of imminent removal. Existing consumers of
either surface are unaffected by this document's mere existence.
