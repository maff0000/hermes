"""market_truth.acquisition.providers — HMT-2B provider adapters.

Exactly one adapter exists: `databento_historical.DatabentoHistoricalProvider` — a zero-spend,
metadata-only adapter (symbology/definitions resolution + free cost/record-count/billable-size
estimates). No bulk historical-data-download capability and no live-streaming client of any kind
exist anywhere in this subpackage — see that module's docstring for the full, binding scope
statement.

Vendor SDK objects never leave this subpackage's boundary: every public method on every adapter
here returns one of that adapter's own plain, frozen dataclasses (mirrors `market_truth.provider`'s
capability-abstraction pattern).
"""
