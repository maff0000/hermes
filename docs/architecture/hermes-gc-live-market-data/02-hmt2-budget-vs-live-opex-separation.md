# HERMES GC Live Market Data — HMT-2 Budget vs HERMES GC Live Opex Separation

**Initiative:** `HERMES GC Historical + Live Market-Data Doctrine`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`00-purpose-and-terminology.md`](00-purpose-and-terminology.md).

## §1 — HMT-2 historical budget (bounded, one-time research budget)

Per `docs/architecture/hmt0-market-truth-v2/hmt2-provisional-acquisition-roadmap.md`, HMT-2 operates under
a bounded historical-data research budget. This pack records the latest confirmed figures for that
budget, distinguishing the two figures that must always be quoted together and never conflated:

| Figure | Value | What it means |
|---|---|---|
| HMT-2 historical budget ceiling | **$100** | The bounded ceiling for the HMT-2 stratified-research-corpus acquisition line (see the roadmap doc's own ~$125/~$100/~$25 envelope structure). |
| **Ledger acquisition spend** | **≈$12.45** *(component of total below)* | Spend recorded specifically against the HMT-2 acquisition ledger. |
| **Total HMT-2 programme spend** | **≈$12.45** | Ledger acquisition spend **plus** pre-ledger reference-series/definitions/pilot baseline spend. |
| Remaining HMT-2 budget | **≈$87.55** | $100 ceiling minus total HMT-2 programme spend (≈$12.45). |

**Always quote both spend figures where relevant** — "ledger acquisition spend" and "total HMT-2 programme
spend" are not interchangeable. The ledger figure covers only ledger-tracked acquisition transactions; the
total programme figure additionally includes pre-ledger reference-series, definitions work, and pilot
baseline spend that predates the acquisition ledger itself. In the currently confirmed figures both happen
to net to the same ≈$12.45, but that is a fact about current spend composition, not a statement that the
two figures are the same thing — future updates to either figure must preserve the distinction.

## §2 — HERMES GC live opex is a separate cost class

The $199/month CME Globex MDP 3.0 Standard subscription (see
[`01-databento-commercial-facts.md`](01-databento-commercial-facts.md) §1) for **live** GC operation is a
**separate, recurring cost class**: **HERMES GC LIVE DATA OPEX**.

- It is **not** the same budget as the HMT-2 historical budget ceiling in §1.
- It must **not** be charged against the bounded $100 HMT-2 historical budget — **even if the same
  Databento provider/account is used** for both. Using the same account is a commercial/administrative
  convenience; it does not merge the two budget lines.
- HMT-2's budget is a **bounded, one-time research allowance** for building the historical corpus. HERMES
  GC LIVE DATA OPEX is an **ongoing recurring operational cost** for keeping a live GC feed running once
  (and if) `HMT-LIVE-1` is authorised and implemented (see
  [`07-hmt-live-1-future-phase-not-authorised.md`](07-hmt-live-1-future-phase-not-authorised.md)). These
  are different kinds of spend with different governance, and must be tracked, reported, and approved
  separately.

## §3 — Practical consequence for future accounting

Any future cost tracking, budget dashboard, or Fabric/ledger entry for GC market data must keep these as
two distinct lines:

1. `HMT-2 historical budget` — bounded ceiling $100, current spend ≈$12.45 (total programme spend;
   ledger-only component also ≈$12.45), remaining ≈$87.55. Governs one-time/PAYG historical acquisition
   only.
2. `HERMES GC LIVE DATA OPEX` — recurring $199/month (subject to re-verification per
   [`01-databento-commercial-facts.md`](01-databento-commercial-facts.md)). Governs the live subscription
   that would feed continuous GC MBP-1 ingestion once authorised.

Before either line changes (a new HMT-2 acquisition, or activation of a live subscription), the real
Databento account must be checked per
[`01-databento-commercial-facts.md`](01-databento-commercial-facts.md) §4 — account state is never to be
inferred from the HMT-2 ledger, and the HMT-2 ledger is never to be updated to reflect live opex spend.
