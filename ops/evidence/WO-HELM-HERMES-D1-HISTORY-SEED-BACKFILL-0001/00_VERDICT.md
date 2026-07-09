# WO-HELM-HERMES-D1-HISTORY-SEED-BACKFILL-0001 — HELM verdict (CODE-ONLY PR)

## GREEN_D1_HISTORY_SEED_BACKFILL_PR_READY_FOR_R2D2_AUDIT
Tracking: HELM_HERMES_D1_HISTORY_SEED_BACKFILL_BUILD::2026-07-09T12:30Z::GREEN_D1_HISTORY_SEED_BACKFILL_PR_READY_FOR_R2D2_AUDIT

R2D2 activation authority consumed:
R2D2_HERMES_D1_CANDLE_HISTORY_ACTIVATION_AUDIT::2026-07-09T12:13:47Z::GREEN_D1_CANDLE_HISTORY_FORWARD_WRITER_ACTIVATION_AUDIT_APPROVED

Branch: wo/WO-HELM-HERMES-D1-HISTORY-SEED-BACKFILL-0001   Base: 277424a7420290781e0f949589511cd1d5d07007
Files changed (2): utils/candle_d1_history_seed_backfill_v1.py (NEW), tests/test_candle_d1_history_seed_backfill_v1.py (NEW)

## Current D1 depth
D1 history index depth 0 (forward writer active; appends only future seals). This engine seeds prior sealed days.

## Source path SELECTED (approved candidate 3 — safe, GREEN)
Reconstruct sealed 6/6 D1 candles from the GOVERNED CANONICAL H4 HISTORY (hermes:candles:XAU_USD:H4:history:v1)
via candle_d1_derivation_v1.derive_d1 — the SAME derivation the live D1 seal uses. Group COMPLETE status-OK H4
children on the fixed NY-5PM grid (22/02/06/10/14/18 UTC), require EXACTLY 6 per D1 bucket, derive, admit ONLY via
candle_d1_history_v1.assert_sealed_complete_d1 (status OK, closed, 6/6, coverage 1.0, no gap). Output matches D1
latest semantics by construction. UTC-only; 22:00Z anchor enforced by d1d.assert_d1_open_anchor.
- FORBIDDEN sources IMPOSSIBLE by construction: NO pymysql/SQL import (stale SQL candles_H4/M30 impossible),
  NO market_map import, NO 00:00-UTC anchor (derive_d1 uses 22:00 NY-5PM), NO fabrication (missing H4 -> reject, never synthesise).
- LIVE SOURCE PROOF (read-only against runtime H4 history depth 218): 30 D1 buckets with exactly 6 complete children
  available -> depth >=26 ACHIEVABLE (>=35 preferred NOT yet: only 30 sealable days present; honest — engine caps at what the source proves).

## Dry-run behaviour (no writes)
dry_run_plan(client): read-only. Returns source_path, config, current_depth, candidate/accepted/rejected counts,
rejection_reasons, earliest/latest candidate ts, idempotency (new/already_present_match/conflict), projected_depth,
meets_min_depth (>=26), meets_preferred_depth (>=35), INERT write-plan count, write_mode=HISTORY_INERT_NO_WRITE,
no_write_proof=True. Tests assert ZERO redis.set/zadd/delete in dry-run.

## Gates added (all DARK — NOT set anywhere)
HERMES_D1_HISTORY_BACKFILL_ENABLED / _AUTHORISED / _DRY_RUN (default true) / _MAX_CANDLES / _MIN_DEPTH.
Default: disabled + dry-run + no writes. Enabled-without-authorised -> SystemExit(101). Live write allowed ONLY
with enabled AND authorised AND dry_run=false (execute_backfill fail-loud GOV-CANDLE-D1-BF-010). NOT executed here.

## Idempotency (by open_epoch)
_idempotency_status: new | already_present_match | conflict (fingerprint over instrument/timeframe/timestamp/OHLC/
source_count, excluding volatile provenance). Match -> skip (reported, not duplicated). Conflict -> fail-loud
GOV-CANDLE-D1-BF-011, NEVER overwrite. Never delete. Re-run is a no-op (test: 2nd execute writes 0, skips all).

## Bounded
Explicit max_candidates (default 60, hard cap 1..400); bounded H4 read (max_candidates*6 + margin, newest-first);
target depth default 26, preferred 35; no unbounded scans; no unbounded writes; no deletes.

## Candidate validation rules (fail-loud / reject)
child count != 6 -> D1_INCOMPLETE_CHILDREN; child hours != NY-5PM grid -> reject; any H4 child non-OK/incomplete/
gapped/coverage<1/non-UTC/non-XAU_USD -> excluded (_h4_complete); derived env not sealable -> D1_NOT_SEALABLE.
XAUUSD H4 excluded (canonical XAU_USD only). No candidate is ever synthesised to reach 6.

## Tests (17 focused, all pass)
dark-by-default config / enabled-without-authorised SystemExit / live-write needs both gates + dry_run false /
source is H4 history (no pymysql/SQL/market_map/direct-redis import) / valid sealed 6/6 accepted (>=26) /
incomplete day rejected not synthesised / non-OK H4 excluded / XAUUSD H4 excluded / dry-run performs NO writes /
dry-run reports earliest-latest+reasons / idempotency new/match/conflict / dry-run flags conflict + skips match /
bounded max_candidates / execute refuses without gates / fully-gated execute writes D1-history-only + idempotent +
no delete / conflict fails loud never overwrites / no interpretive tokens in code.
Touched-area non-regression: D1 history + derivation + wire = 53 passed.
FULL SUITE: branch == pristine main 277424a (52 failed + collection errors, identical pre-existing env/plugin noise) -> 0 NEW failures; +17 passing.

## Forbidden-token scan (explained)
regime/risk/strategy/signal/trade x1 -> module NEGATIVE-declaration docstring; score x1 -> index_score (ZSET ordering);
exit x2 -> Python SystemExit(101) halt; market_map x5 -> negative declarations + report field source_forbidden_market_map
(PROVES not-a-source); XAUUSD x1 -> _ALIAS_DENY guard. No interpretive logic entered HERMES.

## Boundaries (CODE-ONLY)
No runtime mutation. No deploy. No backfill executed. No Redis live writes/deletes. No SQL writes. No market_map.
No D1 indicators/features/levels activation. No catalog overclaim (D1 history not marked RUNTIME_PUBLISHED). No
consumer-live. No secrets. tick/quote/feed-health/instrument_catalog/D1 latest/M1-H4 untouched.
