# HERMES Tick `seq` Semantic — Stored-Row Sequence

**Semantic version:** `hermes.tick.seq.stored_row.v1`
**WO:** `WO-HELM-HERMES-TICK-GAP-SEMANTIC-AND-LEDGER-0001`
**Status:** Architect-ratified (after R2D2 GREEN of the 2026-06-10/11 outage gap assessment)
**All timestamps UTC.**

## Definition

HERMES tick `seq` is a **per-instrument monotonic sequence assigned over stored tick rows**,
ordered by `(timestamp, id)`.

`seq` is **NOT** a guarantee that every market-time tick exists. It guarantees only that the
ticks HERMES *stored* for an instrument are densely, monotonically numbered `1..N` with no
duplicates (enforced later by `UNIQUE(instrument, seq)` in migration 017).

## Why this semantic

The 2026-06-10/11 power/ingest outage produced synchronised raw-tick dropouts across all
instruments (~9–11h cumulative lost coverage per instrument over two days). These raw ticks
are **unrecoverable**: OANDA exposes no historical raw-tick endpoint in current HERMES
capability (only `/pricing/stream` live and `/pricing` snapshot), and there is no governed
fallback / archive / quarantine / Redis-AOF / PLUTUS source holding the missing ticks.

Because the missing ticks can never be reinserted, `seq` over `(timestamp, id)` is permanently
stable. Defining `seq` over *stored* rows (rather than over complete market time) is therefore
both correct and durable, and means the existing AUD_USD Stage E sequence
(`1..1,882,675`) **does not need reset**.

## Accepted / unrecoverable gaps

Accepted/unrecoverable tick gaps MUST be recorded in **`hermes_tick_gap_ledger`**
(migration 018). Each record carries `status`, `recoverability`, `accepted_policy`,
`semantic_version`, the backing `r2d2_finding_key`, and the Architect ruling text.

The 2026-06-10/11 outage windows are seeded as
`status=ACCEPTED`, `recoverability=UNRECOVERABLE`,
`accepted_policy=STORED_ROW_SEQUENCE`, `semantic_version=hermes.tick.seq.stored_row.v1`
(see `scripts/seed_tick_gap_ledger.py`).

## Consumer contract

Consumers that require **complete market-time tick coverage** (e.g. raw-tick microstructure
replay) MUST inspect `hermes_tick_gap_ledger` before relying on tick-level continuity. A
contiguous `seq` range does NOT imply contiguous market time.

Consumers operating on **candles/signals** are covered by the separate candle/signal recovery
lane: candle/signal repair may use broker (OANDA) candle backfill **with provenance**
(`hermes_backfill_row_provenance`), but it does **not** recreate raw ticks and is independent
of `seq`.

## Migration 017 `UNIQUE(instrument, seq)` precondition

`UNIQUE(instrument, seq)` (migration 017) can be considered ONLY after:

1. Stage F completes (all instruments sequenced);
2. all stored tick rows intended for sequencing have a non-NULL `seq`;
3. R2D2 validates dense / monotonic / no-duplicate `seq` per instrument;
4. accepted/unrecoverable tick gaps are recorded in `hermes_tick_gap_ledger`.

## Stage F gate

`scripts/stage_f_gate_check.py` is a read-only gate confirming the semantic is bound, the
2026-06-10/11 accepted gaps exist for all six instruments with none left OPEN/UNKNOWN, that
017 is absent, and that native writer / Redis tick stream / retention are all inactive. It
reports gate state only; it never runs Stage F.
