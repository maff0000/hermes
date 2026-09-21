# HMT-0 — GC futures identity and roll

**Initiative:** `HMT-0 — Market Truth v2 Architecture Closure`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`hmt0-closure-report.md`](hmt0-closure-report.md).

## Purpose

Confirmed in `current-state-reconciliation.md` §3: there is no futures-contract-identity concept anywhere
in the current HERMES schema (no `roll`, no contract-month field, no `GC`-specific identity of any kind —
verified by repo-wide grep for `contract_month`, `futures_contract`, `roll_policy` across every `.py` and
`.sql` file, zero hits). This document is therefore genuinely new architecture, not a description of
anything already built, and is written accordingly — as a target design, not a status report.

## §1 — Actual-contract identity is permanent and mandatory

> Every raw/canonical GC event permanently retains the actual traded-contract identity.

A bare `GC` root symbol is **insufficient** as an identity for any stored event or fact. The specific
contract month/year (e.g. `GCZ26` — December 2026 delivery) must be present, permanently, on every raw
event and every canonical record derived from it. This is non-negotiable: a `GC` root-only identity would
make it impossible to later determine which physical contract a historical price/volume figure actually
came from, which is fatal to any serious futures research use (roll-adjustment, calendar-spread analysis,
delivery-month seasonality, liquidity-by-contract analysis all require knowing the exact contract).

- **Raw/native events**: carry the contract identity exactly as the exchange/provider expressed it for
  that event (e.g. Databento's own instrument identifier for the specific GC contract).
- **Canonical records derived from raw events** (per-contract candles, per-contract derived facts): carry
  the same actual-contract identity, never abstracted away, even when the record also participates in a
  continuous series (§2).

## §2 — Continuous futures are derived-only, versioned, and never destructive

A "continuous GC" series (the kind of single, uninterrupted-looking price series a chart or a backtest
typically wants) is:

- **Derived-only.** It is never a primary storage object in its own right with its own independently
  writable identity — it is always computed from the actual-contract history in §1.
- **Versioned.** Different roll policies (e.g. volume-based roll, open-interest-based roll, calendar-day
  roll, back-adjusted vs. panama-adjusted price stitching) produce genuinely different continuous series.
  Each continuous series carries an explicit roll-policy version identity (see
  `derived-fact-taxonomy-and-ownership.md` for the general versioned-derived-fact contract this
  specialises).
- **Retains its contributing actual contract per bar/period.** Every bar in a continuous series records
  which actual contract (§1) contributed that bar's data. A continuous series that has "forgotten" which
  real contract each bar came from is not an acceptable design — the whole point of keeping actual-contract
  identity permanent (§1) is defeated if the continuous derivation discards it.
- **Retains the roll reason/policy that produced it.** Every roll event (the point where the continuous
  series' contributing contract changes) records why it rolled (the policy's trigger — e.g. "volume
  crossed below threshold X relative to the next contract on date Y") and which policy version made that
  determination.
- **Never produced by destructively rewriting the underlying actual-contract history.** A roll-adjustment
  method that works by, say, subtracting a price offset from *historical actual-contract rows themselves*
  to make a chart look continuous is explicitly forbidden. Any price adjustment for continuity purposes
  exists **only** inside the derived continuous-series record, never as a mutation applied back onto the
  actual-contract raw/canonical history.

## §3 — Relationship to the existing HERMES canonical-candle precedent

`migrations/027_darwin_canonical_historical_authority.sql` already establishes a real precedent this
document deliberately follows: canonical H4/D1 candles are derived-only (via
`candle_h4_derivation_v1.derive_h4` / `candle_d1_derivation_v1.derive_d1`), carry their own
`derivation_policy` and `source_policy_epoch` identity columns, and never overwrite or rewrite the
tables they were derived from (the legacy `candles_H4`/`candles_D1` remain untouched, and the new
canonical tables are populated only by governed derivation, never backfilled by mutating a base table).
The continuous-futures architecture in this document generalises exactly that pattern — versioned
derivation identity, non-destructive sourcing — to the GC roll problem. It does not reuse any of that
migration's actual code, tables, or instrument scope (which remains XAU_USD-only); the relationship here
is architectural precedent, not shared implementation.

## §4 — What this document explicitly does not specify

- No concrete table/column schema. No roll-policy algorithm choice (volume-based vs. open-interest-based
  vs. calendar-day, etc.) — that is an implementation decision for HMT-1, if and when separately
  authorised, and is explicitly out of scope for this documentation-only pack.
- No statement about which roll policy(ies) HERMES will ultimately support — this document establishes
  that roll policy must be versioned and recorded, not which policy is correct.
- No claim that any of this is built, tested, or scheduled. This is architecture direction only.
