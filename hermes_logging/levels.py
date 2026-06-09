"""
LogLevel enum with syslog mapping.

GOV-LOG-001: All services MUST use these levels.
"""

from enum import IntEnum


class LogLevel(IntEnum):
    """
    Unified log levels for all trading platform services.

    GOV-LOG-001: All services MUST use these levels.

    Level values match Python's logging module for compatibility:
    - DEBUG = 10
    - INFO = 20
    - WARNING = 30
    - ERROR = 40
    - CRITICAL = 50
    """
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50

    @property
    def syslog_level(self) -> int:
        """
        Map to syslog severity levels for GELF transport.

        Syslog severity levels (RFC 5424):
        - 7 = Debug
        - 6 = Informational
        - 4 = Warning
        - 3 = Error
        - 2 = Critical

        Returns:
            int: Corresponding syslog severity level
        """
        mapping = {
            10: 7,  # DEBUG -> debug
            20: 6,  # INFO -> informational
            30: 4,  # WARNING -> warning
            40: 3,  # ERROR -> error
            50: 2,  # CRITICAL -> critical
        }
        return mapping.get(self.value, 6)  # Default to informational if unknown

    @classmethod
    def from_string(cls, level_str: str) -> "LogLevel":
        """
        Convert string representation to LogLevel.

        Args:
            level_str: Level name (case-insensitive)

        Returns:
            LogLevel enum value

        Raises:
            ValueError: If level_str is not a valid level name
        """
        try:
            return cls[level_str.upper()]
        except KeyError:
            valid_levels = ", ".join(cls.__members__.keys())
            raise ValueError(
                f"Invalid log level: '{level_str}'. Valid levels: {valid_levels}"
            )
