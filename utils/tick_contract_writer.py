"""HERMES-native raw-tick writer for the HERMES tick contract
(WO-HELM-HERMES-TICK-CONTRACT-PUBLISH-0001).

Writes HermesTickContract rows into tradingSignals.ticks honouring the existing
idempotency UNIQUE(instrument, timestamp, bid, ask, source) via INSERT IGNORE.
INSERT IGNORE here suppresses ONLY the idempotency duplicate (a true duplicate
tick is not a new valid tick) — and we make every ignore VISIBLE (logged) so
there is no silent loss of valid ticks. Includes HERMES-owned seq + contract_version.

NOT wired into the live hot path by this WO (capability only; no cutover).
conn_factory + seq_gen + logger are injected for testability.
"""
from __future__ import annotations
from typing import Callable
from models.tick_contract import HermesTickContract

_INSERT_IGNORE_SQL = (
    "INSERT IGNORE INTO ticks "
    "(instrument, timestamp, bid, ask, source, received_at_utc, seq, contract_version) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)


class TickContractWriter:
    def __init__(self, conn_factory: Callable, seq_gen, logger, contract_version: str):
        if not contract_version:
            raise ValueError("GOV-CFG-001: contract_version required (fail-loud; no default)")
        self._conn_factory = conn_factory
        self._seq_gen = seq_gen
        self._log = logger
        self._contract_version = contract_version

    def build_contract(self, instrument, source, source_ts_utc, received_at_utc, bid, ask) -> HermesTickContract:
        seq = self._seq_gen.next(instrument)
        return HermesTickContract(
            instrument=instrument, source=source,
            source_ts_utc=source_ts_utc, received_at_utc=received_at_utc,
            bid=bid, ask=ask, seq=seq, contract_version=self._contract_version,
        )

    def write(self, contract: HermesTickContract) -> bool:
        """Returns True if a row was inserted, False if idempotency-ignored (logged)."""
        params = (
            contract.instrument, contract.source_ts_utc, contract.bid, contract.ask,
            contract.source, contract.received_at_utc, contract.seq, contract.contract_version,
        )
        with self._conn_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(_INSERT_IGNORE_SQL, params)
                inserted = cur.rowcount == 1
            conn.commit()
        if not inserted:
            # VISIBLE: idempotency duplicate ignored — seq may gap (monotonic, not gapless).
            self._log.info(
                "[TICK_CONTRACT_IDEMPOTENT_SKIP] instrument=%s source_ts=%s seq=%s (duplicate, not a new valid tick)",
                contract.instrument, contract.source_ts_utc, contract.seq,
            )
        return inserted
