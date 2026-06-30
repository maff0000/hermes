# Final Report — market_map Containment + Session/Levels Inventory (code-only/read-only)
**WO-HELM-HERMES-MARKET-MAP-CONTAINMENT-SESSION-LEVELS-INVENTORY-0001 · HELM/HERMES**
**Verdict: GREEN_PR_OPEN_HERMES_MARKET_MAP_CONTAINMENT_SESSION_LEVELS_INVENTORY_CODE_ONLY**

## Host process (read-only; not touched)
market_map.py PID 6843 — host-loose under systemd market-map-dev.service, cwd /srv-dev/tradingSignals/services/market-map,
--interval 60, writes legacy hermes:market_map:*, reads candles + trading_windows/hermes_market_hours SQL. OUTSIDE the container.

## Ownership (all deterministic -> HERMES-owned)
current_session + open/close = HERMES_DETERMINISTIC_SESSION_FACT; session ranges + PDH/PDL + ADR + midpoint =
HERMES_DETERMINISTIC_LEVEL_FACT (PDH/PDL/ADR are D1-derived -> GATED). No ARES_INTERPRETIVE_CONTEXT outputs (no
regime/risk/decision). Legacy hermes:market_map:* = LEGACY_DEPRECATED (frozen).

## Design artefacts (inert, code-only)
- utils/hermes_sessions_v1.py -> hermes:sessions:XAU_USD:v1 contract + disabled publisher.
- utils/hermes_levels_v1.py -> hermes:levels:XAU_USD:{scope}:v1 contract + disabled publisher; daily/weekly D1-derived scopes GATED.
- 22 tests; full HERMES+candle suite 433 passed.

## Containment plan
Contain market_map compute in-container behind the new gated publishers (dual-publish during burn-in), control-plane
sessions/levels statuses, rollback = disable flags / legacy untouched, cutover (stop service + retire legacy) is a SEPARATE authorised WO.

## Exclusions honoured
No deploy/restart/kill PID 6843/activation/Redis write/SQL write/auth/ACL/NOAUTH/D1 history/D1-derived activation/
consumer cutover/legacy deletion/regime/risk/decision/ARES interpretation/refactor/cross-app edits.

## Recommended next WO
Architect ruling to proceed with the containment build (wire session/levels compute in-container, dual-publish), then
a separate consumer-cutover WO to stop market-map-dev.service + retire hermes:market_map:*. D1-derived levels after D1 latest GREEN.
