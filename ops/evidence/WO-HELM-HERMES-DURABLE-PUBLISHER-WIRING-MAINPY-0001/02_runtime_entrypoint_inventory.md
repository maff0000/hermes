# 02 — Runtime entrypoint inventory (Part A)

## Service entrypoint
- `main.py` — FastAPI app. `from contextlib import asynccontextmanager` (L15); `async def lifespan(app)` (L1001).
  Startup builds the gated `*_from_env` producers, then `yield`, then a shutdown block. Run under uvicorn.

## Existing gated runtime producers (the wiring pattern this WO matches)
| Producer | Built in lifespan via | Stored on |
|---|---|---|
| structure_engine_publisher | `_se_build_publisher()` | `state.structure_engine_publisher` |
| shadow_tick_emitter | `_build_shadow_tick_emitter()` | `state.shadow_tick_emitter` |
| candle_forward_emitter | `_build_candle_forward_seam()` (INERT, default no-op) | `state.candle_forward_emitter` |
| candle_h4_producer | `_build_h4_producer()` (gated; DisabledH4Producer default) | `state.candle_h4_producer` |

State defaults are set to `None` in the `ServiceState` block (L99–L110); the lifespan reassigns them. Shutdown
(after `yield`, L1238+) stops watchdog + healthcheck reporter, then cancels adapter tasks.

## Detached publisher launch scripts (the targets being made durable)
All in-container `/tmp`, launched as detached loops by each activate WO, killed by each deploy restart:
| Script | Family / keys | Cadence |
|---|---|---|
| `/tmp/control_plane_publisher.py` | `hermes:contract:manifest:v1` / `publisher:heartbeat:v1` / `catalog:candles:v1` / `health:v1` | 60s |
| `/tmp/indicator_publisher.py` | `hermes:indicators:XAU_USD:{M1,M5,M15,H1,H4}:v1` | 60s |
| `/tmp/candle_feature_publisher.py` | `hermes:candle_features:XAU_USD:{…}:v1` | 60s |
| `/tmp/sessions_levels_publisher.py` | `hermes:sessions:XAU_USD:v1` + `hermes:levels:XAU_USD:{session,intraday}:v1` | 60s |

Each detached script: builds its family publisher via the merged `*_from_env` factory (gate), no-ops if disabled,
then computes-and-writes on a bounded loop. The D1 producer/observer path is in-memory inside the service and
resets on restart (D1 AMBER) — left as-is (not part of the detached set; safe to preserve).

## Config loading
`env_config.get_env / get_env_bool / get_env_int(key, default, required)` — all HERMES-owned env. DB read for
sessions/classification config via `env_config.get_db_config()` (pre-existing governed credentials, read-only).

## Wiring decision
Add a fifth gated runtime component — the **publisher supervisor** — built+started in lifespan startup
(immediately after `candle_h4_producer`), stopped in lifespan shutdown (after healthcheck reporter). It owns
client-injected runner threads that call the durable step functions (ports of the four detached scripts).
