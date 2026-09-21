# HMT-0 — Current-state reconciliation

**Initiative:** `HMT-0 — Market Truth v2 Architecture Closure`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. No implementation, no schema change, no code change is
authorised or performed by this document or this pack. See
[`hmt0-closure-report.md`](hmt0-closure-report.md) for the full pack index.

## Purpose

Before describing where HMT-0 wants HERMES to go, this document records where HERMES actually is,
grounded directly in the real schema (`schema.sql`), the real migrations (`migrations/001`–`027`), and
the existing `docs/architecture/*.md` / `docs/governance/*.md` corpus — not assumption. Every claim below
was verified by reading the cited file at HEAD of this pack's base commit
(`3f90e640c9c9c1f4a22ba4ac586a1d478f35a997`). Where a concept described elsewhere in this pack does not
yet exist anywhere in the codebase, that is stated explicitly here so a future reader never mistakes
forward-looking architecture for present reality.

## 1. What HERMES is today (governance-bound)

Per `docs/governance/PID-HERMES-MVP-001.md` (binding): HERMES is a **pure market-data fact spine** —
deterministic candles and indicators, gap/completeness transparency, publication validation and
governance. It has **no strategy, recommendation, risk, or trading authority**. Every document in this
HMT-0 pack stays inside that boundary: nothing here proposes HERMES emit a signal, a regime, a risk
score, or a trade recommendation. The new P0 microstructure fact classes described later in this pack
(volume-at-price, CVD, absorption, etc. — see `p0-microstructure-requirements-matrix.md`) are additional
**facts**, in the same governance sense as an existing indicator — not trading decisions.

## 2. Real current schema — what exists, verified against `schema.sql` and `migrations/`

### 2.1 Native OANDA candle tables (live, trustworthy)

`schema.sql` defines `candles_M1` as the base table, with `candles_M5`, `candles_H1` created
`LIKE candles_M1`, and `candles_D1` also created `LIKE candles_M1` (line 27). `candles_M15` exists via a
later migration with a slightly different column set (no `created_at` — see §2.3). These are fed
continuously by the existing live OANDA collection path and are the real, current source of truth for
M1/M5/M15/H1.

### 2.2 Legacy `candles_H4` / `candles_D1` — NON-AUTHORITATIVE (documented, untouched)

Per migration `027_darwin_canonical_historical_authority.sql`'s own header comment (verified by reading
the file in full): legacy `candles_H4` is UTC-00:00-anchored (00/04/08/12/16/20), stale since
2026-06-15 (246 rows only), and is a **different boundary convention entirely** from the new canonical
H4. Legacy `candles_D1` is UTC-midnight-anchored and materially gapped (119 gaps >1 day, max gap 5 days) —
**not** the 22:00Z NY-5PM governed daily boundary the new canonical D1 uses. Both legacy tables remain
untouched by migration 027 and are addressed only as a *retirement roadmap* item in this pack (see
`legacy-migration-roadmap.md` §(a)) — HMT-0 does not touch either table.

### 2.3 Canonical historical authority surface — real, already shipped (PR #166, migration 027)

`migrations/027_darwin_canonical_historical_authority.sql` (merged to `main` at this pack's own base
commit, 2026-09-16) is real and already exists:

- **Two new physical tables**, `canonical_candles_h4` and `canonical_candles_d1`, populated only by the
  governed derivation functions (`candle_h4_derivation_v1.derive_h4` / `candle_d1_derivation_v1.derive_d1`)
  — the same functions the live producer uses. Identity: `(instrument, timeframe, open_time)` UNIQUE.
  `open_time` for D1 is the 22:00Z NY-5PM fixed daily anchor (per column comment in the migration) — this
  is the real, current canonical D1 boundary contract this pack's legacy-migration document refers to.
- **Four views**, `canonical_candles_m1/m5/m15/h1`, exposing the existing live tables unchanged in a
  uniform row shape (each filtered `WHERE instrument = 'XAU_USD' AND complete = 1` — today's canonical
  surface is XAU_USD-only by construction, not by a documented policy statement elsewhere).
- **One unified view**, `canonical_candles`, `UNION ALL` across all six.
- A documented, deliberate M15 provenance gap: `canonical_candles_m15.derivation_generated_at_utc` and
  `.created_at` are honestly `NULL` because the underlying `candles_M15` table has no `created_at` column
  — the migration's own comment states this is a genuine unknown, never fabricated from the market-open
  timestamp. This is the concrete precedent this pack's "no fabricated provenance" discipline (see
  `time-order-sequence-model.md`) is asking the GC programme to generalise.

