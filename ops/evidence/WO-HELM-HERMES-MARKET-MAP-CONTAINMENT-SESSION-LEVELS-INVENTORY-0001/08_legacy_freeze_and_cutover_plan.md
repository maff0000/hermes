# Legacy Freeze & Cutover Plan
FROZEN (untouched, not deleted): hermes:market_map:* + hermes:signals:* + the host systemd service market-map-dev.service (PID 6843).
Cutover (SEPARATE future authorised WO, NOT this one):
1. Wire + activate hermes:sessions/levels publishers in-container (dual-publish alongside legacy).
2. Burn-in: prove new keys match the deterministic facts; ARES/Falcon switch reads to the new keys.
3. Authorised consumer-cutover window: stop market-map-dev.service (systemctl stop/disable) + retire hermes:market_map:*.
NO deletion, NO service stop, NO cutover in this WO. PID 6843 left running.
