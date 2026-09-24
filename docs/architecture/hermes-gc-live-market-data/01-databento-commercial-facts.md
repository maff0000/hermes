# HERMES GC Live Market Data — Databento Commercial Facts

**Initiative:** `HERMES GC Historical + Live Market-Data Doctrine`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`00-purpose-and-terminology.md`](00-purpose-and-terminology.md).

> **PROVIDER FACTS VERIFIED 2026-09-24 — SUBJECT TO FUTURE COMMERCIAL CHANGE.**
> Everything in this document is a snapshot of Databento's commercial terms as understood on
> 2026-09-24. Vendor pricing, entitlements, and packaging change over time and without this document
> being updated. **Do not rely solely on this document for any future purchasing, activation, or
> subscription-change decision.** Re-verify commercial terms directly against Databento (account
> console, current published pricing, and current subscription-pricing documentation) before activating
> or changing any subscription. See §4 for the explicit pre-activation account-verification requirement.

## §1 — CME Globex MDP 3.0 Standard subscription

| Fact | Value (as verified 2026-09-24) |
|---|---|
| Subscription | CME Globex MDP 3.0 **Standard** |
| Price | **$199/month** |
| Coverage | CME, CBOT, NYMEX, COMEX (the full CME Globex venue group) |
| Live market data | **Included** |
| Exchange licence fee (Standard tier) | **No separate exchange licence fee** on the Standard subscription |
| Level-0 (L0) historical | **16+ years** |
| Level-1 (L1) historical | **1 year / rolling last-12-months** |
| Level-2/Level-3 (L2/L3) historical | **1 month** |
| History beyond entitlement | Available **pay-as-you-go (PAYG)** for additional/older history beyond the above entitlements |

## §2 — MBP-1 classification and what the Standard subscription supports for GC

- Databento classifies **MBP-1 = Level-1 (L1) / top-of-book**.
- Under the L1 historical entitlement in §1, the Standard subscription's rolling 12-month historical
  window applies to GC MBP-1.
- The Standard subscription **supports both of the following simultaneously**, for the same GC
  instrument family, under the same subscription:
  1. **Continuous live GC MBP-1 ingestion** (via the live market data entitlement).
  2. **Rolling 12-month historical GC MBP-1** (via the L1 historical entitlement).
- **Older/deeper GC MBP-1 history** — beyond the rolling 12-month L1 window — is obtained via
  **historical PAYG**, not via the Standard subscription's included entitlement.

This is the commercial basis for the architecture doctrine in
[`03-target-architecture-and-transport-boundary.md`](03-target-architecture-and-transport-boundary.md):
one Databento Standard subscription is commercially capable of feeding both the live delivery path and
the durable-corpus/replay delivery path for GC MBP-1, with PAYG reserved for reaching further back than
the rolling 12-month window.

## §3 — Authoritative source references (human verification required)

This document does not fabricate or guess at specific URLs. A human must confirm the exact current links
before this document (or anything derived from it) is treated as a citable commercial reference. The
categories of authoritative source that should be confirmed and linked are:

- Databento's **CME pricing page** (current CME Globex MDP 3.0 subscription tiers and pricing).
- Databento's **MBP-1 schema documentation** (definition of the MBP-1 schema and its L1/top-of-book
  classification).
- Databento's **subscription-pricing announcement(s)** covering the Standard tier terms recorded in §1–§2.

**Action required before commercial reliance:** a human (Helm, or whoever owns the Databento commercial
relationship) should locate and attach the exact current URLs for the three reference categories above,
and re-confirm the figures in §1–§2 are still current at that time.

## §4 — Mandatory pre-activation account verification

Before any live GC subscription is activated or changed, the following must be queried **directly against
the real Databento account** — none of it may be inferred from the HMT-2 historical-acquisition ledger or
from this document:

- Available historical credit balance.
- Active subscription status (is a subscription already active, on what tier).
- Grandfathered-vs-new pricing status (whether the account is on legacy pricing that may differ from the
  current published Standard tier terms in §1).
- Current billing state (any outstanding balance, billing cycle alignment, payment method status).

This requirement exists independently of, and is not satisfied by, the HMT-2 budget accounting in
[`02-hmt2-budget-vs-live-opex-separation.md`](02-hmt2-budget-vs-live-opex-separation.md) — that document
tracks a separate, bounded historical-research budget, not the live account's real-time commercial state.
