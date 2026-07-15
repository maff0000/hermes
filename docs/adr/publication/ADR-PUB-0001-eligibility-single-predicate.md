# ADR-PUB-0001 — Eligibility is a single fail-closed predicate, re-evaluated each cycle
Status: Accepted (design). Decision: publication requires AND of the full eligibility matrix against LIVE inputs every cycle;
holder existence is never sufficient; a HELD holder is publishable only via revalidated HELD_CURRENT. Consequence: TTL renewal
is gated on revalidation. Alternative rejected: publish-on-holder-existence (would allow stale-as-current).
