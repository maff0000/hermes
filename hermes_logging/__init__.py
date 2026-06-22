# HERMES-owned logging package (WO-HELM-HERMES-LOGGING-VENDOR-0001).
# Replaces the former cross-repo tradingProteus logging dependency. No hardcoded GELF target.
"""
Structured logging module for the trading platform.

Usage:
    from hermes_logging import LogLevel, get_logger, StructuredLogger

    # Simple usage
    log = get_logger("zeusv3")
    log.info("Processing signal")

    # With decision context (GOV-LOG-002)
    log = get_logger("zeusv3", decision_id="abc123", instrument="XAU_USD")
    log.info("Signal validated", extra={"confidence": 0.85})

    # Update context mid-flow
    log.decision_id = "new-decision-id"
    log.info("Decision updated")

    # Create child logger with new context
    child_log = log.with_context(decision_id="child-decision")

GOV-LOG-001: All services MUST use LogLevel enum.
GOV-LOG-002: decision_id MUST be included when available.
GOV-LOG-004: JSON output includes timestamp, level, service, message,
             decision_id, correlation_id.
GOV-LOG-011: All services streaming to Graylog MUST use GELFHandler.
"""

from .formatters import ConsoleFormatter, JSONFormatter
from .levels import LogLevel
from .logger import StructuredLogger, get_logger, resolve_gelf_target
from .gelf import GELFHandler, send_gelf, log_to_graylog, get_default_handler

__all__ = [
    "LogLevel",
    "get_logger",
    "resolve_gelf_target",
    "StructuredLogger",
    "JSONFormatter",
    "ConsoleFormatter",
    # GELF Handler (GOV-LOG-011)
    "GELFHandler",
    "send_gelf",
    "log_to_graylog",
    "get_default_handler",
]

__version__ = "1.0.0"
