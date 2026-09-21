# HMT-1 — Research partition contract (`hmt1-research-partition-contract-v1`)

**Status:** IMPLEMENTED (HMT-1). Matches `market_truth/partition.py` exactly. Local/disposable test
filesystem storage only — no production object-store deployment, no HMT-1 historical events in
operational MariaDB (per `data-lifecycle-and-storage.md` §1.2/§4).

## Writer

| Field | Value |
|---|---|
| Library | `pyarrow` |
| Pinned version | see `requirements.txt` (`pyarrow==<exact version>`) and the HMT-1 final report for the exact version actually used |
| Compression | `zstd` |
| Writer config identity | `zstd-default-level-v1` (`partition.PartitionWriter.WRITER_CONFIG_ID`) |

## Deterministic logical layout

```
schema=v1/venue=<venue>/product=<product>/contract=<canonical-contract-id>/date=YYYY-MM-DD/event_type=<market_trade|top_of_book>/part-00000.parquet
schema=v1/venue=<source_classification>/instrument=<instrument-id>/date=YYYY-MM-DD/event_type=market_quote/part-00000.parquet
```

`date` is the UTC calendar date of `provenance.source_event_time`. `<canonical-contract-id>` and
`<instrument-id>` are filesystem-escaped (`partition._safe_segment`) but otherwise exactly the
event's `contract_id`/`instrument_id`. One file (`part-00000.parquet`) per (partition key, event
family) group — HMT-1's fixture volumes never need multi-part splitting.

## Canonical row serialization (§11 — exactly one)

`partition.serialize_event_row(event)` builds an explicit, fixed-order field list per event family
(`TRADE_ROW_FIELDS` / `TOP_OF_BOOK_ROW_FIELDS` / `QUOTE_ROW_FIELDS`, each ending in the same 17
shared provenance fields) and encodes every value with the same length-prefixed byte encoding
`identity.py` uses for event identity (`identity.encode_field`) — never JSON library default key
ordering/formatting, never a binary float. `hermes_receive_time` is excluded from every row (wall
clock; always `None` for fixture-replayed events — see the market-event-contract doc).

## Two distinct partition identities (§12)

- **`partition_content_sha256`** — SEMANTIC. SHA-256 over every row's `serialize_event_row()`
  bytes, in the deterministic row order given by `partition.event_sort_key` (source event time,
  then source sequence, then event ordinal, then identity hash — never input/iteration order).
  This is the identity that MUST reproduce exactly on every replay, and is asserted equal between
  run A and run B in `test_replay_determinism.py`.
- **`artifact_sha256`** — PHYSICAL. SHA-256 of the emitted Parquet file's actual bytes. Whether
  this also reproduces run-to-run depends on the pinned `pyarrow` writer's own byte determinism;
  the measured result (pass or an honest §12 escalation) is recorded in the HMT-1 final report and
  is never used to weaken the semantic-hash requirement above either way.

## Reload / reconstruction

`partition.PartitionReader.read_events(relative_dir, event_family)` reads the Parquet file back
and reconstructs full `MarketTradeEvent`/`TopOfBookEvent`/`MarketQuoteEvent` objects (including a
freshly-built `ProvenanceEnvelope`, `hermes_receive_time` again `None`) — not just raw rows. A
reloaded event's `serialize_event_row()` bytes are asserted identical to the original's in
`test_partition_roundtrip.py`.
