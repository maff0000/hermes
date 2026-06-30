# Part E — Containerisation / Containment Plan (design only; no runtime change)
Goal: bring host-loose market_map.py (systemd market-map-dev.service, PID 6843) inside the HERMES container/app
boundary and migrate its deterministic outputs to governed hermes:sessions:* / hermes:levels:* — without changing
runtime in this WO.

1. Target module path: utils/hermes_sessions_v1.py + utils/hermes_levels_v1.py (contracts, THIS PR) + a future
   utils/hermes_session_level_compute_v1.py wrapping market_map.py's deterministic compute (get_current_session,
   get_session_range, get_previous_day_range, calculate_adr) reading governed candle keys + governed session config.
2. Runtime entrypoint integration: wire build_session_publisher_from_env() + build_level_publisher_from_env() into
   main.py (like the indicator/candle-feature seam), gated by HERMES_SESSION_PUBLISH_* / HERMES_LEVEL_PUBLISH_*.
3. Env/config migration: move trading_windows/hermes_market_hours reads behind the HERMES container env; session
   config remains governed SQL/config (internal HERMES source, documented; not exposed to consumers).
4. Redis output migration: publish hermes:sessions:XAU_USD:v1 + hermes:levels:XAU_USD:{session,intraday}:v1 (daily/
   weekly GATED until D1 latest GREEN). Versioned only; XAU_USD only.
5. Legacy freeze/compat: market_map.py keeps writing hermes:market_map:* (FROZEN) until consumer cutover; the new
   keys run in parallel (dual-publish) during a burn-in window; NO legacy deletion.
6. Control-plane updates: manifest/health/catalog gain sessions=BUILT_NOT_ACTIVE->ACTIVE and levels (session/intraday
   ACTIVE; daily/weekly GATED). D1-derived levels stay gated until D1 latest GREEN.
7. Rollback: disable HERMES_SESSION/LEVEL_PUBLISH_* (publishers -> no-op); legacy market-map-dev.service untouched -> instant fallback.
8. Consumer-cutover prerequisites: ARES/Falcon read the new hermes:sessions/levels keys; parallel burn-in proven;
   then a SEPARATE authorised cutover WO stops market-map-dev.service + retires hermes:market_map:*.
9. No ARES interpretation in HERMES outputs: validators reject regime/risk/liquidity/order_block/smart_money/decision (tested).
10. No legacy deletion before cutover: hermes:market_map:* + the systemd service remain until the cutover WO.
