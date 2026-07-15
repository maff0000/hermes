# ADR-PUB-0003 — Live truth is TTL-bounded and self-neutralising
Status: Accepted. Decision: no permanent key holds live truth; the current pointer expires within proposal_ttl_seconds if the
publisher stops revalidating; explicit expires_at_utc is also carried. Missing/expired ⇒ not current (fail closed).
