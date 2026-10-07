# WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001 — governed indicator-history contract (EMA 200 lane)

**Persona:** Rogue (Delivery Controller), per direct Central Architecture dispatch
**Status:** ACCEPTED — Central Architecture has reviewed and accepted this Work Order as durable project
authority and has explicitly authorised dispatch of a bounded Implementer mandate against it (see Acceptance
record below). This supersedes the original "PREPARATION ONLY — not yet accepted for implementation. No
FORGE dispatch has occurred." line this document carried through PRs #185/#186 — that line was accurate at
the time it was written and is retained in this file's Git history, not rewritten, per this project's own
discipline against silently erasing prior state. No HERMES application code, Redis, or SQL has been modified
by this WO itself; implementation is Git-tracked separately per the governance chain (§1).

## Acceptance record

- **Accepted by:** Central Architecture, via explicit instruction to the Delivery Controller (Rogue):
  "ARCHITECT ACCEPTANCE — HERMES PR #185" (accepting PR #185's governance package, merge SHA
  `0981224414b80194fea027dfa13c7cd9f8a8649b`) and, separately, "ARCHITECT AUTHORIZATION — OPEN
  IMPLEMENTATION GATE" — "The Central Architect now authorizes you to open governed implementation of:
  `WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001` under its parent: `PID-HERMES-MVP-001` and all
  amendments/rulings incorporated by merged PR #185," explicitly instructing the Delivery Controller to
  "[s]ynchronise against canonical HERMES main" and record the implementation base before dispatch (§2 of
  that mandate), which this document's "Implementation base" field above already satisfies (PR #186,
  merge SHA `792adafa0bbaff6b2e0898a3b897a95bfc72983b`).