### 2.4 `darwin_ro` — real, already provisioned (2026-09-16/17, re-verified 2026-09-20)

`migrations/027_darwin_readonly_principal_grants.template.sql` is the real template behind the
`darwin_ro` principal: SELECT-only on the 7 canonical objects above, nothing else. Full chronology
(including the resolved PR-close-time "unresolved" state and a self-caught evidence-gathering incident)
is in `darwin-ro-provenance-chronology.md`. This is existing, closed state — not part of this pack's
forward-looking scope, included here only for completeness of "what already exists."

### 2.5 Ticks and the stored-row `seq` semantic (existing, ratified)

`schema.sql` defines a `ticks` table (`instrument`, `timestamp DATETIME(3)`, `bid`, `ask`, `source`) as
"optional high-resolution storage." `docs/hermes_tick_seq_semantic.md` (read in full) ratifies that the
existing `seq` column (introduced by migrations 015–018) is a **per-instrument monotonic sequence over
stored tick rows**, ordered by `(timestamp, id)` — explicitly **not** a market-time-complete sequence and
**not** an exchange/provider sequence number. This existing, ratified semantic is the direct precedent
this pack's `time-order-sequence-model.md` builds its "never reuse HERMES's own stored-row `seq` as if it
were an exchange sequence" rule on.

### 2.6 OANDA integration (existing, live)

`docs/architecture/oanda-coordination.md` documents the real, existing DEV+PROD shared-credential OANDA
v20 client model: two independent, polite clients on one account, persistent pricing streams, scheduled
REST phase offset, bounded reconnect. Canonical instrument naming is enforced today in code
(`utils/hermes_instrument_catalog_v1.py`, `utils/hermes_feed_health_v1.py`, `utils/candle_d1_publish_wire_v1.py`):
`XAU_USD` is the only canonical output key; `XAUUSD` is accepted only as an **inbound alias of OANDA's own
alternate spelling of the same OANDA feed** and is fail-closed rejected as an output/publish key
(`GOV-HERMES-IC-017`, `GOV-HERMES-IC-021`, `GOV-HERMES-FH-004`). This is a materially different thing from
"Vantage `XAUUSD`" as used in this pack — see the explicit disambiguation and the flagged naming-collision
caution in `source-taxonomy.md` §0.

### 2.7 Redis publication model (existing, live)

`docs/architecture/redis-consumer-integrity-boundary.md` documents the real, live two-layer model:
network perimeter (source-IP allowlist) + Redis ACL (`hermes:*` keys/channels, read-only for consumers,
bounded writer authority for `hermes-signal`). Nothing in this pack proposes changing that boundary.

### 2.8 Legacy `market-map-dev.service` (existing, host-loose, frozen)

