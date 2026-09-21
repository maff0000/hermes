"""
market_truth.providers.fixture — the one deterministic provider implementation for HMT-1.

Reads a governed, committed JSONL fixture file and yields `RawSourceRecord`s in file order (the
provider's own deterministic native order — file line order, nothing else). No network call, no
credential, no vendor SDK dependency anywhere in this module.

Fixture doctrine (HMT-0/HMT-1 binding — see hmt1-provisional-scope.md §4 /
hmt2-provisional-acquisition-roadmap.md §11): synthetic governed fixtures are the default and, for
HMT-1's proof, sufficient. Every fixture line MUST carry `"__synthetic__": true`; a line missing
that marker is rejected — this module refuses to silently treat unlabeled data as if it might be a
real source record.

Fixture line schema (`FIXTURE_LINE_SCHEMA_VERSION`) — one JSON object per line::

    {
      "__synthetic__": true,
      "record_type": "TRADE" | "TOP_OF_BOOK_UPDATE" | "QUOTE",
      "source_classification": "<market_truth.provenance.SourceClassification value>",
      "provider_symbol": "<raw provider symbol - GC contract symbol, or a broker/OTC instrument id>",
      "source_artifact_id": "<unique-per-record source identity>",
      "source_event_time": "<verbatim ISO-8601 UTC text, e.g. 2026-09-15T13:30:00.123Z>",
      "source_timestamp_precision": "<market_truth.provenance.TimestampPrecision value>",
      "provider_receive_time": "<verbatim ISO-8601 UTC text, or null>",
      "provider_receive_quality": "<market_truth.provenance.ProviderReceiptQuality value>",
      "source_sequence": <int, or null>,
      "sequence_domain": "<tag, or 'NOT_AVAILABLE' iff source_sequence is null>",
      "acquisition_epoch": "<deterministic fixture-run identity string>",
      "historical_provenance_era": "<market_truth.provenance.HistoricalProvenanceEra value>",
      "quality_state": "<market_truth.contracts.EventQualityState value>",
      "payload": { ... record_type-specific fields, see canonicaliser.py ... }
    }
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterator, Mapping, MutableMapping

from market_truth.contracts import EventQualityState
from market_truth.provenance import (
    HistoricalProvenanceEra,
    ProviderReceiptQuality,
    SequenceAvailability,
    SourceClassification,
    TimestampPrecision,
    parse_utc_text,
)
from market_truth.provider import (
    MarketDataProvider,
    ProviderCapability,
    RawRecordType,
    RawSourceRecord,
)

FIXTURE_PROVIDER_VERSION = "hmt1-fixture-provider-v1"
FIXTURE_LINE_SCHEMA_VERSION = "hmt1-fixture-line-schema-v1"

_CAPABILITIES = frozenset(
    {
        ProviderCapability.TRADE_EVENTS,
        ProviderCapability.TOP_OF_BOOK_TRANSITIONS,
        ProviderCapability.SOURCE_SEQUENCE,
        ProviderCapability.PROVIDER_RECEIVE_TIMESTAMP,
        ProviderCapability.DEFINITIONS_SYMBOLOGY,
        ProviderCapability.HISTORICAL_RANGE_FIXTURE_INPUT,
        ProviderCapability.QUALITY_CONDITION_METADATA,
    }
)


class FixtureLoadError(ValueError):
    """Raised on any structurally invalid or unlabeled fixture line. Fail-closed."""


class FixtureMarketDataProvider(MarketDataProvider):
    """Loads one governed JSONL fixture file entirely into memory at construction time (fixtures
    are small, committed, and deterministic — there is no streaming/pagination concern here) and
    replays its lines, in file order, as `RawSourceRecord`s.
    """

    def __init__(self, fixture_path: Path, *, provider_id: str, dataset_id: str):
        super().__init__(provider_id=provider_id, dataset_id=dataset_id, capabilities=_CAPABILITIES)
        self.fixture_path = Path(fixture_path)
        self._raw_bytes = self.fixture_path.read_bytes()
        self._lines = [line for line in self._raw_bytes.decode("utf-8").splitlines() if line.strip()]
        # Fail fast at construction time: every line must parse and be labelled synthetic. A
        # provider that cannot even load must never silently offer zero records instead.
        self._parsed = [self._parse_line(i, line) for i, line in enumerate(self._lines)]

    def content_sha256(self) -> str:
        """Hash of the raw fixture file bytes exactly as committed — the "source fixture hash"
        recorded on the evidence manifest (evidence.py)."""
        return hashlib.sha256(self._raw_bytes).hexdigest()

    def _parse_line(self, index: int, line: str) -> MutableMapping[str, object]:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FixtureLoadError(f"{self.fixture_path}:{index + 1}: invalid JSON") from exc
        if obj.get("__synthetic__") is not True:
            raise FixtureLoadError(
                f"{self.fixture_path}:{index + 1}: missing/false '__synthetic__' marker — "
                "refusing to load a line that does not explicitly declare itself synthetic"
            )
        return obj

    def iter_records(self) -> Iterator[RawSourceRecord]:
        for index, obj in enumerate(self._parsed):
            yield self._to_raw_record(index, obj)

    def _to_raw_record(self, index: int, obj: Mapping) -> RawSourceRecord:
        try:
            record_type = RawRecordType(obj["record_type"])
            source_classification = SourceClassification(obj["source_classification"])
            raw_provider_symbol = obj["provider_symbol"]
            source_artifact_id = obj["source_artifact_id"]
            source_event_time_text = obj["source_event_time"]
            source_timestamp_precision = TimestampPrecision(obj["source_timestamp_precision"])
            provider_receive_time_text = obj.get("provider_receive_time")
            provider_receive_quality = ProviderReceiptQuality(obj["provider_receive_quality"])
            source_sequence = obj.get("source_sequence")
            sequence_domain = obj["sequence_domain"]
            acquisition_epoch = obj["acquisition_epoch"]
            historical_provenance_era = HistoricalProvenanceEra(obj["historical_provenance_era"])
            quality_state = EventQualityState(obj["quality_state"])
            payload = obj["payload"]
        except (KeyError, ValueError) as exc:
            raise FixtureLoadError(
                f"{self.fixture_path}: line {index + 1} does not satisfy {FIXTURE_LINE_SCHEMA_VERSION}: {exc}"
            ) from exc

        source_event_time = parse_utc_text(source_event_time_text)
        provider_receive_time = parse_utc_text(provider_receive_time_text) if provider_receive_time_text else None

        sequence_availability = (
            SequenceAvailability.NOT_AVAILABLE if source_sequence is None else SequenceAvailability.AVAILABLE
        )

        return RawSourceRecord(
            record_type=record_type,
            provider_id=self.provider_id,
            dataset_id=self.dataset_id,
            source_classification=source_classification,
            source_record_schema=FIXTURE_LINE_SCHEMA_VERSION,
            source_artifact_id=source_artifact_id,
            raw_provider_symbol=raw_provider_symbol,
            source_event_time=source_event_time,
            source_event_time_text=source_event_time_text,
            source_timestamp_precision=source_timestamp_precision,
            provider_receive_time=provider_receive_time,
            provider_receive_time_text=provider_receive_time_text,
            provider_receive_quality=provider_receive_quality,
            source_sequence=source_sequence,
            sequence_domain=sequence_domain,
            sequence_availability=sequence_availability,
            acquisition_epoch=acquisition_epoch,
            historical_provenance_era=historical_provenance_era,
            quality_state=quality_state,
            payload=payload,
        )
