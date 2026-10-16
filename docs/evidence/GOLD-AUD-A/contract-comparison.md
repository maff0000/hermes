# GOLD-AUD-A: documented-versus-actual contract comparison

Source phase. Sources: `maff0000/hermes` @ `ca1901789f8c7a7e085d02ebde8f7264d90d669e`, `maff0000/HELIOS` @ `7f7193d55475e40583892b94dd461b1b64d6f1ef`.

Classification vocabulary (PID Scope 9): MATCH, DIVERGENT, UNDOCUMENTED (live, no repo document), DOCUMENTED-NOT-LIVE. In this phase the **pre-classification** compares the repository documents with the repository source. The final classification needs the production observation, so the column `observed (PROD)` reads `PENDING HELM PROTOCOL` on every row and the column `final class` is left pending. Exactly one row per documented key and per source family.

## 1. Per-family table

| ID | Key pattern | Type (source) | Source reference | TTL (source) | Documented in repo? | Doc reference | Pre-classification (doc vs source) | observed (PROD) | final class |
|---|---|---|---|---|---|---|---|---|---|
| F01 | `hermes:ticks:XAU_USD:latest:v1` | string (JSON envelope) | `utils/tick_live_emitter_v1.py:L183; utils/tick_contract_v1.py:L26-27` | EX 10 s; ttl_seconds 5 | YES: registered, status DESIGNED (publisher not built) | `docs/contracts/hermes_redis_contract_manifest.md:L11; docs/contracts/hermes_redis_tick_contract_v1.md` | DIVERGENT (document says publisher not built; source has a gated per-tick emitter; TTL/EX/freshness thresholds MATCH) | PENDING HELM PROTOCOL | PENDING |
| F02 | `hermes:ticks:latest:v1` | string (JSON catalog) | `utils/tick_contract_v1.py:L128 (builder only; no runtime caller found)` | would be EX 10 s | YES: registered, DESIGNED (publisher not built) | `docs/contracts/hermes_redis_contract_manifest.md:L12` | MATCH (both say no publisher; source has builder only) | PENDING HELM PROTOCOL | PENDING |
| F03 | `hermes:candles:XAU_USD:{M1|M5|M15|H1|H4|D1}:latest:v1` | string (JSON envelope) | `utils/candle_contract_v1.py:L66-68; utils/candle_publisher_v1.py:L266-293` | EX = timeframe + buffer (M1 90, M5 360, M15 1080, H1 3900, H4 15300, D1 90000 s) | FAMILY NAME ONLY (core latest+history M1/M5/M15/H1; H4/D1 for XAU_USD); no key or schema registry | `docs/architecture/environment-model.md:L48-51; docs/config/hermes-configuration-reference.md:L38-53` | UNDOCUMENTED-IN-REPO (key pattern and schema are in code and a config note only) | PENDING HELM PROTOCOL | PENDING |
| F04 | `hermes:candles:XAU_USD:{M1|M5|M15|H1|H4}:history:v1:{open_epoch} and :history:v1:index` | string per bar; sorted set index | `utils/candle_history_v1.py:L129-139; utils/candle_history_forward_writer_v1.py:L202` | per-bar EX = configured retention days (H4: 120 d cap, 250-bar count trim) | FAMILY NAME ONLY | `docs/config/hermes-configuration-reference.md:L38-53` | UNDOCUMENTED-IN-REPO | PENDING HELM PROTOCOL | PENDING |
| F04b | `hermes:candles:XAU_USD:D1:history:v1:{open_epoch} and :history:v1:index` | string per bar; sorted set index | `utils/candle_d1_history_v1.py:L41-46; utils/candle_d1_history_seed_backfill_v1.py:L277` | EX 120 d cap; keeps 35 bars | NO | - | UNDOCUMENTED-IN-REPO | PENDING HELM PROTOCOL | PENDING |
| F05 | `hermes:indicators:XAU_USD:{M1|M5|M15|H1|H4|D1}:v1` | string (JSON payload, not the universal envelope) | `utils/hermes_indicators_v1.py:L115-147; utils/hermes_runtime_publisher_steps_v1.py:L612-641` | none (persistent) | NOT REGISTERED; manifest says indicators are 'separately designed (different WOs)' | `docs/contracts/hermes_redis_contract_manifest.md:L35-37` | UNDOCUMENTED-IN-REPO | PENDING HELM PROTOCOL | PENDING |
| F06 | `hermes:indicators:XAU_USD:{M1|M5|M15|H1|H4}:history:v1:{open_epoch} and :history:v1:index (absent at the documented production revision)` | string; sorted set | `utils/indicator_history_v1.py:L56-67; utils/hermes_runtime_publisher_steps_v1.py:L562-609` | as F04 | NO | - | UNDOCUMENTED-IN-REPO | PENDING HELM PROTOCOL | PENDING |
| F07 | `hermes:candle_features:XAU_USD:{M1|M5|M15|H1|H4|D1}:v1` | string (JSON payload) | `utils/hermes_candle_features_v1.py:L80-114; utils/hermes_runtime_publisher_steps_v1.py:L686-722` | none | NOT REGISTERED (manifest: separately designed) | `docs/contracts/hermes_redis_contract_manifest.md:L35-37` | UNDOCUMENTED-IN-REPO | PENDING HELM PROTOCOL | PENDING |
| F08 | `hermes:sessions:XAU_USD:v1` | string | `utils/hermes_sessions_v1.py:L66; utils/hermes_runtime_publisher_steps_v1.py:L800-819` | none | FAMILY NAME ONLY ('sessions') | `docs/architecture/environment-model.md:L49-51` | UNDOCUMENTED-IN-REPO | PENDING HELM PROTOCOL | PENDING |
| F09 | `hermes:levels:XAU_USD:{session|intraday|daily}:v1` | string | `utils/hermes_levels_v1.py:L98; utils/hermes_runtime_publisher_steps_v1.py:L826-855` | none | FAMILY NAME ONLY ('levels') | `docs/architecture/environment-model.md:L49-51` | UNDOCUMENTED-IN-REPO | PENDING HELM PROTOCOL | PENDING |
| F10 | `hermes:quote:XAU_USD:v1` | string | `utils/hermes_quote_tick_contract_v1.py:L59; utils/hermes_runtime_publisher_steps_v1.py:L948-970` | EX 15 s | FAMILY NAME ONLY ('quote/tick') | `docs/architecture/environment-model.md:L49-51` | UNDOCUMENTED-IN-REPO | PENDING HELM PROTOCOL | PENDING |
| F11 | `hermes:feed_health:XAU_USD:v1` | string | `utils/hermes_feed_health_v1.py:L60; utils/hermes_runtime_publisher_steps_v1.py:L911-939` | EX 180 s | NO (manifest builder lists feed_health as INVENTORY_PENDING) | `utils/hermes_control_plane_v1.py:L189` | UNDOCUMENTED-IN-REPO | PENDING HELM PROTOCOL | PENDING |
| F12 | `hermes:instrument_catalog:XAU_USD:v1` | string | `utils/hermes_instrument_catalog_v1.py:L43; utils/hermes_runtime_publisher_steps_v1.py:L880-901` | EX 300 s | NO | - | UNDOCUMENTED-IN-REPO | PENDING HELM PROTOCOL | PENDING |
| F13 | `hermes:gaps:XAU_USD:v1` | string | `utils/hermes_gaps_v1.py:L409` | none | FAMILY NAME ONLY ('gaps') | `docs/architecture/environment-model.md:L49-51` | UNDOCUMENTED-IN-REPO | PENDING HELM PROTOCOL | PENDING |
| F14 | `hermes:backfill:status:XAU_USD:v1` | string | `utils/hermes_backfill_status_v1.py:L278` | none | NO | - | UNDOCUMENTED-IN-REPO | PENDING HELM PROTOCOL | PENDING |
| F15 | `hermes:contract:manifest:v1` | string | `utils/hermes_control_plane_v1.py:L22; utils/hermes_runtime_publisher_steps_v1.py:L423` | none | NO | - | UNDOCUMENTED-IN-REPO (and its own status table is stale in places, see AUDIT section 9) | PENDING HELM PROTOCOL | PENDING |
| F16 | `hermes:catalog:candles:v1` | string | `utils/hermes_control_plane_v1.py:L24; utils/hermes_runtime_publisher_steps_v1.py:L424` | none | NO | - | UNDOCUMENTED-IN-REPO | PENDING HELM PROTOCOL | PENDING |
| F17 | `hermes:health:v1` | string | `utils/hermes_control_plane_v1.py:L25; utils/hermes_runtime_publisher_steps_v1.py:L425` | none | NO | - | UNDOCUMENTED-IN-REPO | PENDING HELM PROTOCOL | PENDING |
| F18 | `hermes:publisher:heartbeat:v1` | string | `utils/hermes_control_plane_v1.py:L23, L67-68; utils/hermes_runtime_publisher_steps_v1.py:L426` | EX 180 s | NO | - | UNDOCUMENTED-IN-REPO | PENDING HELM PROTOCOL | PENDING |
| L01 | `{prefix}signals:latest:{instrument}` | hash (53 text fields) | `signal_builder.py:L902-972; main.py:L909-910` | EX 600 s | FAMILY NAME ONLY ('signal/indicator snapshot') | `docs/architecture/environment-model.md:L48-49; utils/hermes_control_plane_v1.py:L193-197 (FROZEN_PENDING_CONSUMER_CUTOVER)` | UNDOCUMENTED-IN-REPO (legacy) | PENDING HELM PROTOCOL | PENDING |
| L02 | `{prefix}signals:price:{instrument}` | hash | `utils/redis_publisher.py:L143-154; main.py:L856` | EX 300 s | FAMILY NAME ONLY ('current price') | `docs/architecture/environment-model.md:L48` | UNDOCUMENTED-IN-REPO (legacy) | PENDING HELM PROTOCOL | PENDING |
| L03 | `{prefix}signals:candle:{TF}:{instrument}:latest` | hash | `utils/redis_publisher.py:L189-200 (no caller found)` | EX 3600 s | NO; governed candle contract forbids it as governed output | `utils/candle_contract_v1.py:L8-9` | UNDOCUMENTED candidate (code only, no caller found; live presence UNVERIFIED) | PENDING HELM PROTOCOL | PENDING |
| L04 | `{prefix}signals:health:status` | hash | `utils/redis_publisher.py:L231-236 (no caller found)` | EX 60 s | NO | - | UNDOCUMENTED candidate (code only, no caller found; live presence UNVERIFIED) | PENDING HELM PROTOCOL | PENDING |
| L05 | `{prefix}market_map:{instrument}` | hash | `services/market-map/market_map.py:L313-331` | EX 600 s | NO; control plane marks FROZEN_PENDING_CONSUMER_CUTOVER | `utils/hermes_control_plane_v1.py:L193-197` | UNDOCUMENTED-IN-REPO (legacy) | PENDING HELM PROTOCOL | PENDING |
| L06 | `{prefix}instrument:{instrument}` | hash | `utils/redis_publisher.py:L268-330` | not examined | NO; control plane marks LEGACY_OR_PARTIAL_CATALOG | `utils/hermes_control_plane_v1.py:L196` | UNDOCUMENTED-IN-REPO (legacy) | PENDING HELM PROTOCOL | PENDING |
| L07 | `pub/sub channels signals:tick:*, signals:stream:*` | not keys | `utils/redis_publisher.py:L135; signal_builder.py:L975` | n/a | NO | - | UNDOCUMENTED candidate; not observable under the allow-list, so UNVERIFIED by design | PENDING HELM PROTOCOL | PENDING |
| L08 | `hermes:ticks:{instrument} (stream) and hermes:ticks:latest:{instrument}` | stream; string | `utils/tick_stream_publisher.py:L14-16 (no runtime caller found)` | configured | NO | - | UNDOCUMENTED candidate (code only, no runtime caller found; live presence UNVERIFIED) | PENDING HELM PROTOCOL | PENDING |
| I01 | `hermes:candle_features:latest:v1:{instrument}:{timeframe}; hermes:candle_context:current:v1:{instrument}; hermes:indicators:latest:v1:{instrument}:{timeframe}` | string | `utils/candle_payload_builders.py:L61, L115, L143 (no runtime caller found)` | n/a | MENTIONED as 'separately designed' | `docs/contracts/hermes_redis_contract_manifest.md:L35-37` | DOCUMENTED-NOT-LIVE candidate (inert builders only) | PENDING HELM PROTOCOL | PENDING |
| S01 | `hermes:shadow:*` | string | `utils/candle_publisher_v1.py:L23; utils/tick_live_emitter_v1.py (SHADOW_PREFIX)` | EX as canonical | dev-only; production posture fail-closed | `docs/config/hermes-configuration-reference.md:L26-28` | DOCUMENTED-NOT-LIVE candidate (dev-only namespace; presence in production would be DIVERGENT) | PENDING HELM PROTOCOL | PENDING |

