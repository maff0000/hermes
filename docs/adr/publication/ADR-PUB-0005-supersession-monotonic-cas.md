# ADR-PUB-0005 — Deterministic supersession via monotonic generation + CAS
Status: Accepted. Decision: proposal_generation is a monotonic publication sequence distinct from the deterministic
proposal_id; supersede only on strictly-greater generation via atomic compare-and-set; single-writer fence; no distributed
lock unless multi-instance is separately architected. Guarantees consumers never see two current proposals.
