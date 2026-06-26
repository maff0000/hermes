# Final Report — WO-HELM-HERMES-GOLD-MTF-CANDLE-CONTRACT-EXTEND-0001

**Persona:** HELM · **Lane:** HERMES · **Type:** code-build-only, shadow-first
**Verdict sought:** `GREEN_PR_OPEN_GOLD_MTF_CONTRACT_EXTEND_SHADOW_FIRST`

1. **WO identity.** GOLD MTF Candle Contract Extension — extend the governed HERMES candle contract +
   dev-shadow seam to the DIRECT-NATIVE grid M1/M5/M15/H1 and fix wick semantics. Shadow-first; canonical dark.

2. **Branch / worktree / base.** Branch `wo/WO-HELM-HERMES-GOLD-MTF-CANDLE-CONTRACT-EXTEND-0001` in
   worktree `/srv/trading/hermes-worktrees/wo-gold-mtf`, off `origin/main` `af2679b`. One WO = one branch =
   one worktree = one PR.

3. **Timeframe grid.** Added M1 (60s) and M15 (900s) to `TIMEFRAMES`, `TF_SECONDS`, `TTL_BUFFER_SECONDS`.
   Grid: M1=90s EX, M5=360s, M15=1080s, H1=3900s. (`03_timeframe_grid.md`)

4. **Direct-native semantics.** All grid TFs build with `source_timeframe=timeframe`, `source_count=1`,
   `expected_source_count=1`, `source_coverage=1.0`, `derivation=DIRECT_FROM_SOURCE`,
   `derivation_policy=NONE_DIRECT`, `source_policy_epoch=DIRECT_NATIVE_V1`. (`06_sample_payloads.json`)

5. **Wick semantics fixed.** `body_high=max(o,c)`, `body_low=min(o,c)`, `body_size=abs(c-o)`,
   `range_size=high-low`, `wick_high=high-body_high`, `wick_low=body_low-low`. (`04_wick_semantics_proof.txt`)

6. **No high/low aliasing.** `wick_high` is the upper-wick **size**, never the high price; `wick_low` the
   lower-wick size, never the low. Validator recomputes both and rejects aliasing with
   `GOV-CANDLE-CONTRACT-035`. Proof: `test_wick_high_is_not_the_high_price`, `07_validation_failloud_matrix.txt`.

7. **upper/lower_wick_size mapping honoured.** Repo's `candle_features.candle_geometry` already exposes
   `upper_wick_size`/`lower_wick_size` with the correct math; the contract's `wick_high`/`wick_low` equal
   those sizes (computed inline). The redundant price-level alias in `candle_features` is left untouched so
   its tests stay green.

8. **Geometry validation.** New `GOV-CANDLE-CONTRACT-030..038`: presence, non-negativity, body-within-range,
   body≤range, body/wick recomputation, range recomputation, direction value + sign consistency.

9. **candle_direction.** Pure sign of `close-open` → `UP`/`DOWN`/`FLAT`. Deterministic, no thresholds, no
   config. Avoids implying the config-governed classification. (`05_geometry_and_direction.md`)

10. **Unpriced safety.** On `NO_SOURCE_DATA`/`MARKET_CLOSED` all seven geometry fields are `None` together;
    validator skips geometry checks. Proof: `test_market_closed_geometry_is_none_and_valid`.

11. **Regime exclusion (Option A).** No `regime`/`regime_confidence`; HERMES carries deterministic
    candle-state only. Forbidden-token scan (`GOV-CANDLE-CONTRACT-029`) blocks any `regime` field.
    (`10_regime_exclusion.md`)

12. **Instrument identity.** Only canonical `XAU_USD` is published. One-way alias map `XAUUSD→XAU_USD`;
    no dual-publish. Proof: `test_xauusd_alias_canonicalised_no_dual_publish`. (`08_instrument_canonicalisation.md`)

13. **Shadow key topology.** `hermes:shadow:candles:{instrument}:{M1|M5|M15|H1}:latest:v1` — every key
    carries `:v1`; unversioned rejected by `assert_shadow_key`. (`09_shadow_key_topology.md`)

14. **Seam extension.** `SUPPORTED_TF = ("M1","M5","M15","H1")`. D1/H4/D skipped `UNSUPPORTED_TIMEFRAME`
    (counter + rate-limited log), never remapped, never derived from stale SQL.

15. **Canonical stays DARK.** No canonical writer wired; `signals:candle:*` still blocked by
    `GOV-CANDLE-CONTRACT-028`. (`11_no_activation_proof.md`)

16. **No activation.** `HERMES_CANDLE_FORWARD_ENABLED` unchanged (`false`); no live `.env`, no Redis, no SQL,
    no backfill, no deploy/restart, no container/service touch. `shadow_authorised` only in test fixtures.

17. **Live aggregator untouched.** `models/candle.py` not in the diff; `CandleAggregator` still produces
    M1/M5/H1/D1. M15 production deliberately deferred to a future activation WO. (`14_deferred_scope.md`)

18. **Tests.** New `tests/test_candle_gold_mtf_contract_v1.py` (17 cases) + 3 existing files updated.
    Full run: **172 passed**, 0 failures. (`12_test_results.txt`, `13_test_inventory.md`)

19. **Pre-existing failures excluded.** The repo's `test_watchdog*` (needs pytest-asyncio) and
    `test_api/test_canonical_engine/test_m1_deriver/test_redis_publisher` (need live DB/REDIS env) fail
    identically on the base `af2679b` with my changes stashed — not introduced here.

20. **Lane & safety.** All changed paths are HERMES candle contract/seam/config/tests/evidence
    (`git diff --name-only`). No Falcon/SOLO/NEO/ARES/HELIOS/Proteus touch. No shared-code import. All
    timestamps UTC (ms).

21. **Deferred, recorded.** M15 aggregator production, H4 derivation, D anchor, canonical publish,
    `wick_profile`/`range_state`/`volatility_state`, shadow activation, backfill — each documented, none
    silently dropped. (`14_deferred_scope.md`)

22. **Disposition.** Code green, evidence complete, fabric build keys written, PR opened. Requesting R2D2
    verdict **`GREEN_PR_OPEN_GOLD_MTF_CONTRACT_EXTEND_SHADOW_FIRST`**.
