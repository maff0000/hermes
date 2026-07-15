# Security & Threat Model (§24)

The publication surface is a single Redis key pair readable by a future consumer. Threats and mitigations:

| Threat | Vector | Mitigation |
|---|---|---|
| Stale proposal replay | consumer reads an old pointer as current | TTL on K1 + explicit `expires_at_utc` + `validated_at_utc` age check + status/tombstone in K2; consumer rejects on any |
| Unauthorised key overwrite | another writer writes K1 | single-writer doctrine + `publisher_instance_id` fence + monotonic-generation CAS; alien/lower generation ⇒ `PUB_STALE_WRITER`/`PUB_DUPLICATE_PUBLISHER` |
| Policy-digest spoofing | forged `policy_digest` in envelope | consumer does NOT trust the envelope digest as authority; the *publisher* recomputes policy digest from the live mounted policy each cycle (P4). Envelope digest is provenance, not authorisation |
| Proposal-ID collision | two different proposals same id | ordering is by `proposal_generation` (monotonic), not id; CAS on generation |
| Out-of-order writer | delayed write lands after newer | generation CAS rejects non-increasing writes |
| Duplicate publisher | two publisher processes | fence + `PUB_DUPLICATE_PUBLISHER`; multi-instance requires separate lease design (out of scope) |
| Oversized payload | huge segment list | `max_payload_bytes` + `maxItems` cap ⇒ `PUB_PAYLOAD_TOO_LARGE`, no write (never silent truncate) |
| Schema confusion | malformed/legacy envelope | strict `additionalProperties:false` + `contract`/`contract_version` const + consumer schema validation ⇒ `PUB_SCHEMA_VALIDATION_FAILED` |
| Alias injection `XAUUSD` | non-canonical instrument sneaks in | `instrument`/`canonical_instrument` const `XAU_USD`; any `XAUUSD` ⇒ `PUB_NON_CANONICAL_INSTRUMENT` + alert |
| Timestamp manipulation | future/oldened times | consumer checks `now < expires_at_utc` AND `validated_at_utc` age; publisher stamps from one injected UTC cycle clock |
| TTL extension without revalidation | keep-alive of stale | TTL renewal REQUIRES full eligibility re-pass (§12); no bare EXPIRE |
| Redis persistence restoring stale state | RDB/AOF reload brings back old K1 | cold-start doctrine (§14): retained K1 not trusted, revalidated or revoked/expired |
| Cross-application writer | ARES/Falcon writes the key | key prefix HERMES-owned + writer identity; anomalous writer ⇒ duplicate/stale-writer fault |
| Malformed segment | bad interval/timeframe | schema item constraints + gap end=start+period invariant (§21) ⇒ `PUB_GAP_INVARIANT_VIOLATION` |
| Consumer treats proposal as execution authority | design misuse | const `publication_only=true`, `execution_authorised=false`; consumer contract forbids inferring execution (§23) |

No secrets are ever stored in the payload. The envelope carries only digests + bounded counts + capped segment descriptors.