## 2. Field-level comparison of the two registered contracts

Documents: `docs/contracts/hermes_redis_contract_manifest.md` (manifest) and `docs/contracts/hermes_redis_tick_contract_v1.md` (design). Source: `utils/tick_contract_v1.py`.

| Attribute | Documented | Source | Pre-classification | observed (PROD) |
|---|---|---|---|---|
| Per-instrument key | `hermes:ticks:{instrument}:latest:v1` | `utils/tick_contract_v1.py:L43-46` | MATCH | PENDING HELM PROTOCOL |
| Aggregate key | `hermes:ticks:latest:v1` | `utils/tick_contract_v1.py:L40`, L128 | MATCH | PENDING HELM PROTOCOL |
| Value type | string, JSON envelope | `utils/tick_live_emitter_v1.py:L183` (`json.dumps`) | MATCH | PENDING HELM PROTOCOL |
| Envelope ttl_seconds | 5 | `utils/tick_contract_v1.py:L26` | MATCH | PENDING HELM PROTOCOL |
| Redis EX | 10 | `utils/tick_contract_v1.py:L27`; `utils/tick_live_emitter_v1.py:L183` | MATCH | PENDING HELM PROTOCOL |
| Freshness thresholds | FRESH up to 5 s, DEGRADED up to 15 s, STALE beyond 15 s | `utils/tick_contract_v1.py:L28-29`, L61-72 | MATCH | PENDING HELM PROTOCOL |
| freshness_state values | FRESH, DEGRADED, STALE, UNAVAILABLE | `utils/tick_contract_v1.py:L34` | MATCH | PENDING HELM PROTOCOL |
| status values | manifest names a status field but not its values | `utils/tick_contract_v1.py:L35`: OK, WARN, BLOCK, ERROR, UNAVAILABLE | UNDOCUMENTED (vocabulary) | PENDING HELM PROTOCOL |
| seq | null in v1 | `utils/tick_contract_v1.py:L103` builder sets `seq` None; validator rejects non-null (L199-200) | MATCH | PENDING HELM PROTOCOL |
| derivation | manifest lists `derivation` among envelope fields (`docs/contracts/hermes_redis_contract_manifest.md:L20`) | held inside `provenance.derivation` (check at `utils/tick_contract_v1.py` GOV-TICK-CONTRACT-012, constant L30); not a top-level field; `_ENVELOPE_KEYS` (L160-163) has no top-level derivation | DIVERGENT (manifest envelope list vs source; the tick design document nests it correctly) | PENDING HELM PROTOCOL |
| Publisher status | DESIGNED (publisher not built) | gated per-tick emitter exists (`utils/tick_live_emitter_v1.py`) | DIVERGENT (document stale: manifest last changed 2026-06-16; emitter first added 2026-07-07) | PENDING HELM PROTOCOL |
| Forbidden interpretive fields | none carried | `utils/tick_contract_v1.py:L37-39`, L217 forbidden term scan | MATCH | PENDING HELM PROTOCOL |
| Tick fields in `data` | design document table | `utils/tick_contract_v1.py:L74-105`: instrument, received_at_utc, bid, ask, mid, spread, source, seq, contract_version | MATCH to be confirmed field by field against the design document during the production phase; no divergence found by reading | PENDING HELM PROTOCOL |

## 2a. Revision sensitivity

The repository documents the production image as older than the audited revision. At the code level the only family whose presence changes between the two is F06 (indicator history), which is absent at the older revision; the other cited modules are unchanged (see `AUDIT-2026-10-16.md` section 0.5). The final classification of every row is made against the deployed revision read from the production manifest, not against `ca19017`.

## 3. Timestamp discipline per documented or source family

The four times are kept separate (PID Scope 4): bar open, publish, valid-until, observer receive. See `AUDIT-2026-10-16.md` section 4 for the field names per family. The observer receive time is supplied by the protocol client and is `PENDING HELM PROTOCOL`.

## 4. Counts

Source families: 29 rows (19 governed incl. F04b, 8 legacy, 1 inert, 1 shadow). Registered documented keys: 2 (F01, F02). Every row requires a defect entry when the final class is DIVERGENT or UNDOCUMENTED (`defects.md` pre-allocates entries for the source-derivable ones; production-dependent entries are PENDING).

