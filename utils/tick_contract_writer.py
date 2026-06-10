"""HERMES-native raw-tick writer for the HERMES tick contract
(WO-HELM-HERMES-TICK-CONTRACT-PUBLISH-0001; R1 fix per R2D2 + Architect ruling).

Fail-loud market truth: a plain INSERT is used (NOT blanket INSERT IGNORE). Only a
GENUINE idempotency duplicate (the existing UNIQUE(instrument,timestamp,bid,ask,source)
row) is skipped — visibly. Every other condition fails loud:
  - a duplicate-key error that is NOT the idempotency key (e.g. future
    UNIQUE(instrument,seq) collision) -> raise GOV-TICK-INSERT-001;
  - malformed/truncated/any other DB error -> raise GOV-TICK-INSERT-001;
  - an unexpected zero-row plain INSERT -> raise GOV-TICK-INSERT-001.
No silent loss of valid ticks. Idempotent skips are logged, never labelled success.

NOT wired into the live hot path by this WO (capability only; no cutover).
conn_factory + seq_gen + logger are injected for testability.
"""
from __future__ import annotations
from typing import Callable
from models.tick_contract import HermesTickContract

_MYSQL_DUP_ENTRY = 1062  # ER_DUP_ENTRY

_INSERT_SQL = (
    "INSERT INTO ticks "
    "(instrument, timestamp, bid, ask, source, received_at_utc, seq, contract_version) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
_IDEMPOTENCY_SELECT_SQL = (
    "SELECT 1 FROM ticks "
    "WHERE instrument=%s AND timestamp=%s AND bid=%s AND ask=%s AND source=%s LIMIT 1"
)


def _is_dup_key_error(exc: Exception) -> bool:
    """True only for a MySQL duplicate-entry (1062) error, by error code."""
    args = getattr(exc, "args", None) or (None,)
    return args[0] == _MYSQL_DUP_ENTRY


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
        """True if inserted; False ONLY for a confirmed genuine idempotency duplicate.
        Raises (fail-loud) for every other condition."""
        params = (
            contract.instrument, contract.source_ts_utc, contract.bid, contract.ask,
            contract.source, contract.received_at_utc, contract.seq, contract.contract_version,
        )
        with self._conn_factory() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(_INSERT_SQL, params)
                    rc = cur.rowcount
                conn.commit()
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:
                    pass
                return self._handle_insert_error(conn, contract, exc)
            if rc != 1:
                raise RuntimeError(
                    f"GOV-TICK-INSERT-001: plain INSERT affected {rc} rows (expected 1) "
                    f"for instrument={contract.instrument} seq={contract.seq} — fail-loud"
                )
            return True

    def _handle_insert_error(self, conn, contract: HermesTickContract, exc: Exception) -> bool:
        # 1) anything that is NOT a duplicate-key error -> fail loud (no masking).
        if not _is_dup_key_error(exc):
            raise RuntimeError(
                f"GOV-TICK-INSERT-001: non-duplicate insert failure for "
                f"instrument={contract.instrument} seq={contract.seq}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        # 2) duplicate-key: confirm it is the GENUINE idempotency duplicate.
        if self._idempotency_row_exists(conn, contract):
            self._log.info(
                "[TICK_CONTRACT_IDEMPOTENT_SKIP] instrument=%s source_ts=%s seq=%s "
                "(genuine idempotency duplicate — not a new valid tick)",
                contract.instrument, contract.source_ts_utc, contract.seq,
            )
            return False
        # 3) duplicate-key but NOT the idempotency key (e.g. UNIQUE(instrument,seq)
        #    seq collision, or schema mistake) -> fail loud, never mislabel as duplicate.
        raise RuntimeError(
            f"GOV-TICK-INSERT-001: duplicate-key error that is NOT the idempotency key "
            f"(possible seq collision) for instrument={contract.instrument} seq={contract.seq} "
            f"— fail-loud"
        ) from exc

    def _idempotency_row_exists(self, conn, contract: HermesTickContract) -> bool:
        with conn.cursor() as cur:
            cur.execute(
                _IDEMPOTENCY_SELECT_SQL,
                (contract.instrument, contract.source_ts_utc, contract.bid, contract.ask, contract.source),
            )
            return cur.fetchone() is not None
