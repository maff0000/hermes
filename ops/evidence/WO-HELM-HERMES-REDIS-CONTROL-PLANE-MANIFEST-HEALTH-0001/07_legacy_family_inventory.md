# Legacy Family Inventory (advertised, NOT deleted)
- hermes:signals:price:{inst} / hermes:signals:latest:{inst}  -> FROZEN_PENDING_CONSUMER_CUTOVER (legacy, many instruments)
- hermes:market_map:{inst}                                    -> FROZEN_PENDING_CONSUMER_CUTOVER (legacy, many instruments)
- hermes:instrument:{inst} / hermes:instruments:enabled       -> LEGACY_OR_PARTIAL_CATALOG (unversioned; pending v1 catalog/migration)
No legacy deletion is performed or authorised in this WO. Retirement is a separate authorised consumer-cutover window.
