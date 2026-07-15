# Contract Envelope (§10)

Single versioned timezone-aware-UTC envelope carried in the current-pointer key K1. Distinguishes FIVE timestamps — never one
ambiguous time. Field-by-field authority in the JSON Schema. Summary of the distinct timestamps:

| Field | Meaning |
|---|---|
| `generated_at_utc` | planner produced the proposal object (holder) |
| `validated_at_utc` | publisher last ran the full eligibility predicate and it passed |
| `published_at_utc` | this envelope was written to Redis |
| `source_as_of_utc` | as-of time of the newest source input (gaps generated_at) the proposal reflects |
| `expires_at_utc` | hard expiry; `now >= expires_at_utc` ⇒ not current (independent of Redis TTL) |

Provenance block carries `publisher_instance_id` (single-writer fence), `source_commit`, `source_image`,
`publisher_component`. Safety block carries the const-locked execution/publication flags (§8). Supersession fields
(`supersedes_proposal_id`, `supersedes_generation`, `revocation_reason`) express replacement/removal. Both a Redis TTL AND an
explicit `expires_at_utc` are present so a consumer fails closed even if it reads a key whose TTL has not yet fired.
