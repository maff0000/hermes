# Configuration Doctrine (§25)

No operational configuration in code. No configuration installed by this WO. No hidden fallback defaults.

External, versioned, schema-validated, digestable, read-only-mounted (or approved external mechanism), secret-free config —
**absent while publication remains dark** — governs:

| Item | Notes |
|---|---|
| publication gates | env NAMES only (`HERMES_RECOVERY_PUBLISHER_ENABLED/AUTHORISED`); values external |
| `contract_version` supported set | authorised contract versions |
| `supported_planner_versions` | e.g. `["v1"]` |
| `supported_policy_versions` | e.g. `["1"]` |
| TTL / freshness timers | `proposal_ttl_seconds`, `revalidation_cadence_seconds`, `revalidation_max_age_seconds`, `max_gaps_age_seconds`, `max_coverage_age_seconds`, `status_ttl_seconds`, `tombstone_ttl_seconds` |
| payload limits | `max_payload_bytes`, `max_published_segments` |
| Redis key prefix | `hermes:recovery_proposal:` (governed) |
| alert thresholds | refusal-rate / staleness alerts, if applicable |
| `allow_unclassified_publish` | default false |

Doctrine mirrors the planner policy transport: a single mounted read-only JSON, digest-recorded, default-deny on any invalid
or missing field. The publisher fails closed (`PUB_DISABLED`) when config/gates are absent — which is the correct dark state.
