# Gap End-Epoch Invariant Ruling (§21)

Finding A: `_gaps_semantic_digest` currently keys on interval START and relies on `end = start + timeframe period`. The gaps
adapter constructs intervals as `[open_epoch, open_epoch + PERIOD_SECONDS[tf])`, so the invariant holds **by construction**
for all real inputs. This WO does **not** fix the digest.

Publication ruling: **publication validation MUST independently assert the invariant before declaring a proposal eligible**
(matrix I5). Any scoped gap/segment where `end != start + period(tf)` ⇒ refuse `PUB_GAP_INVARIANT_VIOLATION` (no key, alert).
This turns an implicit construction-time assumption into an explicit, externally-checkable eligibility gate — defence in
depth against a future producer that violates it. The invariant-hardening of the digest itself remains a separate WO.
