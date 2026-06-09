"""
Log formatters for JSON and Console output.

GOV-LOG-004: JSON format MUST include timestamp, level, service, message,
             decision_id, correlation_id fields.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class JSONFormatter(logging.Formatter):
    """
    JSON formatter for structured logging output.

    GOV-LOG-004: Output MUST include:
    - timestamp (ISO8601 with milliseconds)
    - level (string: DEBUG/INFO/WARNING/ERROR/CRITICAL)
    - service (string)
    - message (string)
    - decision_id (string, optional)
    - correlation_id (string, optional)
    - instrument (string, optional)
    - extra (dict, optional additional fields)
    """

    def __init__(
        self,
        service: str,
        decision_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        instrument: Optional[str] = None,
    ):
        """
        Initialize JSON formatter.

        Args:
            service: Service name (zeusv3, tyche, apollo, etc.)
            decision_id: Optional decision ID for tracing
            correlation_id: Optional correlation ID for request tracing
            instrument: Optional trading instrument
        """
        super().__init__()
        self.service = service
        self.decision_id = decision_id
        self.correlation_id = correlation_id
        self.instrument = instrument

    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record as JSON string.

        Args:
            record: Log record to format

        Returns:
            JSON string representation of log record
        """
        # Build base log entry
        log_entry: Dict[str, Any] = {
            "timestamp": self._format_timestamp(record),
            "level": record.levelname,
            "service": self.service,
            "message": record.getMessage(),
        }

        # Add optional trace IDs - prefer record-level over formatter-level
        decision_id = getattr(record, "decision_id", None) or self.decision_id
        if decision_id:
            log_entry["decision_id"] = decision_id

        correlation_id = getattr(record, "correlation_id", None) or self.correlation_id
        if correlation_id:
            log_entry["correlation_id"] = correlation_id

        instrument = getattr(record, "instrument", None) or self.instrument
        if instrument:
            log_entry["instrument"] = instrument

        # Add extra fields from record
        extra = getattr(record, "extra", None)
        if extra and isinstance(extra, dict):
            log_entry["extra"] = extra

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)

    def _format_timestamp(self, record: logging.LogRecord) -> str:
        """
        Format timestamp as ISO8601 with milliseconds.

        Args:
            record: Log record containing timestamp

        Returns:
            ISO8601 formatted timestamp string
        """
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(record.msecs):03d}Z"


class ConsoleFormatter(logging.Formatter):
    """
    Human-readable formatter for terminal output.

    Output format:
    2026-01-24 10:30:45.123 | INFO | zeusv3 | [DEC-abc123] Processing signal for XAU_USD
    """

    # ANSI color codes for terminal output
    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
        "RESET": "\033[0m",      # Reset
    }

    def __init__(
        self,
        service: str,
        decision_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        instrument: Optional[str] = None,
        use_colors: bool = True,
    ):
        """
        Initialize Console formatter.

        Args:
            service: Service name (zeusv3, tyche, apollo, etc.)
            decision_id: Optional decision ID for tracing
            correlation_id: Optional correlation ID for request tracing
            instrument: Optional trading instrument
            use_colors: Whether to use ANSI colors (default True)
        """
        super().__init__()
        self.service = service
        self.decision_id = decision_id
        self.correlation_id = correlation_id
        self.instrument = instrument
        self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record as human-readable string.

        Args:
            record: Log record to format

        Returns:
            Formatted string for console output
        """
        # Format timestamp
        timestamp = self._format_timestamp(record)

        # Get level with optional coloring
        level = record.levelname
        if self.use_colors:
            color = self.COLORS.get(level, "")
            reset = self.COLORS["RESET"]
            level_str = f"{color}{level:8}{reset}"
        else:
            level_str = f"{level:8}"

        # Build context prefix
        decision_id = getattr(record, "decision_id", None) or self.decision_id
        instrument = getattr(record, "instrument", None) or self.instrument

        context_parts = []
        if decision_id:
            context_parts.append(f"[DEC-{decision_id}]")
        if instrument:
            context_parts.append(f"[{instrument}]")

        context = " ".join(context_parts)
        if context:
            context = context + " "

        # Build final message
        message = record.getMessage()

        # Add exception info if present
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)

        return f"{timestamp} | {level_str} | {self.service:10} | {context}{message}"

    def _format_timestamp(self, record: logging.LogRecord) -> str:
        """
        Format timestamp for console display.

        Args:
            record: Log record containing timestamp

        Returns:
            Formatted timestamp string (YYYY-MM-DD HH:MM:SS.mmm)
        """
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{int(record.msecs):03d}"
