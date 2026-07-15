# ADR-PUB-0006 — Restart re-establishes eligibility; retained keys are not trusted
Status: Accepted. Decision: no in-memory proposal survives restart; a retained external pointer is revalidated or
revoked/expired; no startup path republishes from Redis alone. Neutralises RDB/AOF-restored stale state.
