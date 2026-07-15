# TTL & Freshness Doctrine (§12)

**Fail loud. A missing or expired proposal is never current.** No permanent Redis key holds live truth.

## Timers (all governed config, `configuration_doctrine.md`)
| Name | Recommended | Meaning |
|---|---|---|
| `proposal_ttl_seconds` | 180 (3× planner cadence) | TTL of the current pointer K1; expiry ⇒ external `ABSENT` |
| `revalidation_cadence_seconds` | 60 (= planner cadence) | how often the publisher re-runs the full eligibility predicate |
| `revalidation_max_age_seconds` | 120 | max age of `validated_at_utc` before the pointer is `STALE` |
| `max_gaps_age_seconds` | 120 | max gaps `generated_at` age (matches planner staleness) |
| `max_coverage_age_seconds` | 900 | max coverage snapshot age (matches policy `coverage_max_age_seconds`) |
| `policy_validity_horizon_seconds` | n/a (digest-bound) | policy identity is digest-bound, not time-bound; drift ⇒ revoke |
| `status_ttl_seconds` | 300 | TTL of status record K2 |
| `tombstone_ttl_seconds` | 600 | revocation/withdrawal tombstone lifetime |

Constraint: `proposal_ttl_seconds > revalidation_cadence_seconds` (so a healthy publisher continuously renews before expiry),
and `revalidation_max_age_seconds ≥ revalidation_cadence_seconds` (one missed cycle is tolerated; two ⇒ stale). If the
publisher stalls, the pointer expires within `proposal_ttl_seconds` and the truth self-neutralises.

## Transitions
- **Fresh publish**: eligibility passes ⇒ write K1 with `expires_at_utc = published_at_utc + proposal_ttl_seconds`, set TTL.
- **Held renewal**: eligibility **re-passes** ⇒ renew TTL + bump `validated_at_utc`. **TTL renewal REQUIRES full
  revalidation** — never a bare `EXPIRE` to keep a stale holder alive (that would be false-green).
- **Stale**: `now − validated_at_utc > revalidation_max_age_seconds` ⇒ status `STALE`; consumer treats as not-current; pointer
  continues counting down to expiry (not force-refreshed).
- **Expired**: `now ≥ expires_at_utc` (or key gone) ⇒ external `ABSENT`.

## Consumer behaviour (fail-closed)
| Situation | Consumer must |
|---|---|
| current key missing | treat as **no current proposal** (not an error, not stale-as-live) |
| current key expired / `now ≥ expires_at_utc` | reject as not-current |
| status `STALE`/`BLOCKED`/`REVOKED`/`SUPERSEDED`/`WITHDRAWN` | reject as not-current |
| status/health absent | fail closed — do **not** assume current |
| schema/version/instrument mismatch | reject |

**Never** publish a stale holder merely to renew TTL. **Never** use a permanent (no-TTL) key for live truth.
