# 10 — No-writes / no-backfill / D1 gates preserved

## No writes / no backfill (tests)
- `test_warmstart_reads_redis_h4_history_no_writes` — `client.sets == []` after a full warm-start.
- `test_warmstart_then_live_rollover_publishes_genuine_seal` — hydration writes nothing; the single write is the
  later LIVE roll-over seal (a genuine governed publish), not hydration.
- Module scan `test_no_redis_sql_socket_thread_at_import` — no `redis`/`pymysql`/`socket`/`threading` import, no
  client constructed at import; re-import is side-effect-free.
- Not a backfill engine: hydration seeds ONLY the current unsealed block; no historical write, no synthesis.

## D1 gates remain (unchanged by this WO)
- **D1 latest stays AMBER** until a genuine live seal — hydration never publishes (`d1_published_by_hydration:false`,
  `d1_remains_gated_amber:true`). Test `test_d1_gates_remain_amber_after_hydration`,
  `test_complete_six_ready_but_not_published_by_hydration`.
- **D1 history blocked**, **D1 indicators gated**, **D1 candle_features gated**, **D1 levels/daily/weekly gated** —
  none are touched; `LATEST_TFS` (durable supervisor) still excludes D1; `HERMES_LEVEL_D1_AUTHORISED` etc. unchanged.

## D1 policy preserved (tests)
- `test_fixed_2200_anchor_no_dst` — winter + summer `now` both bucket to 22:00:00 UTC; `assert_d1_open_anchor` holds.
- `test_d1_policy_markers_preserved` — source default `redis_h4_history`, derivation timeframe H4, H4 history index
  key shape; non-22:00 open rejected. No direct `candles_D1`; no 24×H1 shortcut (source is H4-only).
- `test_non_h4_and_non_xau_rejected` — non-H4 + non-XAU children rejected; XAU_USD only.

## Full regression
`08_tests.log`: **113 passed** (20 new hydration + 93 existing D1/H4 derivation, publish-wire, seal-hook). Producer
edits (added `hydrate` + classifier + metrics) broke none of the existing D1/H4 behaviour.
