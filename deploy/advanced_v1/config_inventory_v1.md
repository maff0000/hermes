# HERMES deployment runtime-configuration INVENTORY & CLASSIFICATION (v1)

WO-HELM-HERMES-COMPLETE-TRACKED-RUNTIME-CONFIG-AND-XAU-PARITY-0001. Secret-free. Generated from
`runtime_config_schema_v1.py` (the single source of truth). One record per deployment-contract field.

Classification categories: **A** tracked non-secret control · **B** external secret/authority · **C** host/runtime
parameter · **D** explicit default · **E** obsolete (retired, evidence-backed) · **F** rejected cross-application.

| Canonical name | Class | Type | Parity? | Host? | Group | Effective (non-secret) | Aliases | Consequence if omitted |
|---|---|---|---|---|---|---|---|---|
| `HERMES_BACKFILL_STATUS_PUBLISH_AUTHORISED` | D | bool | Y |  | adv1_families | true | - | publisher unauthorised -> XAU output regression |
| `HERMES_BACKFILL_STATUS_PUBLISH_ENABLED` | D | bool | Y |  | adv1_families | true | - | publisher disabled -> XAU output regression |
| `HERMES_FEED_HEALTH_PUBLISH_AUTHORISED` | D | bool | Y |  | adv1_families | true | - | publisher unauthorised -> XAU output regression |
| `HERMES_FEED_HEALTH_PUBLISH_ENABLED` | D | bool | Y |  | adv1_families | true | - | publisher disabled -> XAU output regression |
| `HERMES_GAPS_PUBLISH_AUTHORISED` | D | bool | Y |  | adv1_families | true | - | publisher unauthorised -> XAU output regression |
| `HERMES_GAPS_PUBLISH_ENABLED` | D | bool | Y |  | adv1_families | true | - | publisher disabled -> XAU output regression |
| `HERMES_INDICATOR_PUBLISH_AUTHORISED` | D | bool | Y |  | adv1_families | true | - | publisher unauthorised -> XAU output regression |
| `HERMES_INDICATOR_PUBLISH_ENABLED` | D | bool | Y |  | adv1_families | true | - | publisher disabled -> XAU output regression |
| `HERMES_TICK_PUBLISH_AUTHORISED` | D | bool | Y |  | adv1_families | true | - | publisher unauthorised -> XAU output regression |
| `HERMES_TICK_PUBLISH_ENABLED` | D | bool | Y |  | adv1_families | true | - | publisher disabled -> XAU output regression |
| `HERMES_CANDLE_CANONICAL_ACTIVATION_APPROVAL` | B | string | Y |  | canonical_redis | <secret/ext> | - | canonical activation refused -> candle canonical publish disabled |
| `HERMES_CANDLE_CANONICAL_INSTRUMENTS` | D | csv | Y |  | canonical_redis | XAU_USD | - | canonical scope change |
| `HERMES_CANDLE_CANONICAL_REDIS_DB` | D | int | Y |  | canonical_redis | 0 | - | canonical publish wrong DB |
| `HERMES_CANDLE_CANONICAL_REDIS_HOST` | C | string | Y | Y | canonical_redis | 192.168.11.10 | - | canonical candle publish misrouted/broken |
| `HERMES_CANDLE_CANONICAL_REDIS_PORT` | C | int | Y | Y | canonical_redis | 6379 | - | canonical publish wrong port |
| `DB_HOST` | C | string | Y | Y | db | 192.168.11.10 | DEV_DB_HOST | DB unreachable -> boot-gate fail |
| `DB_NAME` | A | string | Y |  | db | tradingSignals | - | wrong schema / boot-gate fail |
| `DB_PASSWORD` | B | string |  |  | db | <secret/ext> | DEV_DB_PASSWORD | auth fail |
| `DB_PORT` | C | int | Y | Y | db | 3307 | DEV_DB_PORT | silent 3306 default -> DB unreachable -> restart loop |
| `DB_USER` | A | string |  |  | db | <external> | DEV_DB_USER | auth fail |
| `HERMES_ADVANCED_V1_MASTER_ENABLED` | A | bool | Y |  | expansion_dark | false | - | expansion master ON -> seven-new publication risk |
| `HERMES_ADVANCED_V1_PUBLISHER_MODE` | A | enum | Y |  | expansion_dark | DISABLED | - | publisher SHADOW/ACTIVE -> expansion publication |
| `BUILD_UTC` | A | iso8601 |  |  | identity | <external> | - | build identity invalid |
| `HERMES_IMAGE_REF` | A | string |  |  | identity | <external> | - | mutable/unpinned image; non-reproducible runtime |
| `HERMES_IMAGE_TAG` | A | string |  |  | identity | <external> | - | image tag unresolved |
| `HERMES_REQUIRE_DIGEST_PIN` | D | bool |  |  | identity | true | - | mutable-tag deploy permitted |
| `SOURCE_SHA` | A | sha40 |  |  | identity | <external> | - | UNKNOWN provenance; unpromotable candidate |
| `OANDA_ACCOUNT_ID` | B | string |  |  | oanda | <secret/ext> | - | no market data |
| `OANDA_API_KEY` | B | string |  |  | oanda | <secret/ext> | - | no market data |
| `OANDA_ENVIRONMENT` | A | enum | Y |  | oanda | live | - | wrong OANDA endpoint |
| `HERMES_PUBLISHER_RUNTIME_OWNER` | D | enum | Y |  | process | in_process | - | XAU parity-critical control; omission/change regresses the authorised XAU runtime |
| `REDIS_DB` | D | int | Y |  | redis | 0 | DEV_REDIS_DB | wrong Redis DB -> split-brain |
| `REDIS_HOST` | C | string | Y | Y | redis | 192.168.11.10 | DEV_REDIS_HOST | publication/routing to wrong Redis |
| `REDIS_KEY_PREFIX` | D | string | Y |  | redis | hermes: | DEV_REDIS_KEY_PREFIX | key namespace drift |
| `REDIS_PASSWORD` | B | string |  |  | redis | <secret/ext> | DEV_REDIS_PASSWORD | auth fail if required |
| `REDIS_PORT` | C | int | Y | Y | redis | 6379 | DEV_REDIS_PORT | wrong Redis port |
| `ENVIRONMENT` | A | enum | Y |  | runtime | DEV | - | wrong env prefix resolution (DEV_* mapping) |
| `RUN_ENV` | D | enum | Y |  | runtime | STAGING | - | purge/backfill inertness changes |
| `SIGNAL_HOST` | D | string |  |  | runtime | 0.0.0.0 | - | - |
| `SIGNAL_PORT` | D | int |  |  | runtime | 8211 | - | HTTP surface unreachable / healthcheck fail |
| `CONSUMER_LIVE` | A | bool |  |  | safety | false | - | consumer enabled -> boundary breach |
| `HERMES_BACKFILL_EXECUTION_ENABLED` | A | bool |  |  | safety | false | - | prohibited backfill executor enabled |
| `INSTRUMENTS` | D | csv | Y |  | stream | XAU_USD,XAG_USD,XPT_USD,XCU_USD,GBP_USD,EUR_USD,USD_JPY,AUD_USD,NZD_USD,USD_CAD,USD_CHF,EUR_GBP,WTICO_USD,SPX500_USD | - | OANDA subscription scope change (must remain the 14-instrument set) |
| `HERMES_CANDLE_D1_HISTORY_AUTHORISED` | D | bool | Y |  | xau_operational | true | - | publisher unauthorised -> XAU output regression |
| `HERMES_CANDLE_D1_HISTORY_ENABLED` | D | bool | Y |  | xau_operational | true | - | publisher disabled -> XAU output regression |
| `HERMES_CANDLE_D1_INSTRUMENTS` | D | csv | Y |  | xau_operational | XAU_USD | - | D1 scope change |
| `HERMES_CANDLE_D1_PUBLISH_AUTHORISED` | D | bool | Y |  | xau_operational | true | - | publisher unauthorised -> XAU output regression |
| `HERMES_CANDLE_D1_PUBLISH_ENABLED` | D | bool | Y |  | xau_operational | true | - | publisher disabled -> XAU output regression |
| `HERMES_CANDLE_D1_SOURCE_TIMEFRAME` | D | enum | Y |  | xau_operational | H4 | - | D1 source tf change |
| `HERMES_CANDLE_FEATURE_D1_AUTHORISED` | D | bool | Y |  | xau_operational | true | - | XAU parity-critical control; omission/change regresses the authorised XAU runtime |
| `HERMES_CANDLE_FEATURE_PUBLISH_AUTHORISED` | D | bool | Y |  | xau_operational | true | - | publisher unauthorised -> XAU output regression |
| `HERMES_CANDLE_FEATURE_PUBLISH_ENABLED` | D | bool | Y |  | xau_operational | true | - | publisher disabled -> XAU output regression |
| `HERMES_CANDLE_FEATURE_PUBLISH_INSTRUMENTS` | D | csv | Y |  | xau_operational | XAU_USD | - | XAU parity-critical control; omission/change regresses the authorised XAU runtime |
| `HERMES_CANDLE_FEATURE_PUBLISH_TIMEFRAMES` | D | csv | Y |  | xau_operational | M1,M5,M15,H1,H4,D1 | - | feature timeframe change |
| `HERMES_CANDLE_FORWARD_ENABLED` | D | bool | Y |  | xau_operational | true | - | candle forwarding off -> XAU regression |
| `HERMES_CANDLE_FORWARD_SINK` | D | enum | Y |  | xau_operational | canonical | - | sink change |
| `HERMES_CANDLE_H4_PUBLISH_ENABLED` | D | bool | Y |  | xau_operational | true | - | H4 publish off |
| `HERMES_CANDLE_HISTORY_FORWARD_AUTHORISED` | D | bool | Y |  | xau_operational | true | - | publisher unauthorised -> XAU output regression |
| `HERMES_CANDLE_HISTORY_FORWARD_ENABLED` | D | bool | Y |  | xau_operational | true | - | publisher disabled -> XAU output regression |
| `HERMES_CANDLE_HISTORY_FORWARD_INSTRUMENTS` | D | csv | Y |  | xau_operational | XAU_USD | - | XAU parity-critical control; omission/change regresses the authorised XAU runtime |
| `HERMES_CANDLE_HISTORY_FORWARD_TIMEFRAMES` | D | csv | Y |  | xau_operational | M1,M5,M15,H1,H4 | - | history forward timeframe change |
| `HERMES_CANDLE_INSTRUMENTS_GROUP` | D | csv |  |  | xau_operational | XAU_USD | - | - |
| `HERMES_CANDLE_PUBLISH_AUTHORISED` | D | bool | Y |  | xau_operational | true | - | publisher unauthorised -> XAU output regression |
| `HERMES_CANDLE_PUBLISH_ENABLED` | D | bool | Y |  | xau_operational | true | - | publisher disabled -> XAU output regression |
| `HERMES_D1_HISTORY_BACKFILL_AUTHORISED` | D | bool | Y |  | xau_operational | true | - | publisher unauthorised -> XAU output regression |
| `HERMES_D1_HISTORY_BACKFILL_DRY_RUN` | D | bool | Y |  | xau_operational | false | - | history warm-start mode change |
| `HERMES_D1_HISTORY_BACKFILL_ENABLED` | D | bool | Y |  | xau_operational | true | - | publisher disabled -> XAU output regression |
| `HERMES_D1_HISTORY_BACKFILL_MAX_CANDLES` | D | int | Y |  | xau_operational | 60 | - | XAU parity-critical control; omission/change regresses the authorised XAU runtime |
| `HERMES_D1_HISTORY_BACKFILL_MIN_DEPTH` | D | int | Y |  | xau_operational | 26 | - | XAU parity-critical control; omission/change regresses the authorised XAU runtime |
| `HERMES_D1_WARMSTART_AUTHORISED` | D | bool | Y |  | xau_operational | true | - | publisher unauthorised -> XAU output regression |
| `HERMES_D1_WARMSTART_ENABLED` | D | bool | Y |  | xau_operational | true | - | publisher disabled -> XAU output regression |
| `HERMES_FEED_HEALTH_PUBLISH_INSTRUMENTS` | D | csv | Y |  | xau_operational | XAU_USD | - | XAU parity-critical control; omission/change regresses the authorised XAU runtime |
| `HERMES_H4_WARMSTART_AUTHORISED` | D | bool | Y |  | xau_operational | true | - | publisher unauthorised -> XAU output regression |
| `HERMES_H4_WARMSTART_ENABLED` | D | bool | Y |  | xau_operational | true | - | publisher disabled -> XAU output regression |
| `HERMES_INDICATOR_D1_AUTHORISED` | D | bool | Y |  | xau_operational | true | - | XAU parity-critical control; omission/change regresses the authorised XAU runtime |
| `HERMES_INDICATOR_PUBLISH_INSTRUMENTS` | D | csv | Y |  | xau_operational | XAU_USD | - | XAU parity-critical control; omission/change regresses the authorised XAU runtime |
| `HERMES_INDICATOR_PUBLISH_TIMEFRAMES` | D | csv | Y |  | xau_operational | M1,M5,M15,H1,H4,D1 | - | indicator timeframe change (M1..D1) |
| `HERMES_INSTRUMENT_CATALOG_PUBLISH_AUTHORISED` | D | bool | Y |  | xau_operational | true | - | publisher unauthorised -> XAU output regression |
| `HERMES_INSTRUMENT_CATALOG_PUBLISH_ENABLED` | D | bool | Y |  | xau_operational | true | - | publisher disabled -> XAU output regression |
| `HERMES_INSTRUMENT_CATALOG_PUBLISH_INSTRUMENTS` | D | csv | Y |  | xau_operational | XAU_USD | - | XAU parity-critical control; omission/change regresses the authorised XAU runtime |
| `HERMES_LEVEL_D1_AUTHORISED` | D | bool | Y |  | xau_operational | true | - | XAU parity-critical control; omission/change regresses the authorised XAU runtime |
| `HERMES_LEVEL_PUBLISH_AUTHORISED` | D | bool | Y |  | xau_operational | true | - | publisher unauthorised -> XAU output regression |
| `HERMES_LEVEL_PUBLISH_ENABLED` | D | bool | Y |  | xau_operational | true | - | publisher disabled -> XAU output regression |
| `HERMES_LEVEL_PUBLISH_INSTRUMENTS` | D | csv | Y |  | xau_operational | XAU_USD | - | XAU parity-critical control; omission/change regresses the authorised XAU runtime |
| `HERMES_LEVEL_PUBLISH_SCOPES` | D | csv | Y |  | xau_operational | session,intraday,daily | - | level scope change |
| `HERMES_PUBLISHER_RUNTIME_AUTHORISED` | D | bool | Y |  | xau_operational | true | - | publisher unauthorised -> XAU output regression |
| `HERMES_PUBLISHER_RUNTIME_ENABLED` | D | bool | Y |  | xau_operational | true | - | publisher disabled -> XAU output regression |
| `HERMES_QUOTE_PUBLISH_AUTHORISED` | D | bool | Y |  | xau_operational | true | - | publisher unauthorised -> XAU output regression |
| `HERMES_QUOTE_PUBLISH_ENABLED` | D | bool | Y |  | xau_operational | true | - | publisher disabled -> XAU output regression |
| `HERMES_QUOTE_PUBLISH_INSTRUMENTS` | D | csv | Y |  | xau_operational | XAU_USD | - | XAU parity-critical control; omission/change regresses the authorised XAU runtime |
| `HERMES_REDIS_CONTROL_PLANE_AUTHORISED` | D | bool | Y |  | xau_operational | true | - | publisher unauthorised -> XAU output regression |
| `HERMES_REDIS_CONTROL_PLANE_ENABLED` | D | bool | Y |  | xau_operational | true | - | publisher disabled -> XAU output regression |
| `HERMES_SESSION_PUBLISH_AUTHORISED` | D | bool | Y |  | xau_operational | true | - | publisher unauthorised -> XAU output regression |
| `HERMES_SESSION_PUBLISH_ENABLED` | D | bool | Y |  | xau_operational | true | - | publisher disabled -> XAU output regression |
| `HERMES_SESSION_PUBLISH_INSTRUMENTS` | D | csv | Y |  | xau_operational | XAU_USD | - | XAU parity-critical control; omission/change regresses the authorised XAU runtime |
| `HERMES_TICK_PUBLISH_INSTRUMENTS` | D | csv | Y |  | xau_operational | XAU_USD | - | XAU parity-critical control; omission/change regresses the authorised XAU runtime |

