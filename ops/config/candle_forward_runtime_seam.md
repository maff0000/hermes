# Config Governance — HERMES candle-forward runtime seam (INERT)

**WO:** WO-HELM-HERMES-CANDLE-FORWARD-RUNTIME-WIRE-INERT-0001 · **Owner:** HELM (HERMES lane) · **Default:** DISABLED

| Key | Default | Description | Activation gate |
|-----|---------|-------------|-----------------|
| `HERMES_CANDLE_FORWARD_ENABLED` | `false` | Master gate for the runtime candle-forward emit seam. When false (default) the runtime constructs a no-op `DisabledCandleEmitter` — structurally aware, writes nothing, requires no sink config. | Architect-authorised activation only (and even enabled, this WO emits nothing). |
| `HERMES_CANDLE_FORWARD_SINK` | *(none — required iff enabled)* | Explicit forward sink mode. No hidden default — if `HERMES_CANDLE_FORWARD_ENABLED=true` and this is unset, the runtime FAILS LOUD. This WO permits only `none`/`inert` (governed `NoWriteCandleSink`). Any write mode (shadow/canonical/live) is rejected fail-loud (`GOV-CANDLE-FWD-SEAM-002`). | Write modes require a separate, authorised, R2D2-audited WO. |

```json
{
  "llm_reasoning": "The candle-forward path is trading-sensitive market-truth publication and must never be enabled by a hidden default. The runtime is made structurally aware of the governed candle producer/publisher via a disabled-by-default seam so a later authorised WO can activate it without re-plumbing. Enabling without an explicit sink mode fails loud (no silent no-op, no Proteus fallback, no stale-SQL fallback, no canonical writer). Only a no-write sink is permitted in the inert wire WO; shadow/canonical writes are gated to separate authorised steps.",
  "owner": "HELM/HERMES",
  "default_disabled": true,
  "fault_codes": ["GOV-CANDLE-FWD-SEAM-001", "GOV-CANDLE-FWD-SEAM-002"]
}
```
