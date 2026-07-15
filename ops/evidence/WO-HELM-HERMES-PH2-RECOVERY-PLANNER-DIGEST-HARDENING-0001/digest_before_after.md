# Recovery-relevant digest — before/after demonstration

Fixture: NOW=2026-07-14 12:00Z; H1 gap open 06:00 (window [05:00,08:00)); retention floors far in the past.

| coverage change | recovery-relevant digest | vs empty |
|---|---|---|
| empty | `d7fb79bafc9fc001` | baseline |
| + unrelated H1 candle @11:00 (outside window) | `d7fb79bafc9fc001` | UNCHANGED |
| + unrelated M1 current-edge candle | `d7fb79bafc9fc001` | UNCHANGED |
| + H1 candle @06:00 that FILLS the gap | `949890c49195d109` | CHANGED |

BEFORE this WO: the whole-window coverage presence digest changed on ANY new/sealed candle within retention,
so all three rows above would have CHANGED the tuple and forced a recompute. AFTER: only the gap-filling row changes it.
