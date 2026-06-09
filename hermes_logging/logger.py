"""
StructuredLogger class and get_logger factory.

GOV-LOG-002: decision_id MUST be included when available.
GOV-LOG-011: All services streaming to Graylog MUST use GELFHandler.
GOV-CFG-001: All config from environment variables or explicit parameters.
"""

import logging
import os
import threading
import time
from typing import Any, Dict, Optional, Union

from .formatters import ConsoleFormatter, JSONFormatter
from .levels import LogLevel


class _GELFLoggingHandler(logging.Handler):
    """
    Python logging.Handler wrapper around GELFHandler.

    GOV-LOG-011: All services streaming to Graylog MUST use GELFHandler.
    GOV-LOG-006: GELF failures MUST NOT crash the calling service.

    This bridges Python's logging framework with our GELFHandler,
    allowing logs to be sent to both console and Graylog simultaneously.
    """

    def __init__(
        self,
        service: str,
        decision_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        instrument: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
    ):
        """
        Initialize GELF logging handler.

        Args:
            service: Service name for _service field
            decision_id: Default decision ID
            correlation_id: Default correlation ID
            instrument: Default instrument
            host: Graylog server hostname/IP (default from GRAYLOG_HOST env)
            port: Graylog GELF input port (default from GRAYLOG_PORT env)
        """
        super().__init__()
        # Import here to avoid circular imports at module level
        from .gelf import GELFHandler

        self._service = service
        self._decision_id = decision_id
        self._correlation_id = correlation_id
        self._instrument = instrument
        self._gelf_handler = GELFHandler(
            host=host,
            port=port,
            service_name=service,
        )

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a log record to Graylog.

        GOV-LOG-006: MUST NOT raise exceptions on failure.

        Args:
            record: Log record to send
        """
        try:
            # Build GELF message from log record
            gelf_msg: Dict[str, Any] = {
                'short_message': record.getMessage(),
                'timestamp': record.created,
                'level': self._python_to_syslog_level(record.levelno),
                '_service': self._service,
            }

            # Add decision_id - prefer record-level over handler-level
            decision_id = getattr(record, 'decision_id', None) or self._decision_id
            if decision_id:
                gelf_msg['_decision_id'] = decision_id

            # Add correlation_id
            correlation_id = getattr(record, 'correlation_id', None) or self._correlation_id
            if correlation_id:
                gelf_msg['_correlation_id'] = correlation_id

            # Add instrument
            instrument = getattr(record, 'instrument', None) or self._instrument
            if instrument:
                gelf_msg['_instrument'] = instrument

            # Add extra fields from record
            extra = getattr(record, 'extra', None)
            if extra and isinstance(extra, dict):
                for key, value in extra.items():
                    # Prefix with _ for GELF custom fields
                    gelf_key = f'_{key}' if not key.startswith('_') else key
                    gelf_msg[gelf_key] = value

            # Add exception info if present
            if record.exc_info:
                gelf_msg['full_message'] = self.format(record)

            # Send to Graylog (non-blocking)
            self._gelf_handler.send(gelf_msg)

        except Exception:
            # GOV-LOG-006: Never crash the calling service
            pass

    def _python_to_syslog_level(self, python_level: int) -> int:
        """
        Convert Python logging level to syslog level.

        Args:
            python_level: Python logging level (10-50)

        Returns:
            Syslog level (0-7)
        """
        if python_level >= 50:  # CRITICAL
            return 2
        elif python_level >= 40:  # ERROR
            return 3
        elif python_level >= 30:  # WARNING
            return 4
        elif python_level >= 20:  # INFO
            return 6
        else:  # DEBUG
            return 7

    def close(self) -> None:
        """Clean shutdown - close GELF handler."""
        try:
            self._gelf_handler.close()
        except Exception:
            pass
        super().close()


class StructuredLogger:
    """
    Thread-safe structured logger wrapper.

    Provides:
    - LogLevel enum support
    - Auto-injection of service, decision_id, correlation_id
    - Extra field support via log.info("message", extra={"key": "value"})
    - Thread-safe operation
    """

    _lock = threading.Lock()

    def __init__(
        self,
        name: str,
        service: str,
        decision_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        instrument: Optional[str] = None,
        level: Union[LogLevel, int] = LogLevel.INFO,
    ):
        """
        Initialize structured logger.

        Args:
            name: Logger name (typically module name)
            service: Service name (zeusv3, tyche, apollo, etc.)
            decision_id: Optional decision ID for tracing (GOV-LOG-002)
            correlation_id: Optional correlation ID for request tracing
            instrument: Optional trading instrument
            level: Minimum log level (default INFO)
        """
        self._name = name
        self._service = service
        self._decision_id = decision_id
        self._correlation_id = correlation_id
        self._instrument = instrument
        self._level = level if isinstance(level, int) else int(level)

        # Get or create underlying Python logger
        self._logger = logging.getLogger(name)
        self._logger.setLevel(self._level)

        # Track handlers to avoid duplicates
        self._handlers_added = False

    @property
    def service(self) -> str:
        """Get service name."""
        return self._service

    @property
    def decision_id(self) -> Optional[str]:
        """Get decision ID."""
        return self._decision_id

    @decision_id.setter
    def decision_id(self, value: Optional[str]) -> None:
        """
        Set decision ID for subsequent log entries.

        GOV-LOG-002: decision_id MUST be included when available.
        """
        self._decision_id = value

    @property
    def correlation_id(self) -> Optional[str]:
        """Get correlation ID."""
        return self._correlation_id

    @correlation_id.setter
    def correlation_id(self, value: Optional[str]) -> None:
        """Set correlation ID for subsequent log entries."""
        self._correlation_id = value

    @property
    def instrument(self) -> Optional[str]:
        """Get instrument."""
        return self._instrument

    @instrument.setter
    def instrument(self, value: Optional[str]) -> None:
        """Set instrument for subsequent log entries."""
        self._instrument = value

    def add_console_handler(self, use_colors: bool = True) -> None:
        """
        Add console (stderr) handler with ConsoleFormatter.

        Args:
            use_colors: Whether to use ANSI colors (default True)
        """
        with self._lock:
            handler = logging.StreamHandler()
            handler.setLevel(self._level)
            handler.setFormatter(
                ConsoleFormatter(
                    service=self._service,
                    decision_id=self._decision_id,
                    correlation_id=self._correlation_id,
                    instrument=self._instrument,
                    use_colors=use_colors,
                )
            )
            self._logger.addHandler(handler)

    def add_json_handler(self, stream=None) -> None:
        """
        Add JSON handler (for file or stream output).

        Args:
            stream: Output stream (default: stderr)
        """
        with self._lock:
            handler = logging.StreamHandler(stream)
            handler.setLevel(self._level)
            handler.setFormatter(
                JSONFormatter(
                    service=self._service,
                    decision_id=self._decision_id,
                    correlation_id=self._correlation_id,
                    instrument=self._instrument,
                )
            )
            self._logger.addHandler(handler)

    def add_gelf_handler(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
    ) -> None:
        """
        Add GELF handler for Graylog integration.

        GOV-LOG-011: All services streaming to Graylog MUST use GELFHandler.

        Args:
            host: Graylog server hostname/IP (default from GRAYLOG_HOST env)
            port: Graylog GELF input port (default from GRAYLOG_PORT env)
        """
        # Import here to avoid circular imports
        from .gelf import GELFHandler

        with self._lock:
            handler = _GELFLoggingHandler(
                service=self._service,
                decision_id=self._decision_id,
                correlation_id=self._correlation_id,
                instrument=self._instrument,
                host=host,
                port=port,
            )
            handler.setLevel(self._level)
            self._logger.addHandler(handler)

    def _make_record_extras(
        self,
        extra: Optional[Dict[str, Any]] = None,
        decision_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        instrument: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Build extras dict for log record.

        Per-call values override instance defaults.
        """
        extras: Dict[str, Any] = {}

        # Use per-call values if provided, else instance defaults
        dec_id = decision_id if decision_id is not None else self._decision_id
        cor_id = correlation_id if correlation_id is not None else self._correlation_id
        inst = instrument if instrument is not None else self._instrument

        if dec_id:
            extras["decision_id"] = dec_id
        if cor_id:
            extras["correlation_id"] = cor_id
        if inst:
            extras["instrument"] = inst
        if extra:
            extras["extra"] = extra

        return extras

    def _log(
        self,
        level: int,
        msg: str,
        *args,
        extra: Optional[Dict[str, Any]] = None,
        decision_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        instrument: Optional[str] = None,
        exc_info: bool = False,
        **kwargs,
    ) -> None:
        """
        Internal log method.

        Args:
            level: Log level (integer)
            msg: Log message (may contain %-style format codes)
            *args: Format arguments
            extra: Extra fields to include in log
            decision_id: Override decision_id for this entry
            correlation_id: Override correlation_id for this entry
            instrument: Override instrument for this entry
            exc_info: Include exception info
            **kwargs: Additional keyword arguments (ignored)
        """
        if not self._logger.isEnabledFor(level):
            return

        record_extras = self._make_record_extras(
            extra=extra,
            decision_id=decision_id,
            correlation_id=correlation_id,
            instrument=instrument,
        )

        self._logger.log(level, msg, *args, extra=record_extras, exc_info=exc_info)

    def debug(
        self,
        msg: str,
        *args,
        extra: Optional[Dict[str, Any]] = None,
        decision_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        instrument: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Log at DEBUG level."""
        self._log(
            LogLevel.DEBUG,
            msg,
            *args,
            extra=extra,
            decision_id=decision_id,
            correlation_id=correlation_id,
            instrument=instrument,
            **kwargs,
        )

    def info(
        self,
        msg: str,
        *args,
        extra: Optional[Dict[str, Any]] = None,
        decision_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        instrument: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Log at INFO level."""
        self._log(
            LogLevel.INFO,
            msg,
            *args,
            extra=extra,
            decision_id=decision_id,
            correlation_id=correlation_id,
            instrument=instrument,
            **kwargs,
        )

    def warning(
        self,
        msg: str,
        *args,
        extra: Optional[Dict[str, Any]] = None,
        decision_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        instrument: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Log at WARNING level."""
        self._log(
            LogLevel.WARNING,
            msg,
            *args,
            extra=extra,
            decision_id=decision_id,
            correlation_id=correlation_id,
            instrument=instrument,
            **kwargs,
        )

    def error(
        self,
        msg: str,
        *args,
        extra: Optional[Dict[str, Any]] = None,
        decision_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        instrument: Optional[str] = None,
        exc_info: bool = False,
        **kwargs,
    ) -> None:
        """Log at ERROR level."""
        self._log(
            LogLevel.ERROR,
            msg,
            *args,
            extra=extra,
            decision_id=decision_id,
            correlation_id=correlation_id,
            instrument=instrument,
            exc_info=exc_info,
            **kwargs,
        )

    def critical(
        self,
        msg: str,
        *args,
        extra: Optional[Dict[str, Any]] = None,
        decision_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        instrument: Optional[str] = None,
        exc_info: bool = False,
        **kwargs,
    ) -> None:
        """Log at CRITICAL level."""
        self._log(
            LogLevel.CRITICAL,
            msg,
            *args,
            extra=extra,
            decision_id=decision_id,
            correlation_id=correlation_id,
            instrument=instrument,
            exc_info=exc_info,
            **kwargs,
        )

    def exception(
        self,
        msg: str,
        *args,
        extra: Optional[Dict[str, Any]] = None,
        decision_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        instrument: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Log at ERROR level with exception info."""
        self._log(
            LogLevel.ERROR,
            msg,
            *args,
            extra=extra,
            decision_id=decision_id,
            correlation_id=correlation_id,
            instrument=instrument,
            exc_info=True,
            **kwargs,
        )

    def with_context(
        self,
        decision_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        instrument: Optional[str] = None,
    ) -> "StructuredLogger":
        """
        Create a new logger with updated context.

        Returns a new StructuredLogger instance with the specified
        context values, sharing the same underlying Python logger.

        Args:
            decision_id: New decision ID (or None to keep current)
            correlation_id: New correlation ID (or None to keep current)
            instrument: New instrument (or None to keep current)

        Returns:
            New StructuredLogger with updated context
        """
        new_logger = StructuredLogger(
            name=self._name,
            service=self._service,
            decision_id=decision_id if decision_id is not None else self._decision_id,
            correlation_id=correlation_id if correlation_id is not None else self._correlation_id,
            instrument=instrument if instrument is not None else self._instrument,
            level=self._level,
        )
        # Share the underlying logger (with its handlers)
        new_logger._logger = self._logger
        return new_logger


def get_logger(
    service: str,
    name: Optional[str] = None,
    decision_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    instrument: Optional[str] = None,
    level: Optional[Union[LogLevel, int, str]] = None,
    enable_console: bool = True,
    enable_json: bool = False,
    enable_gelf: Optional[bool] = None,
    gelf_host: Optional[str] = None,
    gelf_port: Optional[int] = None,
    use_colors: bool = True,
) -> StructuredLogger:
    """
    Get a configured logger for a service.

    GOV-LOG-002: decision_id MUST be included when available.
    GOV-LOG-011: All services streaming to Graylog MUST use GELFHandler.
    GOV-CFG-001: Config from environment or explicit parameters.

    Environment variables:
    - LOG_LEVEL: Default log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    - GRAYLOG_ENABLED: Enable GELF handler (true/false, default false)
    - GRAYLOG_HOST: Graylog server hostname/IP (default 192.168.11.10)
    - GRAYLOG_PORT: Graylog GELF input port (default 12201)

    Args:
        service: Service name (zeusv3, tyche, apollo, etc.)
        name: Logger name (defaults to service name)
        decision_id: Optional decision ID for tracing
        correlation_id: Optional correlation ID for request tracing
        instrument: Optional trading instrument
        level: Log level (LogLevel enum, int, or string). If None, uses
               LOG_LEVEL env var or defaults to INFO.
        enable_console: Add console handler (default True)
        enable_json: Add JSON handler to stderr (default False)
        enable_gelf: Enable GELF handler for Graylog. If None, uses
                     GRAYLOG_ENABLED env var (default False).
        gelf_host: Graylog server hostname/IP (default from GRAYLOG_HOST env)
        gelf_port: Graylog GELF input port (default from GRAYLOG_PORT env)
        use_colors: Use ANSI colors in console output (default True)

    Returns:
        Configured StructuredLogger instance

    Example:
        # Basic usage with console output
        log = get_logger("zeusv3")
        log.info("Processing signal")

        # With GELF enabled (also sends to Graylog)
        log = get_logger("zeusv3", enable_gelf=True, decision_id="DEC-123")
        log.info("Signal validated", extra={"confidence": 0.85})

        # Enable GELF via environment:
        # export GRAYLOG_ENABLED=true
        # export GRAYLOG_HOST=127.0.0.1
        # export GRAYLOG_PORT=12201
        log = get_logger("zeusv3")  # GELF automatically enabled
    """
    # Resolve log level
    if level is None:
        # Check environment variable
        env_level = os.environ.get("LOG_LEVEL", "INFO")
        level = LogLevel.from_string(env_level)
    elif isinstance(level, str):
        level = LogLevel.from_string(level)
    elif isinstance(level, int) and not isinstance(level, LogLevel):
        # Accept raw int but map to nearest LogLevel
        level = level

    # Resolve GELF enabled from environment if not explicitly provided
    if enable_gelf is None:
        gelf_enabled_str = os.environ.get("GRAYLOG_ENABLED", "false").lower()
        enable_gelf = gelf_enabled_str in ("true", "1", "yes", "on")

    # Create logger
    logger_name = name or service
    logger = StructuredLogger(
        name=logger_name,
        service=service,
        decision_id=decision_id,
        correlation_id=correlation_id,
        instrument=instrument,
        level=level,
    )

    # Add handlers — but only if this Python logger has none yet.
    # Multiple get_logger() calls with the same name (e.g. daemon + engine
    # both requesting service='tyche') share one logging.getLogger('tyche').
    # Without this guard, each call appends another StreamHandler → duplicate lines.
    if logger._logger.handlers:
        return logger

    if enable_console:
        logger.add_console_handler(use_colors=use_colors)

    if enable_json:
        logger.add_json_handler()

    if enable_gelf:
        logger.add_gelf_handler(host=gelf_host, port=gelf_port)

    return logger
