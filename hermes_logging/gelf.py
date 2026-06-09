"""
GELF Handler for Graylog Integration.

GOV-LOG-011: All services streaming to Graylog MUST use this handler.

This module provides a GELF (Graylog Extended Log Format) handler that sends
logs to Graylog via TCP connection with automatic reconnection and non-blocking
behavior.

Usage:
    from hermes_logging.gelf import GELFHandler

    handler = GELFHandler()
    handler.send({
        'short_message': 'Something happened',
        '_service': 'zeusv3',
        '_decision_id': 'DEC-123',
    })
"""

import json
import os
import socket
import struct
import sys
import time
import threading
from typing import Optional, Dict, Any


# Syslog level mapping (Python logging level -> GELF/Syslog level)
SYSLOG_LEVELS = {
    'DEBUG': 7,
    'INFO': 6,
    'WARNING': 4,
    'WARN': 4,
    'ERROR': 3,
    'CRITICAL': 2,
    'FATAL': 2,
}

# Reverse mapping for convenience
LEVEL_NAMES = {
    7: 'DEBUG',
    6: 'INFO',
    4: 'WARNING',
    3: 'ERROR',
    2: 'CRITICAL',
}


class GELFHandler:
    """
    GELF handler for Graylog integration.

    GOV-LOG-011: All services streaming to Graylog MUST use this handler.

    Features:
    - TCP connection (reliable) with UDP fallback option
    - Non-blocking send (failures don't crash service)
    - Automatic reconnection on connection loss
    - Proper GELF message formatting

    Environment Variables:
    - GRAYLOG_HOST: Graylog server hostname/IP (default: 192.168.11.10)
    - GRAYLOG_PORT: Graylog GELF input port (default: 12201)
    - GRAYLOG_PROTOCOL: Connection protocol 'tcp' or 'udp' (default: tcp)
    - GRAYLOG_ENABLED: Enable/disable sending (default: true)
    """

    # TCP null terminator for GELF messages
    TCP_NULL_TERMINATOR = b'\x00'

    # GELF version
    GELF_VERSION = '1.1'

    # Maximum UDP chunk size (per GELF spec)
    UDP_CHUNK_SIZE = 8192

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        protocol: Optional[str] = None,
        timeout: float = 5.0,
        facility: str = 'trading-platform',
        service_name: Optional[str] = None,
    ):
        """
        Initialize GELF handler.

        Args:
            host: Graylog server hostname/IP. Default from GRAYLOG_HOST env.
            port: Graylog GELF input port. Default from GRAYLOG_PORT env.
            protocol: 'tcp' or 'udp'. Default from GRAYLOG_PROTOCOL env or 'tcp'.
            timeout: Socket timeout in seconds (default 5.0).
            facility: Log facility name (default 'trading-platform').
            service_name: Default service name for _service field.
        """
        # Load configuration from environment with defaults
        # WO-HELM-HERMES-LOGGING-VENDOR-0001: HERMES-owned; no hardcoded GELF target — fail-loud
        self.host = host or os.environ.get('GRAYLOG_HOST')
        if not self.host:
            raise ValueError('GRAYLOG_HOST not configured (fail-loud; no hardcoded default)')
        _port = port if port is not None else os.environ.get('GRAYLOG_PORT')
        if _port in (None, ''):
            raise ValueError('GRAYLOG_PORT not configured (fail-loud; no hardcoded default)')
        self.port = int(_port)
        self.protocol = (protocol or os.environ.get('GRAYLOG_PROTOCOL', 'udp')).lower()
        self.timeout = timeout
        self.facility = facility
        self.service_name = service_name

        # Check if GELF logging is enabled
        enabled_str = os.environ.get('GRAYLOG_ENABLED', 'true').lower()
        self.enabled = enabled_str in ('true', '1', 'yes', 'on')

        # Determine environment
        self.environment = 'PROD' if '/srv/' in os.getcwd() and '/srv-dev/' not in os.getcwd() else 'DEV'

        # Get hostname for GELF host field
        self._hostname = socket.gethostname()

        # Connection state
        self._socket: Optional[socket.socket] = None
        self._connected = False
        self._lock = threading.Lock()

        # Statistics
        self._stats = {
            'messages_sent': 0,
            'messages_failed': 0,
            'reconnections': 0,
            'last_error': None,
            'last_error_time': None,
        }

    def _get_socket(self) -> Optional[socket.socket]:
        """
        Get or create socket connection (lazy connection).

        Returns:
            Connected socket or None if connection failed.
        """
        if self._socket is not None and self._connected:
            return self._socket

        try:
            if self.protocol == 'udp':
                self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._socket.settimeout(self.timeout)
                self._connected = True
            else:  # TCP
                self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._socket.settimeout(self.timeout)
                self._socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self._socket.connect((self.host, self.port))
                self._connected = True
                self._stats['reconnections'] += 1

            return self._socket

        except socket.error as e:
            self._log_error(f"Failed to connect to Graylog at {self.host}:{self.port}: {e}")
            self._socket = None
            self._connected = False
            return None

    def _close_socket(self) -> None:
        """Close the current socket connection."""
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
            self._connected = False

    def _log_error(self, message: str) -> None:
        """
        Log error to stderr (non-blocking, won't raise).

        GOV-LOG-006: GELF failures MUST NOT crash the calling service.
        """
        self._stats['last_error'] = message
        self._stats['last_error_time'] = time.time()
        try:
            print(f"[GELF ERROR] {message}", file=sys.stderr)
        except Exception:
            pass

    def _format_message(self, message: Dict[str, Any]) -> bytes:
        """
        Format a message as GELF JSON.

        Args:
            message: Dictionary with message fields.

        Returns:
            JSON-encoded bytes with null terminator (for TCP).
        """
        # Build GELF message with required fields
        gelf_msg = {
            'version': self.GELF_VERSION,
            'host': message.get('host', self.service_name or self._hostname),
            'short_message': message.get('short_message', message.get('message', '')),
            'timestamp': message.get('timestamp', time.time()),
            'level': message.get('level', SYSLOG_LEVELS['INFO']),
        }

        # Add optional standard fields
        if 'full_message' in message:
            gelf_msg['full_message'] = message['full_message']

        # Add facility
        gelf_msg['_facility'] = message.get('_facility', self.facility)

        # Add environment
        gelf_msg['_environment'] = message.get('_environment', self.environment)

        # Add service name if not already present
        if '_service' not in message and self.service_name:
            gelf_msg['_service'] = self.service_name

        # Copy all custom fields (those starting with _)
        for key, value in message.items():
            if key.startswith('_') and key not in gelf_msg:
                # GELF requires additional fields to start with _
                # Ensure values are serializable
                if isinstance(value, (str, int, float, bool)) or value is None:
                    gelf_msg[key] = value
                else:
                    gelf_msg[key] = str(value)

        # Convert level from string if needed
        if isinstance(gelf_msg['level'], str):
            gelf_msg['level'] = SYSLOG_LEVELS.get(gelf_msg['level'].upper(), 6)

        # Ensure short_message is not empty (GELF requirement)
        if not gelf_msg['short_message']:
            gelf_msg['short_message'] = '(empty message)'

        # Encode to JSON
        json_bytes = json.dumps(gelf_msg, default=str).encode('utf-8')

        return json_bytes

    def send(self, message: Dict[str, Any]) -> bool:
        """
        Send GELF message to Graylog.

        GOV-LOG-006: MUST NOT raise exceptions on failure.

        Args:
            message: Dictionary with message fields. Required fields:
                - short_message: The log message text

                Optional standard fields:
                - host: Source host (default: service_name or hostname)
                - timestamp: Unix timestamp (default: now)
                - level: Syslog level 0-7 or string (default: 6/INFO)
                - full_message: Extended message text

                Custom fields (must start with _):
                - _service: Service name
                - _decision_id: Decision identifier
                - _correlation_id: Correlation ID
                - _instrument: Trading instrument
                - Any other custom fields

        Returns:
            True if sent successfully, False if failed.
        """
        if not self.enabled:
            return True  # Disabled, but not a failure

        with self._lock:
            try:
                # Format the message
                data = self._format_message(message)

                # Get or create socket
                sock = self._get_socket()
                if sock is None:
                    self._stats['messages_failed'] += 1
                    return False

                # Send based on protocol
                if self.protocol == 'udp':
                    # UDP: Check size limit
                    if len(data) > self.UDP_CHUNK_SIZE:
                        self._log_error(f"Message too large for UDP: {len(data)} bytes (max {self.UDP_CHUNK_SIZE})")
                        self._stats['messages_failed'] += 1
                        return False
                    sock.sendto(data, (self.host, self.port))
                else:
                    # TCP: Add null terminator
                    sock.sendall(data + self.TCP_NULL_TERMINATOR)

                self._stats['messages_sent'] += 1
                return True

            except socket.timeout:
                self._log_error(f"Timeout sending to Graylog (>{self.timeout}s)")
                self._close_socket()
                self._stats['messages_failed'] += 1
                return False

            except socket.error as e:
                self._log_error(f"Socket error sending to Graylog: {e}")
                self._close_socket()
                self._stats['messages_failed'] += 1
                return False

            except Exception as e:
                self._log_error(f"Unexpected error sending to Graylog: {e}")
                self._close_socket()
                self._stats['messages_failed'] += 1
                return False

    def send_log(
        self,
        message: str,
        level: str = 'INFO',
        service: Optional[str] = None,
        decision_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        instrument: Optional[str] = None,
        **extra_fields,
    ) -> bool:
        """
        Convenience method to send a log message with common fields.

        Args:
            message: The log message text.
            level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
            service: Service name (default: handler's service_name).
            decision_id: Decision identifier.
            correlation_id: Correlation ID for request tracing.
            instrument: Trading instrument symbol.
            **extra_fields: Additional custom fields (will be prefixed with _ if needed).

        Returns:
            True if sent successfully, False if failed.
        """
        gelf_msg: Dict[str, Any] = {
            'short_message': message,
            'level': SYSLOG_LEVELS.get(level.upper(), 6),
            'timestamp': time.time(),
        }

        if service:
            gelf_msg['_service'] = service
        elif self.service_name:
            gelf_msg['_service'] = self.service_name

        if decision_id:
            gelf_msg['_decision_id'] = decision_id

        if correlation_id:
            gelf_msg['_correlation_id'] = correlation_id

        if instrument:
            gelf_msg['_instrument'] = instrument

        # Add extra fields, ensuring they start with _
        for key, value in extra_fields.items():
            if not key.startswith('_'):
                key = f'_{key}'
            gelf_msg[key] = value

        return self.send(gelf_msg)

    def close(self) -> None:
        """
        Clean shutdown - close the socket connection.

        Call this when the application is shutting down.
        """
        with self._lock:
            self._close_socket()

    def get_stats(self) -> Dict[str, Any]:
        """
        Get handler statistics.

        Returns:
            Dictionary with:
            - messages_sent: Number of successfully sent messages
            - messages_failed: Number of failed sends
            - reconnections: Number of socket reconnections
            - last_error: Last error message (if any)
            - last_error_time: Timestamp of last error (if any)
        """
        return dict(self._stats)

    def is_connected(self) -> bool:
        """Check if handler has an active connection."""
        return self._connected

    def __enter__(self) -> 'GELFHandler':
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - close connection."""
        self.close()

    def __del__(self) -> None:
        """Destructor - ensure socket is closed."""
        try:
            self.close()
        except Exception:
            pass


# Module-level convenience functions

_default_handler: Optional[GELFHandler] = None
_handler_lock = threading.Lock()


def get_default_handler() -> GELFHandler:
    """
    Get or create the default GELF handler singleton.

    Returns:
        The default GELFHandler instance.
    """
    global _default_handler
    with _handler_lock:
        if _default_handler is None:
            _default_handler = GELFHandler()
        return _default_handler


def send_gelf(message: Dict[str, Any]) -> bool:
    """
    Send a GELF message using the default handler.

    Args:
        message: GELF message dictionary.

    Returns:
        True if sent, False if failed.
    """
    return get_default_handler().send(message)


def log_to_graylog(
    message: str,
    level: str = 'INFO',
    service: Optional[str] = None,
    **extra_fields,
) -> bool:
    """
    Send a log message to Graylog using the default handler.

    Args:
        message: Log message text.
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        service: Service name.
        **extra_fields: Additional fields.

    Returns:
        True if sent, False if failed.
    """
    return get_default_handler().send_log(message, level, service, **extra_fields)
