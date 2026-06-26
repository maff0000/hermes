# Final Report — WO-HELM-HERMES-GOLD-MTF-CANONICAL-WRITER-AND-WIRE-0001

**Persona:** HELM · **Lane:** HERMES · **Type:** code-only, PR-gated
**Verdict sought:** `GREEN_PR_OPEN_GOLD_MTF_CANONICAL_WRITER_AND_WIRE_CODE_ONLY`

## Outcome
Built the missing HERMES canonical candle writer + governed sink/config + canonical seam, confirmed the
existing live wiring already routes completed candles to it, and added M15 production to the aggregator —
all **disabled by default**. No deploy, no activation, no live Redis/SQL.

## Scope delivered
1. **Canonical writer** — `SerializingCandleCanonicalWriter` writes only versioned
   `hermes:candles:{instr}:{M1|M5|M15|H1}:latest:v1` keys, validates via the PR#47 governed contract,
   sets TTL per contract (`redis_ex_seconds`), fails loud on validation/invalid-sink/missing-config/
   unsupported-tf/alias/forbidden fields, and propagates Redis errors. H4/D1/D, unversioned keys, and
   `XAUUSD` alias keys are rejected (`GOV-CANDLE-PUB-CANON-KEY-001..005`, `-WR-001..003`).
2. **Sink/config** — canonical selected only via explicit governed env (`SINK=canonical` +
   `PUBLISH_ENABLED` + `PUBLISH_AUTHORISED` + explicit bus host/port/db). Missing config → fail loud.
   Disabled/no-op path preserved; shadow path untouched and not required; canonical can't be selected
   accidentally.
3. **Live wiring** — already present in `main.py` (emit per completed candle, emitter from seam factory);
   default disabled. `signal_builder.py`/`main.py` unchanged. A `signal_builder.Candle` publishes a
   canonical key through the seam (`test_live_candle_object_flows_through_seam`).
4. **M15 production** — `Timeframe.M15` + 15-min truncation in `models/candle.py`; live
   `signal_builder` aggregator already M15-capable (config-driven `CANDLE_TIMEFRAMES`). D1 still internal,
   never published; no H4 fan-out.
5. **Contract invariants** — payload carries open/high/low/close/volume + body/range/wick geometry +
   `candle_direction` + freshness/gap/provenance + valid_until/TTL + source counts; geometry recomputed;
   `wick_high=high-body_high`, `wick_low=body_low-low` (sizes, never aliases). Validated before every write.

## Tests — 194 passed (focused candle/model/canonical suites)
New `tests/test_candle_canonical_writer_and_wire_v1.py` (20 cases) proves: versioned-canonical-only writes;
disabled writes nothing; canonical requires explicit governed config; invalid sink fails loud; validation
failure prevents write; Redis failure propagates (writer) and is observable (seam metric); TTL correct;
M1/M5/M15/H1 supported; H4/D1 not published; XAUUSD→XAU_USD with no dual-publish; unversioned rejected;
regime rejected; wick aliasing fails; live-candle flows through seam; disabled vs enabled emit; M15
aggregation/flush in both aggregator paths with no extra D1/H4; naive/aware UTC safe.
Three existing tests updated (canonical is now a governed path; `flush_all` now 5 incl. M15).

## Risks / notes for R2D2
- **Two aggregators exist.** `models/candle.py` (enum, `flush_all`) and `signal_builder.CandleAggregator`
  (string-TF, the live path). M15 added to the former in code; the latter is config-driven (already
  M15-capable). The canonical seam consumes either candle shape via `_tf_name`.
- **`live`/`prod` sinks** are now explicitly rejected (only `canonical` selects the writer) — a deliberate
  narrowing vs the old blanket `CANONICAL_SINKS` fail-loud; the canonical capability is dev-targeted.
- **Pre-existing red tests** (`test_watchdog*` async — no pytest-asyncio; `test_config`/`env_compliance`/
  `gap_scanner`/`per_instrument_health`/`recovery_library`/`collection_contract`/`test_api`/
  `test_canonical_engine`/`test_m1_deriver`/`test_redis_publisher` — need live DB/env) fail identically on
  base `99c72e3`; none in this lane.
- **Activation is still a separate WO.** This PR only builds the capability; canonical stays dark.

## Disposition
Code green, evidence complete, fabric build keys written, PR opened. No deploy/restart/activation occurred.
Requesting **`GREEN_PR_OPEN_GOLD_MTF_CANONICAL_WRITER_AND_WIRE_CODE_ONLY`**.
