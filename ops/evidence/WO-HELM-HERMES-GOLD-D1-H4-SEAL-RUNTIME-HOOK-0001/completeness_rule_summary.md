# D1 H4-Child Completeness Rule (RATIFIED) — folded into PR #57
**WO-HELM-HERMES-GOLD-D1-H4-SEAL-RUNTIME-HOOK-0001**

## Rule (hard, not advisory)
A D1 candle may be status=OK ONLY if derived from SIX H4 children where EVERY child is itself status=OK and
complete. A D1 must NOT publish OK if any of the six H4 children is SOURCE_INCOMPLETE / FORMING / STALE /
NO_SOURCE_DATA / GAP_DETECTED / status!=OK / source_count<expected / source_coverage<1.0.

## Implementation
1. `_SealedH4View` (candle_h4_publish_wire_v1.py) now carries the H4 completeness/provenance: status, is_closed,
   source_count, expected_source_count, source_coverage, gap_state, source_timeframe, open_time + OHLCV.
2. `candle_d1_publish_wire_v1.h4_child_is_complete(view)` — fail-closed predicate: timeframe=H4, status=OK,
   is_closed not False, source_count==expected_source_count, source_coverage==1.0, gap_state in {None,NONE},
   open hour on the fixed 22/02/06/10/14/18 UTC grid. Any missing field / non-OK / incomplete -> ineligible.
3. `CanonicalD1Producer.on_h4_close` buffers a child ONLY if `h4_child_is_complete` — otherwise counts
   `d1_skipped_incomplete_child` and does NOT buffer it (rollover still tracked). A short/holed day therefore
   has <6 buffered children.
4. `_seal_and_publish` defense-in-depth: requires EXACTLY 6 buffered children AND re-asserts each is still
   complete-OK; otherwise `d1_skipped_incomplete` + `D1_INCOMPLETE_NOT_PUBLISHED` (no write).

## No silent fallback
No H1-derived replacement, no 24xH1, no direct candles_D1, no synthesised H4, no SOURCE_INCOMPLETE-as-OK,
no D1 history. The incomplete H4 is still published honestly at the H4 layer (H4 latest unaffected).

## Visibility / isolation
`status()` exposes d1_skipped_incomplete_child + d1_skipped_incomplete (+ the H4 producer's d1_hook_* counters).
A completeness skip is a clean (counted) skip, NOT a fault — it never breaks H4 latest or forward-history.
