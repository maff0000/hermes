# Design Summary — HERMES Redis Control-Plane v1 (manifest/heartbeat/catalog/health)
**WO-HELM-HERMES-REDIS-CONTROL-PLANE-MANIFEST-HEALTH-0001 · code-only · HELM/HERMES**

Single module `utils/hermes_control_plane_v1.py` builds the governed v1 control-plane PAYLOADS for four future
versioned keys so ARES/Falcon can discover what HERMES publishes, what is live/gated/blocked/missing/legacy/out-of-scope:
- hermes:contract:manifest:v1   — families (active/gated/blocked/not-implemented/legacy) + ownership + source policies
- hermes:publisher:heartbeat:v1 — publisher liveness + per-timeframe freshness + fault/skip summaries (UTC-only)
- hermes:catalog:candles:v1     — per-timeframe latest/history mapping + source policy + status
- hermes:health:v1              — per-family health with explicit absence semantics

Pure builders + validators. Gated no-op factory: DISABLED by default; ENABLED-without-AUTHORISED -> SystemExit(101);
ENABLED+AUTHORISED -> ControlPlaneBuilder (payloads only, NO Redis client, NO I/O). ZERO Redis/network/SQL/file I/O
at import or anywhere. UTC-only timestamps (_utc/_assert_utc_field). NO unversioned aliases. NO auth/ACL. NO regime/risk
DATA fields (a recursive field-KEY scan forbids regime/risk/auth tokens as keys; ownership TEXT values may name them).
Publishing these keys is a SEPARATE, later, authorised WO.
