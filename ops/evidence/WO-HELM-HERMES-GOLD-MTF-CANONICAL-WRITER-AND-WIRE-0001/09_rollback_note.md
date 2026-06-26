# Rollback Note

- **Code rollback:** revert the merge of this PR (or `git revert <merge-sha>`). The branch touches only
  the candle writer/seam/aggregator + tests; reverting restores the prior inert posture exactly.
- **No runtime rollback needed:** nothing is activated. No env, no live Redis/SQL, no deploy. There is no
  running state to undo.
- **Forward safety:** even on `main`, canonical stays dark until a separate activation WO sets the 7
  governed env vars. If a future activation misbehaves, unsetting `HERMES_CANDLE_FORWARD_ENABLED`
  (or `SINK`) returns the seam to the disabled no-op on next boot.