Confirmed via repo-wide search of `ops/evidence/`: `market-map-dev.service` is a real, currently-running
host systemd unit (`market_map.py`, historically observed at PID 6843, cwd
`/srv-dev/tradingSignals/services/market-map`), publishing `hermes:market_map:*` and `hermes:signals:*`.
It is referenced as explicitly untouched/frozen across at least ten separate HERMES work orders in
`ops/evidence/` (e.g. `WO-HELM-HERMES-MARKET-MAP-CONTAINMENT-SESSION-LEVELS-INVENTORY-0001/08_legacy_freeze_and_cutover_plan.md`:
*"FROZEN (untouched, not deleted): hermes:market_map:\* + hermes:signals:\* + the host systemd service
market-map-dev.service (PID 6843)."*). This pack does not touch it either; see
`legacy-migration-roadmap.md` §(b) for its retirement roadmap treatment.

### 2.9 Environment / deployment model (existing, live)

`docs/architecture/environment-model.md` and `docs/architecture/deployment-identity-and-config-binding.md`
document the real DEV/PROD split, host-binding invariants, and config-preservation doctrine. Nothing in
this pack proposes a change to that model; the new GC programme, if and when authorised, inherits it
unchanged.

## 3. What is genuinely NEW / forward-looking in this HMT-0 pack

Confirmed absent from the current codebase by direct search (`grep -rn` across `.py`/`.sql` for
`contract_month`, `futures_contract`, `roll_policy`, `Databento`, and a bare `'GC'` instrument identity —
zero hits anywhere):

- **GC futures-contract identity and roll** (`gc-futures-identity-and-roll.md`) — there is no
  contract-month/roll concept anywhere in `schema.sql` or any migration today. This is entirely new.
- **COMEX GC as a source** (`source-taxonomy.md`) — no Databento integration exists in the repo at all
  (zero references). This is entirely new.
- **Vantage `XAUUSD` as a distinct, classified source** — no Vantage market-data integration exists
  either; the only repository hit for the literal token "Vantage" is an existing forbidden-token static
  scan in `tests/test_stream_silent_stall_recovery.py` (`T11_NoForbiddenTokens`, grouped with
  `MetaTrader5`/`order_send`/`place_order`/`dispatch_trade`/`Agent_Smith`), guarding `main.py` against
  execution-oriented tokens leaking into the fact spine. This is flagged explicitly in
  `source-taxonomy.md` §0 as a naming caution for whoever eventually authors HMT-1 — it is not a
  contradiction of this pack's scope (this pack touches no `.py` file), but it means the literal string
  "Vantage" already carries a guarded, execution-adjacent connotation in this codebase and should be
  introduced into any future code path deliberately, not incidentally.
- **P0 microstructure fact classes** (`p0-microstructure-requirements-matrix.md`) — volume-at-price, POC,
  VAH/VAL, LVN/HVN, aggressor volume, delta, CVD, trade-size distributions, aggression clusters, flow
  efficiency, absorption, failed aggression, balance/imbalance, acceptance/rejection, failed auctions,
  squeeze/trapped-flow proxies: none of these are computed anywhere in the current HERMES codebase. The
  current indicator set (per `docs/governance/PID-HERMES-MVP-001.md` §9) is EMA50/Bollinger(20,2)/ADX(14)
  plus EMA200/MACD/VWAP as separate, mostly-not-started lanes — all price-derived, none order-flow-derived.
- **`MarketQuoteEvent` / `MarketTradeEvent` / `TopOfBookEvent` canonical event model**
  (`canonical-market-events.md`) — HERMES today has no canonical, provider-independent event model; it has
  candles (aggregated) and raw ticks (`bid`/`ask` snapshots, not book-depth events). This is new.
- **Three-tier storage model** (`data-lifecycle-and-storage.md`) — HERMES today has exactly one
  operational tier (MariaDB + Redis). There is no research store and no evidence vault anywhere in the
  current infrastructure. Both are new.
- **Deterministic replay architecture** (`deterministic-replay-and-evidence.md`) and **provider
  abstraction boundary** (`provider-abstraction.md`) — no such formal boundary/replay contract exists
  today; the OANDA adapter (`adapters/base.py`) is the closest existing precedent (a single, non-abstracted
  provider integration), referenced there as a concrete comparison point.

## 4. Reconciliation summary

| Concept | Exists today? | Evidence |
|---|---|---|
| OANDA XAU_USD candles/ticks (M1/M5/M15/H1) | YES, live | `schema.sql`, `oanda-coordination.md` |
| Canonical H4/D1 (22:00Z boundary) + unified view | YES, shipped PR #166 | `migrations/027_darwin_canonical_historical_authority.sql` |
| `darwin_ro` SELECT-only principal | YES, provisioned | `darwin-ro-provenance-chronology.md` |
| Legacy `candles_H4`/`candles_D1` (different anchor) | YES, non-authoritative, untouched | migration 027 header comments |
| `market-map-dev.service` | YES, host-loose, frozen | `ops/evidence/WO-HELM-HERMES-MARKET-MAP-*` |
| Tick stored-row `seq` semantic | YES, ratified | `docs/hermes_tick_seq_semantic.md` |
| GC futures identity / roll | NO | repo-wide grep, zero hits |
| Databento / COMEX GC integration | NO | repo-wide grep, zero hits |
| Vantage market-data integration | NO (token exists only as a forbidden-scan entry) | `tests/test_stream_silent_stall_recovery.py` |
| P0 microstructure fact classes (VAP/POC/CVD/etc.) | NO | governance PID §9 indicator inventory |
| Canonical provider-independent event model | NO | no equivalent module found |
| Research store / evidence vault tiers | NO | infra inventory (`docs/architecture/environment-model.md` §4) |

This table is the ground truth the rest of the pack is written against. Any document in this pack that
appears to assume a "NO" row already exists should be treated as an error and flagged, not trusted.
