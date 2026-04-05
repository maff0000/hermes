"""
IRIS Client for HERMES
EPIC-D002: tradingSignals
GOV-ENV-001: Environment-aware configuration

Thin wrapper for sending healthchecks, alerts, and audit logs to IRIS.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Literal

import requests

from env_config import get_env, ENV

logger = logging.getLogger("signal-service.iris")

# IRIS endpoint (single instance for all environments)
# GOV-ENV-001: No hardcoded URLs - must be in .env
IRIS_URL = get_env("IRIS_URL", required=True)
IRIS_TIMEOUT = 5  # seconds


class IRISClient:
    """
    Client for sending messages to IRIS alerting system.

    All services send to the same IRIS instance, differentiated by
    the `environment` field in the payload.
    """

    def __init__(self, source: str = "HERMES", environment: str = None):
        """
        Initialize IRIS client.

        Args:
            source: Service name (HERMES, ARES, ZEUS, etc.)
            environment: DEV or PROD (defaults to ENV from env_config)
        """
        self.source = source
        self.environment = environment or ENV
        self.url = IRIS_URL
        self._enabled = True

    def _send(self, payload: Dict[str, Any]) -> bool:
        """Send payload to IRIS. Returns True if accepted."""
        if not self._enabled:
            return False

        try:
            response = requests.post(
                self.url,
                json=payload,
                timeout=IRIS_TIMEOUT,
                headers={"Content-Type": "application/json"}
            )

            if response.status_code == 200:
                data = response.json()
                logger.debug(f"IRIS accepted: {data.get('id', 'unknown')}")
                return True
            else:
                logger.warning(f"IRIS rejected: {response.status_code} - {response.text}")
                return False

        except requests.exceptions.ConnectionError:
            logger.warning("IRIS connection failed - service may be down")
            return False
        except requests.exceptions.Timeout:
            logger.warning("IRIS request timed out")
            return False
        except Exception as e:
            logger.error(f"IRIS send error: {e}")
            return False

    def send_healthcheck(
        self,
        status: Literal["healthy", "degraded", "down"],
        message: str,
        metrics: Dict[str, Any],
        severity: Literal["info", "warning", "critical"] = None
    ) -> bool:
        """
        Send a healthcheck to IRIS.

        Args:
            status: healthy, degraded, or down
            message: Human-readable status message
            metrics: Dict of metrics (instruments_active, ticks_last_minute, etc.)
            severity: info/warning/critical (auto-derived from status if not provided)

        Returns:
            True if IRIS accepted the healthcheck
        """
        # Auto-derive severity from status if not provided
        if severity is None:
            severity = {
                "healthy": "info",
                "degraded": "warning",
                "down": "critical"
            }.get(status, "info")

        payload = {
            "type": "healthcheck",
            "source": self.source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": severity,
            "message": message,
            "environment": self.environment,
            "status": status,
            "metrics": metrics
        }

        return self._send(payload)

    def send_alert(
        self,
        severity: Literal["info", "warning", "critical"],
        message: str,
        category: str = None,
        reference_id: str = None,
        details: Dict[str, Any] = None
    ) -> bool:
        """
        Send an alert to IRIS.

        Args:
            severity: info, warning, or critical
            message: Alert message
            category: Optional category (risk, system, trade, etc.)
            reference_id: Optional reference ID (e.g., DECISION-12345)
            details: Optional additional details

        Returns:
            True if IRIS accepted the alert
        """
        payload = {
            "type": "alert",
            "source": self.source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": severity,
            "message": message,
            "environment": self.environment
        }

        if category:
            payload["category"] = category
        if reference_id:
            payload["reference_id"] = reference_id
        if details:
            payload["details"] = details

        return self._send(payload)

    def send_audit(
        self,
        action: str,
        message: str,
        actor: str = None,
        details: Dict[str, Any] = None
    ) -> bool:
        """
        Send an audit log entry to IRIS.

        Args:
            action: Action performed (config_deployed, service_started, etc.)
            message: Description of what happened
            actor: Who/what performed the action
            details: Additional details

        Returns:
            True if IRIS accepted the audit log
        """
        payload = {
            "type": "audit",
            "source": self.source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": "info",
            "message": message,
            "environment": self.environment,
            "action": action,
            "actor": actor or self.source.lower()
        }

        if details:
            payload["details"] = details

        return self._send(payload)

    def disable(self):
        """Disable IRIS reporting (useful for tests)."""
        self._enabled = False

    def enable(self):
        """Re-enable IRIS reporting."""
        self._enabled = True


# Singleton instance for convenience
_client: Optional[IRISClient] = None


def get_iris_client() -> IRISClient:
    """Get singleton IRIS client instance."""
    global _client
    if _client is None:
        _client = IRISClient()
    return _client


# Convenience functions
def send_healthcheck(status: str, message: str, metrics: Dict[str, Any], **kwargs) -> bool:
    """Send healthcheck using default client."""
    return get_iris_client().send_healthcheck(status, message, metrics, **kwargs)


def send_alert(severity: str, message: str, **kwargs) -> bool:
    """Send alert using default client."""
    return get_iris_client().send_alert(severity, message, **kwargs)


def send_audit(action: str, message: str, **kwargs) -> bool:
    """Send audit log using default client."""
    return get_iris_client().send_audit(action, message, **kwargs)
