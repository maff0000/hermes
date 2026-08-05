# XAU Advanced-v1 module-adoption matrix (§7)
WO-HELM-HERMES-ADVANCED-V1-XAU-MODULE-ADOPTION-0001

Seam already merged (PR#130): utils/hermes_advanced_v1_selection_v1.py (selection_for + generic key factory,
byte-identical to existing keys) + utils/hermes_instrument_registry_v1.py (loader + capability_instruments).

## Delivered this stage
- utils/hermes_advanced_v1_readiness_v1.py — migration-readiness guard (§21): proves migration 025 columns +
  metadata version + 'energy' category exist BEFORE the adopted code may publish; fail-closed; never
  auto-migrates/mutates. This is what keeps merging the adopted source SAFE while production migration is held.
- Static-adoption scan contract + tests below.

## Per-module adoption plan (byte-parity by construction: XAU is the only capability-enabled instrument)
| # | module | scope | current XAU coupling | adopted authority | key | parity | status |
|---|--------|-------|----------------------|-------------------|-----|--------|--------|
| 1 | utils/tick_live_emitter_v1.py | A tick | (removed) | selection_for('tick',records); membership generic | tick_latest_key (== tc.canonical_key) | key/schema/TTL/seq GREEN | ADOPTED (26 tests green; CI green; no regression) |
| 2 | utils/hermes_indicators_v1.py | B indicators | CANONICAL_INSTRUMENT in indicator_key+payload+validators+parser | selection_for('indicator'); tfs from metadata | indicator_key(inst,tf) | 6-TF values/keys/TTL | PLANNED |
| 3 | utils/hermes_gaps_v1.py | C gaps | CANONICAL_INSTRUMENT in GAPS_KEY+contract fields+validators; single-aggregate surface | selection_for('gap') | gaps_key(inst) | key/class/payload/TTL | PLANNED |
| 4 | utils/hermes_backfill_status_v1.py | D backfill-status | key from CANONICAL_INSTRUMENT; status-only | backfill_status_instruments() | backfill_status_key(inst) | key/status/flags | PLANNED |
| 5 | utils/hermes_feed_health_v1.py | E health | CANONICAL_INSTRUMENT allowlist | registry_component_state + selection | n/a | pilot/failure/disabled | PLANNED |
| — | levels/sessions/catalog/quote/candle_d1*/candle_history*/hydration/proposal_validator/recovery_planner | G out-of-scope | CANONICAL_INSTRUMENT | UNCHANGED (owner recorded) | — | — | DEFERRED (not in §6) |

## Execution note (why staged, honestly)
Each in-scope module threads CANONICAL_INSTRUMENT through its key factory, payload builder, parser AND
validators, and is covered by governed tests asserting the current XAU-only enforcement (~40 assertions).
The adoption is byte-parity-preserving BY CONSTRUCTION (only XAU is capability-enabled), but MUST be executed
and parity-reviewed ONE MODULE PER BOUNDED PR to avoid regressing governed publication code. This WO delivers
the readiness guard + matrix + static scan; the per-module rewiring is the next bounded execution (start with
module #1 tick — cleanest, already membership-generic).

## Invariants held
Production untouched; migration 025 held (WTICO base_metals in prod); 7 new instruments inactive; no 2nd
OANDA stream; no backfill executor; no deploy/restart.
