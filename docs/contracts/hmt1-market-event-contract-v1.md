# HMT-1 — Market event contract (`hmt1-market-event-contract-v1`)

**Status:** IMPLEMENTED (HMT-1). Matches `market_truth/contracts.py`, `market_truth/provenance.py`
exactly — no aspirational field the code does not implement.

## Purpose

This is the running-code implementation of the event model ruled in
`docs/architecture/hmt0-market-truth-v2/canonical-market-events.md`. It defines three
provider-neutral, versioned, immutable canonical event families, the exact numeric type every
price uses, and the common provenance envelope every event carries.

## Versions

| Identity | Value | Where recorded |
|---|---|---|
| Market event contract schema | `hmt1-market-event-contract-v1` | `contracts.MARKET_EVENT_CONTRACT_SCHEMA_VERSION`, every event's `provenance.schema_version` |
| Canonicaliser | `hmt1-canonicaliser-v1` | `canonicaliser.CANONICALISER_VERSION`, every event's `provenance.canonicaliser_version` |
| Event-identity algorithm | `hmt1-event-identity-sha256-v1` | `identity.EVENT_IDENTITY_ALGORITHM_VERSION` |
| GC contract-mapping schema | `hmt1-gc-contract-mapping-v1` | `futures.CONTRACT_MAPPING_SCHEMA_VERSION` |
| Fixture-line schema | `hmt1-fixture-line-schema-v1` | `providers.fixture.FIXTURE_LINE_SCHEMA_VERSION` |
| Fixture provider | `hmt1-fixture-provider-v1` | `providers.fixture.FIXTURE_PROVIDER_VERSION` |

## Exact numerics (`ExactPrice`)

`mantissa: int`, `scale: int` — the value is `mantissa * 10**(-scale)`. Never binary float.
`ExactPrice.normalized()` strips trailing-zero mantissa digits to a canonical scale, so two
different textual representations of the same logical price (`"2387.40"` vs `"2387.400"`) compare
equal, hash equal, and serialize identically. `ExactPrice.from_decimal_string()` parses a decimal
literal without ever routing through a binary float.

## Provenance envelope (`ProvenanceEnvelope`)

Every field is kept permanently distinct (never one substituting for another):

| Field | Meaning |
|---|---|
| `provider_id`, `dataset_id` | Provider/dataset identity |
| `source_classification` | `RETAIL_CFD_STREAMING_QUOTE` / `BROKER_OTC_EXECUTABLE_QUOTE_OBSERVATION` / `CENTRALISED_EXCHANGE_FUTURES_GENUINE_ORDER_FLOW` — permanent, never inferred |
| `source_record_schema` | The source record's own type/schema identity |
| `raw_provider_symbol` | Provenance only — never the canonical identity (see `gc-futures-identity-and-roll.md`) |
| `source_artifact_id` | Identity of the raw source record |
| `source_event_time` / `source_event_time_text` | Market/exchange event time — parsed datetime + the source's VERBATIM text (identity/row serialization always uses the verbatim text, never a re-derived string, so precision is never invented) |
| `source_timestamp_precision` | `SECOND` / `MILLISECOND` / `MICROSECOND` / `NANOSECOND` / `UNKNOWN` |
| `provider_receive_time` / `_text` / `provider_receive_quality` | Only present when genuinely exposed; `NOT_AVAILABLE` quality forbids a receive time being set at all |
| `hermes_receive_time` | Deliberately excluded from event identity (wall clock); always `None` for fixture-replayed events — there is no genuine ingestion moment to record in a deterministic offline replay |
| `source_sequence` / `sequence_domain` / `sequence_availability` | The source's own sequence number, tagged with whose counting scheme produced it. Never HERMES's legacy stored-row `ticks.seq` scheme |
| `acquisition_epoch` | Connection/session/run identity |
| `canonicaliser_version` / `schema_version` | Governed versions — NOT part of event identity (see below) |
| `historical_provenance_era` | The three GC eras from `time-order-sequence-model.md` §3, or `NOT_APPLICABLE` |

## The three event families

### `MarketTradeEvent`
`contract_id` (never a bare root), `price` (`ExactPrice`), `quantity` (positive int), `aggressor`
(`AggressorClassification` — `UNKNOWN`/`NOT_AVAILABLE` is the only valid pairing when the source
cannot support a genuine side), `provenance`, `quality_state`, `identity_hash`, `event_ordinal`.
Requires `source_classification == CENTRALISED_EXCHANGE_FUTURES_GENUINE_ORDER_FLOW`.

### `TopOfBookEvent`
`contract_id`, `bid`/`ask` (`BookLevel`: `price` + optional `quantity`/`order_count` — absent means
absent, never defaulted), `provenance`, `quality_state`, `identity_hash`, `event_ordinal`. Rejects a
crossed book (`ask < bid`). Requires `CENTRALISED_EXCHANGE_FUTURES_GENUINE_ORDER_FLOW`. The
canonicaliser (not this contract) enforces the transition discipline: an event is only ever
constructed when the book has genuinely changed.

### `MarketQuoteEvent`
`instrument_id`, `bid`/`ask` (`Optional[ExactPrice]`, at least one required), `provenance`,
`quality_state`, `identity_hash`, `event_ordinal`. Requires `RETAIL_CFD_STREAMING_QUOTE` or
`BROKER_OTC_EXECUTABLE_QUOTE_OBSERVATION` — structurally cannot carry
`CENTRALISED_EXCHANGE_FUTURES_GENUINE_ORDER_FLOW`, and has no `aggressor`/`quantity` field at all.

## Event identity

One governed algorithm (`identity.compute_event_identity`): SHA-256 over an explicit,
length-prefixed serialization of `EventIdentityMaterial` — `algorithm_version`, `event_family`,
`provider_id`, `dataset_id`, `source_artifact_id`, `canonical_instrument_id`,
`source_record_identity`, `source_sequence`, `sequence_domain`, `source_event_time_text`,
`event_ordinal`. Deliberately excludes `canonicaliser_version`/`schema_version` (provenance about
shape, not identity of what) and any wall-clock/PID/random/path field — those fields do not exist
on the dataclass, so there is nothing to accidentally include.

## GC actual-contract identity + mapping

`futures.GcContractIdentity(delivery_year, delivery_month, venue="COMEX", product_root="GC")` —
`canonical_id()` renders e.g. `"COMEX:GC:2026-12"`, never a bare `"GC"`. `ContractMappingTable` is
an explicit, versioned, hashable provider-symbol -> contract table (loaded from a governed JSON
fixture); `resolve()` fails closed (`ContractMappingError`) on any symbol not in the table,
including bare/continuous roots and genuinely ambiguous single-digit-year wire symbols (see the
module docstring in `futures.py` for why this is a table lookup, not month-code arithmetic).
