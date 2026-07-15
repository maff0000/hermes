# ADR-PUB-0002 — Smallest safe Redis surface: current pointer + status record
Status: Accepted. Decision: two TTL-bounded keys only (K1 current pointer, K2 status/revocation). Rejected: per-generation
immutable Redis keys (unbounded history) and a separate health key at the dark stage. Durable history -> HERMES SQL later.
