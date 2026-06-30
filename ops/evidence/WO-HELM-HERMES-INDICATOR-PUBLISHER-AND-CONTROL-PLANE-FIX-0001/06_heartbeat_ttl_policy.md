# Heartbeat TTL Policy (R2D2 ruling)
heartbeat_ttl_policy(): heartbeat_refresh_seconds=60, heartbeat_ttl_seconds=180 (TTL > refresh) so a dead
publisher's heartbeat self-expires (liveness). hermes:contract:manifest:v1 / hermes:catalog:candles:v1 /
hermes:health:v1 remain PERSISTENT (no TTL) — discovery truth, refreshed + timestamped, never silently stale.
Code-only in this WO (constants + policy helper + tests); NO live TTL change. A later deploy/activate WO applies
the heartbeat TTL on the live publisher.
