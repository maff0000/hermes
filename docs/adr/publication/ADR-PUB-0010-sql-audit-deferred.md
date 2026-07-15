# ADR-PUB-0010 — SQL audit deferred but mandatory before production activation
Status: Accepted. Decision: no SQL at the dark/isolated stage (K2 + logs suffice short-horizon); append-only HERMES-owned
audit table is mandatory before production activation and lands as its own WO. No cross-application writes; no DDL here.