- **Dispatch authorised:** the same "OPEN IMPLEMENTATION GATE" mandate's §3—§14 constitute the bounded
  implementation scope, exclusions, and governance rules a dispatched Implementer (FORGE) must work within
  — reproduced in full in §4—§16 of this document (objective, contract, repair design, retention
  normalisation, exclusions, tests, STOP conditions) and in the dispatch mandate given directly to FORGE,
  which must itself carry the governance chain and hard invariants (per that mandate's §3: "Do not assume
  FORGE remembers them from another session").
- **This correction's own authority:** this status update is itself a Delivery-Controller-level
  durable-record correction (not new architecture, not implementation against this WO's technical scope) —
  making the already-given, already-acted-upon Architect acceptance visible in Git, per this project's own
  "no Git record → not durable project authority" rule. It was prompted by a FORGE Implementer correctly
  refusing to proceed against the stale "PREPARATION ONLY / no FORGE dispatch" line still present in Git at
  dispatch time — exactly the STOP-on-ambiguity discipline this WO requires, working as intended.
**Date:** 2026-10-07
**Repo:** `maff0000/hermes` (canonical checkout `/srv/rogue-hermes/canonical` on dell-debian)
**Parent governance:** `docs/governance/PID-HERMES-MVP-001.md` §4 ("EMA 200 is a SEPARATE WO: it requires its
own history-depth, retention and warm-start design") and §11-§16 (added/amended alongside this WO).
**Base SHA (authored against):** `origin/main = 2da7cc3ead27279d1c4153101e6dbc104d1153ff`
**Implementation base (Architect-authorised, post-governance-merge)**: `0981224414b80194fea027dfa13c7cd9f8a8649b`
(canonical `main` after this WO's own governance package, PR #185, merged). The only commits between the
original authored base and this implementation base are this WO's own merge sequence (`ed5f3b7`, `1ffd3fa`,
`a4ddca9`, merge `0981224`), touching only this file and `docs/governance/PID-HERMES-MVP-001.md` — a
benign, documentation-only, self-referential advance that changes nothing this WO's technical scope depends
on. FORGE must implement against `0981224414b80194fea027dfa13c7cd9f8a8649b`, not the original authored
base.
**Production SHA (observed, separate from canonical base)**: `1c359067b0e00d3947fa314382bc125da398d662`
(`hermes-signal:prod-1c359067b0e0`, host `194.164.122.94`, compose project `hermes-prod`) — labelled
`HERMES_BUILD_CLASSIFICATION=NON_PROMOTED_ENGINEERING_CANDIDATE`; see §9 below, not remediated by this WO.

## 1. Governance chain (binding, copied forward)

```
Architecture decision → PID/Amendment → Git-tracked Work Order → Delivery Controller → Implementer → Independent Audit → PR → Architect Acceptance → Merge → Closure
```

**NO PID → NO WORK ORDER.** **NO WORK ORDER → NO IMPLEMENTATION.** **NO INDEPENDENT AUDIT + ARCHITECT
ACCEPTANCE → NO MERGE.** **NO GIT RECORD → NOT DURABLE PROJECT AUTHORITY.** Additionally, per
PID-HERMES-MVP-001 §8 (binding for every HERMES WO): one WO/branch/worktree/PR; R2D2 GREEN before
merge/deploy/activation; external configuration only; no duplicate utilities or one-off repair scripts —
reuse the governed module, extend, don't fork; canonical/deployed/operational states recorded separately.

## 2. Objective

Enable HELIOS (and any future consumer) to read historical governed indicator values associated with
specific closed bars, computed by HERMES's existing deterministic calculation, without creating a second
indicator implementation and without HELIOS (or any consumer) independently recomputing ATR/EMA from raw
candles. Minimum required coverage: M5 historical `atr_14`; H4 historical `ema_50`; H4 historical `ema_200`.
Target architecture: `HERMES → HERMES-owned Redis → HELIOS`. FALCON is not involved in this transport path.

## 3. Discovery summary (full evidence in the handover; key facts below)

### 3.1 Existing candle-history contract (the pattern to mirror)

- Per-record key: `hermes:candles:{instrument}:{timeframe}:history:v1:{open_epoch}` (`utils/candle_history_v1.py`
  `history_key()`).
- Index: `hermes:candles:{instrument}:{timeframe}:history:v1:index` — a ZSET, score=`open_epoch`,
  member=`str(open_epoch)`, **no TTL on the index itself**, pruned explicitly via `ZREMRANGEBYRANK`/
  `BYSCORE`, never left to expire wholesale.
- Retention: M1/M5/M15/H1 — time-based TTL via `HERMES_REDIS_HISTORY_RETENTION_DAYS` (externally configured,
  fail-loud if unset/non-positive; production = 14 days). H4 — count-based (`H4_HISTORY_RETAIN_COUNT = 250`)
  plus a generous 120-day per-object TTL cap.
- Provenance: a `"history"` block on the standard candle envelope —
  `history_contract_version`, `backfill_run_id` (prefixed `FORWARD:<WO-ID>` for real-time writer-produced
  records vs. a plain run-id for true one-off backfills), `backfill_inserted_at_utc`, `source_table`,
  `source_timestamp_utc`. Confirmed byte-identical on a live production record.

### 3.2 Existing indicator "latest" contract (the computation to reuse, not reimplement)

- Key: `hermes:indicators:{instrument}:{timeframe}:v1` (`utils/hermes_indicators_v1.py`). Currently **no
  TTL** on this key (plain `SET`, no `ex=`).
- Fields (confirmed live): `ema_12, ema_26, rsi_14, atr_14, ema_50, ema_9, ema_21, ema_200,
  bollinger_upper_20_2, bollinger_middle_20_2, bollinger_lower_20_2, adx_14, adx_plus_di_14,
  adx_minus_di_14`, plus a `methods` block declaring each family's exact convention
  (e.g. `ema_method: STANDARD_2_OVER_N_PLUS_1_SMA_SEED`).
- Computation lives in `utils/hermes_runtime_publisher_steps_v1.py::_compute_indicators()`, which calls the
  shared, already-governed `utils.indicators.calculate_ema/calculate_rsi/calculate_bollinger_bands/
  calculate_adx` and `utils.atr_calculator.calculate_atr` — **this WO must reuse these exact functions**, not
  a second implementation.
- `EMA_TREND_PERIOD = 200`, `EMA_TREND_WINDOW = 210`. `ema_200` is computed from `deep_closes` and is
  explicitly, correctly left `null` (never fabricated) when fewer than 200 usable closes are available.
- The window read (`indicator_step()` → `_read_history()`) already consumes the exact same governed candle
  history ZSET+keys described in §3.1 — this is the proven mechanism a historical indicator publish should
  hook into (the publisher already has the deep window in hand at the moment it computes `latest`; the
  additional work is persisting a snapshot of that computation per-bar, not re-deriving it).

### 3.3 Canonical H4 anchor policy

- `SOURCE_POLICY_EPOCH = "H4_FROM_H1_NY1700_V1"` (`utils/candle_h4_derivation_v1.py`), anchors
  `22:00/02:00/06:00/10:00/14:00/18:00 UTC`, fixed grid, no DST shift. Confirmed as the live value in
  production H4 candle envelopes. **Supersedes** an older, inert, never-activated `00/04/08/12/16/20`
  scaffold in `utils/candle_payload_builders.py` (a different, unused key family — not what production runs).
  Any new indicator-history work must anchor to the real, live `H4_FROM_H1_NY1700_V1` policy only.

### 3.4 Production H4 candle-history integrity defect (full detail: PID §12, corrected)

**Corrected per HELM's final read-only verification** (`helm:evidence:hermes:h4_history_final_verification:
20261007T1640Z`, R2D2 reconciliation `r2d2:audit:hermes:h4_history_defect_september_incident_reconciliation:
20261007:v1`). Index `ZCARD` = 250. Of the full 250, **185 history objects exist and 65 do not** —
within just the newest-210 subset the indicator publisher actually reads, 145 exist / 65 are missing (the
same 65; this is consistent, not contradictory, since the gap falls entirely inside that subset). All 65
missing objects fall exactly inside `2026-09-05T14:00:00Z`–`2026-09-16T06:00:00Z` inclusive, zero missing
outside it. 45 of the 65 correspond to conventionally market-open periods; 20 correspond to flat
carry-forward weekend periods — **both classes are equally valid governed H4 market truth** (see PID §13,
§6a below). All 65 have four complete H1 children available in durable SQL and are therefore
deterministically reconstructable. This causes `ema_200` to read `null` in production today — correct
publisher behaviour given a thin history window, not an indicator-code bug.

**Root-cause wording corrected** — the prior claim that "TTL expiry was ruled out" was **incorrect and is
retracted**. Production evidence strongly supports expiry under a historical shorter-TTL regime as the cause
of the contiguous missing H4 object window: all and only the relevant forward-written objects are absent, the
loss window is bounded by retention/deployment changes, and neighbouring survivors demonstrate distinct
historical TTL regimes. The exact original TTL of the already-expired objects cannot now be directly
observed, so the precise historical TTL value remains a **supported inference**, not directly observed
forensic fact. No separate root-cause WO is required solely to prove the already-expired TTL.

**Additional finding — imminent expiry of surviving history** (same HELM verification): surviving
forward-written H4 objects from `2026-09-02`/`2026-09-03` have only ~0.1–0.9 days of TTL remaining (true of 5 of 7 independently-sampled objects at those dates; 2 of 7 already carry ~99 days remaining, consistent with the current regime — the finding applies to the subset still on the older regime, not universally to every object dated 2026-09-02/03), under
an apparent older ~35-day TTL regime — see §6a (retention normalisation).

### 3.5 Build/promotion classification (full detail: PID §15)

Not stale, not a one-off skipped step — no promotion mechanism (`PROMOTED` value, "WP3" gate) has ever been
implemented in this codebase; every build is unconditionally `NON_PROMOTED_ENGINEERING_CANDIDATE` by
construction. Separate governance debt, disposition returned to Central Architecture, explicitly not in this
WO's scope.

### 3.6 Sizing inputs

Current XAU_USD candle-history object counts (ZCARD): M1=13,749, M5=3,922, M15=1,314, H1=335, H4=250, D1=52.
Sample sizes: H4 history record 1,540B JSON / 1,888B Redis footprint; M1 history record 1,485B/1,632B;
`latest` indicator payload (H4) 905B. `hermes-cache` Redis: `used_memory` 488.63MB of 1.5GB `maxmemory`
(`noeviction`, 32% utilised, `evicted_keys=0`) — meaningful headroom. D1's exact retention constant was not
traced in this pass (UNRESOLVED, low priority — D1 is not in this WO's minimum-coverage scope).

## 4. Proposed indicator-history Redis contract (design, not yet implemented)

Mirrors §3.1 exactly, substituting `indicators` for `candles`, and is **not HELIOS-specific in naming**:

- **Per-record key:** `hermes:indicators:{instrument}:{timeframe}:history:v1:{open_epoch}`
- **Index key:** `hermes:indicators:{instrument}:{timeframe}:history:v1:index` — ZSET, score=`open_epoch`,
  member=`str(open_epoch)`, no TTL, pruned explicitly (mirrors §3.1, reuses the same index discipline/helper
  functions where the existing module's shape allows direct reuse rather than a parallel reimplementation).
- **Payload:** the same field set already in the `latest` contract (§3.2) — `ema_9/12/21/26/50/200`,
  `rsi_14`, `atr_14`, Bollinger triplet, ADX triplet, `methods` block — plus a `"history"` block mirroring
  §3.1's shape exactly: `history_contract_version`, `publish_run_id` (the live-publisher equivalent of
  `backfill_run_id`, prefixed per the existing `FORWARD:<WO-ID>` convention for real-time writes), `
  published_at_utc`, `source_candle_history_key` (a direct pointer to the §3.1 candle-history record this
  indicator snapshot was computed from — new field, smallest possible provenance addition, not a generic
  event ID), `source_timestamp_utc` (= the bar's `open_epoch`, UTC).
- **Natural identity:** `(instrument, timeframe, open_epoch)` — identical in shape to the existing
  candle-history natural key. **No event ID or content hash is introduced**, per the Architect's explicit
  ruling that one is not required to unblock this phase.
- **Write path:** extend the existing `indicator_step()` (§3.2) so that, at the moment it already computes
  and `SET`s the mutable `latest` key for a newly-closed bar, it additionally writes the immutable
  per-bar history record + `ZADD`s the index — the same pattern the candle-history writer already uses
  alongside candle `latest`. No second computation, no new scheduler, no new service.

## 5. Retention design

Mirror each timeframe's own existing **candle**-history retention exactly — no new retention policy, no new
config variable:

- M1/M5/M15/H1 indicator-history: time-based TTL via the same `HERMES_REDIS_HISTORY_RETENTION_DAYS` the
  candle-history writer already reads (externally configured, fail-loud if unset).
- H4 indicator-history: count-based, the same `H4_HISTORY_RETAIN_COUNT = 250` constant and the same 120-day
  per-object TTL cap, reused directly (not a new constant).

Rationale: a historical indicator snapshot cannot outlive the candle-history window it was computed from
being meaningfully verifiable against (§3.2's `_read_history` already depends on the same window), so
matching retention 1:1 avoids inventing a second retention policy to reason about. H4's 250-count cap already
covers the 210-bar `EMA_TREND_WINDOW` with margin, **once §6's repair closes the current 65-object gap** —
this WO's contract design does not itself increase H4 candle-history depth; it depends on that gap being
closed (§6) for `ema_200` history to actually warm up correctly in H4's near-term window.

## 6. H4 history repair/backfill design (candle-history repair, prerequisite — not indicator work)

**Repair scope confirmed: all 65 missing H4 history objects** (PID §12, §3.4), including both market-open
and weekend flat carry-forward bars (PID §13 — weekend bars are valid governed market truth and must not be
excluded).

1. Reconstruct all 65 missing H4 history objects from durable authoritative `candles_H1` SQL children — not
   legacy/stale `candles_H4` SQL — using the existing, already-governed `utils/candle_h4_history_seed_backfill_v1.py`
   mechanism (the same one used for the original H4 bootstrap), reused, not forked, per PID §8's "no
   duplicate utilities" rule. Each bar requires exactly 4 complete H1 children, deriving strictly under the
   canonical `H4_FROM_H1_NY1700_V1` policy and the `22/02/06/10/14/18 UTC` anchors (§3.3) — never fabricated.
2. Never overwrite an existing H4 survivor; halt/fail safely on conflicting existing data; record repair
   provenance (mirroring §4/§7's provenance shape — a `backfill_run_id`-style field identifying this exact
   repair operation).
3. If H1 source data for any bucket in the 65-wide window is itself missing/incomplete at the authoritative
   source, that specific H4 bucket must remain correctly absent — per PID §3's "a gap key is evidence, not
   execution authority" philosophy, an unfillable gap is represented honestly, never synthesised. (No such
   gap in H1 source data has been found for this window — HELM's verification confirmed all 65 buckets have
   4 complete H1 children available — this clause covers the case where that changes on actual execution.)
4. This repair is **candle-history work**, a prerequisite for the indicator-history contract (§4) to actually
   produce populated H4 `ema_200` history, and should be sequenced and authorised as its own bounded WO/PR if
   Central Architecture agrees, consistent with PID §8's one-WO-one-scope discipline. It is not executed by
   this WO document itself — preparation only.

## 6a. H4 retention normalisation design (bounded, alongside §6 — not a new retention policy)

PID §14: a majority (5 of 7 sampled) of surviving forward-written H4 objects from `2026-09-02`/`2026-09-03` carry only ~0.1–0.9 days of
remaining TTL under an apparent older ~35-day TTL regime and are about to expire. Repairing §6's 65 objects
while allowing this additional, currently-valid H4 history to disappear immediately afterward would be
self-defeating for the warm-up depth §5's acceptance criteria require.

1. Identify the affected surviving H4 objects (those carrying a legacy/shorter TTL regime inconsistent with
   the current canonical H4 retention policy, §5: count-based `H4_HISTORY_RETAIN_COUNT=250` + the current
   120-day per-object TTL cap).
2. Preserve their payload bytes/semantic content exactly — no market-value rewrite of any kind.
3. Apply the existing, already-governed current canonical H4 retention policy to these objects — this is
   normalisation to an already-decided policy, not invention of a new one — without extending any object
   beyond that governed policy's own cap.
4. Ensure index/object consistency is preserved throughout (§7 below).
5. Prove, as acceptance evidence, that the required H4 warm-up depth (§5) will not immediately regress
   through legacy TTL expiry once §6's repair completes.
6. **Implementation-design question for the future implementation stage, not decided here**: whether this
   normalisation can safely reuse §6's existing governed H4 repair/retention mechanism directly (e.g. by
   re-writing the affected objects through the same path, which would naturally apply the current TTL), or
   requires a small, separate, explicitly-bounded operation. Either way, it must not become a general-purpose
   TTL-rewriting utility — scope is limited to the specific surviving objects identified in step 1.

Not executed by this WO document itself — preparation only.

## 7. Provenance design

Covered in §4's payload design: `history_contract_version`, `publish_run_id`, `published_at_utc`,
`source_candle_history_key`, `source_timestamp_utc`. No new identifier scheme — natural key
`(instrument, timeframe, open_epoch)` is sufficient and consistent with the existing candle-history pattern,
per the Architect's explicit ruling that an event ID/content hash is not required for this phase.

## 8. Restart/recovery design

No new recovery logic — mirrors the existing candle-history pattern exactly: the index carries no TTL and is
pruned only explicitly; individual records carry the timeframe-appropriate TTL (§5); on restart, reads simply
continue working off whatever is present, exactly as `_read_history()` already does for candle history today.

## 9. Build-classification disposition (not remediated here)

Recorded in PID §15. This WO does not change any label, does not implement a promotion gate, and does not
treat production's `NON_PROMOTED_ENGINEERING_CANDIDATE` classification as a defect specific to this change —
it is a pre-existing, repo-wide condition. Disposition/ownership of a future promotion-gate WO is returned to
Central Architecture.

## 10. Performance/memory assessment

Per-record size comparable to the existing `latest` indicator payload (~900B) plus a small history block —
materially similar to candle-history records (1.4–1.5KB). Write frequency mirrors each timeframe's own bar
close cadence (negligible for H4 at 6/day/instrument; moderate for M5 at up to 288/day/instrument, comparable
in magnitude to M5's existing ~3,922-object candle history footprint, i.e. low single-digit MB at steady
state). Current Redis headroom (32% of 1.5GB `maxmemory` used, `noeviction`, zero evictions) comfortably
absorbs this addition for the stated instrument/timeframe scope. No unbounded recomputation is introduced —
history is written once, at the moment `latest` is already computed, never recomputed on read.

## 11. Exact implementation scope (for a future, separately-authorised implementation stage)

1. New indicator-history key/index writer, reusing §3.2's existing computation and §3.1's existing
   index/TTL helper patterns — extend `hermes_runtime_publisher_steps_v1.py`'s existing `indicator_step()`,
   do not create a parallel publisher.
2. New provenance fields as designed in §4/§7.
3. Retention wiring reusing the exact existing constants/env vars per §5 — no new configuration surface.
4. (Separately sequenced, per §6) H4 candle-history backfill for all 65 missing objects, contingent on
   §6/§6a being separately authorised and executed. The September-incident correlation question (originally
   gating this item) is addressed by the R2D2 reconciliation cited in PID §12
   (`r2d2:audit:hermes:h4_history_defect_september_incident_reconciliation:20261007:v1`), not by a pending
   Fabric-record check — §6 no longer contains a check of that kind.

## 12. Exact exclusions (binding on any future implementation stage)

Do not: implement HELIOS's own Redis-reader adapter (HELIOS-side work, later, its own WO); implement the
HELIOS Redis ACL/credential (privileged deployment/security work, separate HELM-governed WO, per §12 of the
originating mandate); modify FALCON (later PID-07 work); introduce an event-ID/content-hash identity scheme;
build a generic event bus; reimplement ATR/EMA calculation; depend on or extend `hermes:signals:*` (frozen
legacy); rehabilitate legacy SQL indicator values as an interface; change the H4 anchor policy; modify HELIOS
fixtures (even though they use an incorrect `01/05/09...` anchoring, per the originating mandate they are
explicitly out of scope for this HERMES work package); implement a promotion/build-classification gate (§9);
restart, redeploy, or mutate any production service; create Redis ACL users; expose new ports.

## 13. Required tests (for a future implementation stage)

1. Historical indicator records are generated by the same governed calculation as `latest` (shared
   assertion/fixture against `utils.indicators`/`utils.atr_calculator`, not a reimplementation).
2. M5 historical `atr_14` exists for a representative window.
3. H4 historical `ema_50` exists for a representative window.
4. After §6/§6a's repair and retention normalisation: (a) the required H4 history depth exists;
   (b) the newest governed calculation window contains at least 200 valid closes; (c) H4 `ema_200` is
   non-null; (d) H4 `ema_200` is produced by the existing governed indicator implementation (no alternative
   EMA implementation introduced); (e) this holds without excluding or special-casing weekend flat
   carry-forward bars (PID §13).
5. Historical values correspond to the correct closed bar (`open_epoch` alignment).
6. H4 anchoring is the canonical `22/02/06/10/14/18 UTC` grid (§3.3), not the inert alternate.
7. Sufficient H4 history exists for `ema_200` warm-up post-repair.
8. Redis index members do not point at missing history objects (a regression test for the exact defect
   found in §3.4/§6) — explicitly re-run against the full current 250-member production index after
   repair, not merely a fixture.
16. Surviving H4 history identified in §6a retains its original market-value content, byte-identical, after
    retention normalisation — only the TTL/expiry regime changes.
9. Restart/recovery preserves contract correctness (no special-case logic needed, per §8 — test that reads
   continue working off whatever is present).
10. Historical values remain deterministic across replay/recalculation.
11. Timestamps remain governed aware UTC throughout (no naive `datetime.now()`, no SQL `NOW()`/
    `UTC_TIMESTAMP()`, no local-time assumptions).
12. Existing `latest` contracts remain byte-compatible/unaffected.
13. HERMES primary signal/data operation is not materially degraded (write-path latency/volume check per
    §10's assessment).
14. No legacy SQL indicator definition contaminates the new contract.
15. No `hermes:signals:*` dependency is introduced anywhere in the new code path.

## 14. Runtime acceptance plan (for a future implementation stage)

Per PID §5's three-state Indicator Definition of Done (code-in-main / deployed runner / fresh versioned
Redis payload, each recorded separately) and §8's delivery discipline (one WO/branch/worktree/PR, R2D2 GREEN
before merge/deploy/activation, canonical/deployed/operational states never conflated).

## 15. R2D2 review / blueprint impact

Not yet performed — this WO is preparation only. A future implementation candidate requires R2D2 GREEN before
merge/deploy/activation per PID §8. R2D2's existing HERMES blueprint should be updated, once implementation
exists, to record: the new indicator-history key family, its retention/provenance design, the H4
repair/backfill semantics, and the canonical H4 anchor policy (§3.3) as a durable cross-reference for any
future HERMES or HELIOS-side work.

## 16. STOP conditions (binding on any future implementation stage)

Stop and return to Central Architecture, without improvising, if: the governed computation functions
(§3.2) cannot be cleanly reused for historical (as opposed to latest) computation without a structural
change beyond this WO's scope; H1 source data for the gap window is found, upon actual execution, to be
itself incomplete at the authoritative source (§6 item 3's honest-gap outcome); the §6a retention-normalisation
sampling finds the stale-TTL condition is not limited to the specific objects already identified (broader
than the bounded scope §6a describes); or any ambiguity arises about whether an action is inside or outside
this WO's scope. (The September-incident correlation question that previously gated this section is resolved
via the R2D2 reconciliation cited in PID §12 — it is no longer an open STOP condition.)

## 17. Disposition

**ACCEPTED — implementation dispatch authorised.** The original "Preparation complete. Not authorised for
implementation" line above was accurate when written (PRs #185/#186) and is preserved in this file's Git
history, not rewritten. Central Architecture has since reviewed and accepted this governed package (see
Acceptance record above) and explicitly authorised dispatch of a bounded FORGE Implementer mandate against
the implementation base recorded above (`0981224414b80194fea027dfa13c7cd9f8a8649b`). Implementation must
stay strictly within §4—§16 of this document; any architectural ambiguity encountered during
implementation must STOP and return to Central Architecture, per §1 and §16.
