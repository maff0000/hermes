# No-Activation Proof

This is a **code-build-only, shadow-first** PR. Nothing is deployed, restarted, activated, or written to
any live store.

| Check | Evidence |
|-------|----------|
| Master gate unchanged | `.env.example` still `HERMES_CANDLE_FORWARD_ENABLED=false` (lines 97, 163) |
| No live `.env` touched | `git diff --cached --name-only` contains no `.env` (only `.env.example`) |
| No Redis write added | grep of `02_code_diff.patch` for `.set(`/`.hset(`/`redis.Redis(` added lines → **none** in production code (tests use in-memory fakes only) |
| No SQL/DML/migration | grep for `INSERT/UPDATE/DELETE/CREATE TABLE/ALTER/migrat` added → **none** |
| No backfill | grep for `backfill` → **none** |
| Shadow not activated | `shadow_authorised=True` appears **only** in test fixtures (`_shadow_cfg`); no runtime/.env sets it |
| Canonical dark | no `hermes:candles:*` write path wired; `signals:candle:*` still blocked by `GOV-CANDLE-CONTRACT-028` |
| Deferred TFs still skipped | `SUPPORTED_TF = ("M1","M5","M15","H1")`; D1/H4/D → `UNSUPPORTED_TIMEFRAME` |
| No service/container touched | no compose/systemd/Dockerfile change; no `ssh`/`docker` run |
| Live aggregator unchanged | `models/candle.py` is **not** in the diff — `CandleAggregator` still produces M1/M5/H1/D1; M15 production deferred |

## Runtime posture
The contract + dev-shadow seam are now **M1/M5/M15/H1-ready**, but remain **disabled by default**. Turning
the shadow writer on (and, separately, canonical publish) each require their own authorised, R2D2-audited
activation WO with explicit Redis host/port/db + authorisation flags. This PR changes code and tests only.
