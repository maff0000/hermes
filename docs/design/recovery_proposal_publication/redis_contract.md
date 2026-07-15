# Redis Contract Specification (§9)

**Design only — no key is written by this WO.** Smallest safe surface. HERMES-owned. Follows the existing HERMES convention
`hermes:<family>:<INSTRUMENT>:v1` (cf. `hermes:gaps:XAU_USD:v1`, `hermes:backfill:status:XAU_USD:v1`).

## Approved keys (2)

### K1 — current eligible proposal pointer
| Field | Value |
|---|---|
| key | `hermes:recovery_proposal:XAU_USD:v1` |
| owner / writer | HERMES recovery publisher (single-writer, §16) |
| readers | future authorised HERMES consumer (none today) |
| type | Redis string containing the JSON envelope (`recovery_proposal_publication.v1.schema.json`) |
| contract_version | `"1"` |
| TTL | **`proposal_ttl_seconds`** (governed config; recommended 180s = 3× the 60s planner cadence). **Never persistent.** |
| refresh cadence | on each publisher revalidation cycle that *passes* full eligibility (TTL renewed only then, §12) |
| expiry behaviour | key disappears → external state `ABSENT` → consumer treats as *nothing current* |
| supersession | atomic CAS overwrite by a strictly-greater `proposal_generation` (§14) |
| max payload | `max_payload_bytes` (recommended 32 KiB); oversize ⇒ `PUB_PAYLOAD_TOO_LARGE`, no write |
| cardinality | exactly **one** key per instrument; no history |
| restart behaviour | a retained key is **not** trusted; must be revalidated or it expires (§14) |
| fault semantics | absent/expired = not-current (not a fault); write failure = component degrade, pointer expires |

### K2 — status / revocation record
| Field | Value |
|---|---|
| key | `hermes:recovery_proposal:status:XAU_USD:v1` |
| type | Redis string (JSON): last decision `{status, publication_eligibility, publication_reason_code, proposal_id, proposal_generation, validated_at_utc, expires_at_utc, revocation?, tombstone?}` |
| TTL | short (recommended `status_ttl_seconds` = 300s); tombstones carry `tombstone_ttl_seconds` |
| purpose | lets a consumer distinguish current vs stale/blocked/revoked/withdrawn without guessing; carries revocation tombstone |
| writer | same single publisher; updated every cycle (publish *or* refusal) |
| restart | short TTL bounds staleness; revocation tombstone survives until `tombstone_ttl_seconds` |

## Rejected for the initial surface (justified)
- **Per-generation immutable detail key** (`hermes:recovery_proposal:gen:<n>:...`): would accumulate unbounded history in
  Redis (violates "no unbounded proposal history"). Durable per-generation detail belongs in **HERMES SQL** (§22), keyed by
  `proposal_generation` + digest, added only when later authorised. The bounded summary in K1 is sufficient for a consumer.
- **Publication-health key**: deferred (§19). The initial dark stage needs no separate health key; the status record (K2)
  already exposes last decision + reason. A minimal health key is added only if a consumer contract later requires it.

## Envelope (carried in K1) — see `contract_envelope.md` + JSON Schema
Single versioned UTC envelope with distinct timestamps (`generated_at_utc`, `validated_at_utc`, `published_at_utc`,
`source_as_of_utc`, `expires_at_utc`), full provenance digests, bounded summary, safety flags, supersession/revocation fields,
and publisher identity. No secrets, ever.

## Namespacing & governance
- Key prefix `hermes:recovery_proposal:` is HERMES-owned; no other application may write it (single-writer + writer identity
  fencing, §16). Prefix is governed config (`configuration_doctrine.md`), not hard-coded operational policy.
- Instrument segment is always canonical `XAU_USD`; `XAUUSD` anywhere ⇒ `PUB_NON_CANONICAL_INSTRUMENT` (§24).