## Reconciliation sets (§7)

- **Retained (tracked non-secret):** 91 fields represented in the operational/dark overlays with canonical names.
- **Renamed (legacy -> canonical, fail-loud on conflict):** DEV_DB_HOST -> DB_HOST, DEV_DB_PASSWORD -> DB_PASSWORD, DEV_DB_PORT -> DB_PORT, DEV_DB_USER -> DB_USER, DEV_REDIS_DB -> REDIS_DB, DEV_REDIS_HOST -> REDIS_HOST, DEV_REDIS_KEY_PREFIX -> REDIS_KEY_PREFIX, DEV_REDIS_PASSWORD -> REDIS_PASSWORD, DEV_REDIS_PORT -> REDIS_PORT.
- **Externally supplied (secret/authority/host):** DB_HOST, DB_PASSWORD, DB_PORT, HERMES_CANDLE_CANONICAL_ACTIVATION_APPROVAL, HERMES_CANDLE_CANONICAL_REDIS_HOST, HERMES_CANDLE_CANONICAL_REDIS_PORT, OANDA_ACCOUNT_ID, OANDA_API_KEY, REDIS_HOST, REDIS_PASSWORD, REDIS_PORT.
- **Retired:** none under this WO (no consumed field removed; the shadow-tick/candle-forward-shadow keys remain as the app's existing non-production shadow controls, untouched).
- **Rejected (cross-application):** none entered the contract; only HERMES-owned keys are represented.

## Non-secret discipline
No secret VALUE appears here or in any tracked artefact. Secrets (DB/Redis passwords, OANDA keys) stay in the
base `.env` env_file; the authority-bearing canonical activation token is supplied by the governed env file
(external), represented only as `EXTERNAL_REQUIRED`.
