"""
Discord Webhook Alerts
EPIC-D013: Platform Health Monitoring Service (Project Hygieia)
GOV-ENV-001: Environment-aware configuration

Sends health alerts to Discord channels via webhooks.

Usage:
    from utils.discord_alerts import send_alert, AlertLevel

    send_alert(
        title="Signal Pipeline Alert",
        message="XAU_USD data is stale",
        level=AlertLevel.WARNING,
        fields={"Last Update": "2 hours ago"}
    )
"""

import sys
import json
import logging
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional
from enum import Enum
from pathlib import Path

# Setup paths
UTILS_DIR = Path(__file__).parent.absolute()
BASE_DIR = UTILS_DIR.parent
sys.path.insert(0, str(BASE_DIR))

from env_config import get_env, ENV

logger = logging.getLogger(__name__)


class AlertLevel(Enum):
    """Alert severity levels with Discord colors."""
    INFO = 0x3498db      # Blue
    SUCCESS = 0x2ecc71   # Green
    WARNING = 0xf39c12   # Orange
    CRITICAL = 0xe74c3c  # Red


# Discord webhook URLs (environment-aware)
DISCORD_WEBHOOKS = {
    'DEV': get_env('DISCORD_WEBHOOK_DEV',
        'https://discord.com/api/webhooks/1457680766430613656/GzNc0lcvepYFrMa64_h34YxvAL_kSQIX6pzBwGwo3dy0g9s3ybwCYysMokoenqczYR6i'),
    'PROD': get_env('DISCORD_WEBHOOK_PROD',
        'https://discord.com/api/webhooks/1457680766430613656/GzNc0lcvepYFrMa64_h34YxvAL_kSQIX6pzBwGwo3dy0g9s3ybwCYysMokoenqczYR6i'),
}


def get_webhook_url() -> str:
    """Get webhook URL for current environment."""
    return DISCORD_WEBHOOKS.get(ENV, DISCORD_WEBHOOKS['DEV'])


def send_alert(
    title: str,
    message: str,
    level: AlertLevel = AlertLevel.INFO,
    fields: Dict[str, str] = None,
    webhook_url: str = None,
    mention_role: str = None,
    footer: str = None
) -> bool:
    """
    Send alert to Discord webhook.

    Args:
        title: Alert title
        message: Alert message body
        level: Alert severity (INFO, SUCCESS, WARNING, CRITICAL)
        fields: Optional dict of field name -> value pairs
        webhook_url: Override webhook URL
        mention_role: Discord role ID to mention (e.g., "1234567890")
        footer: Optional footer text

    Returns:
        True if sent successfully
    """
    url = webhook_url or get_webhook_url()

    if not url:
        logger.warning("No Discord webhook URL configured")
        return False

    # Build embed
    embed = {
        "title": title,
        "description": message,
        "color": level.value,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {
            "text": footer or f"Signal Service [{ENV}] - Project Hygieia"
        }
    }

    # Add fields
    if fields:
        embed["fields"] = [
            {"name": name, "value": str(value), "inline": True}
            for name, value in fields.items()
        ]

    # Build payload
    payload = {"embeds": [embed]}

    # Add role mention if specified
    if mention_role:
        payload["content"] = f"<@&{mention_role}>"

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=10
        )

        if response.status_code == 204:
            logger.info(f"Discord alert sent: {title}")
            return True
        else:
            logger.error(f"Discord webhook failed: {response.status_code} - {response.text}")
            return False

    except requests.RequestException as e:
        logger.error(f"Discord webhook error: {e}")
        return False


def send_health_report(
    overall_status: str,
    instruments: Dict[str, dict],
    issues: List[str],
    backfill_recommended: List[str],
    webhook_url: str = None
) -> bool:
    """
    Send health check report to Discord.

    Args:
        overall_status: HEALTHY, WARNING, or CRITICAL
        instruments: Dict of instrument -> health info
        issues: List of issue strings
        backfill_recommended: List of instruments needing backfill

    Returns:
        True if sent successfully
    """
    # Determine alert level
    level_map = {
        'HEALTHY': AlertLevel.SUCCESS,
        'WARNING': AlertLevel.WARNING,
        'CRITICAL': AlertLevel.CRITICAL
    }
    level = level_map.get(overall_status, AlertLevel.INFO)

    # Build status icons
    status_icons = {'HEALTHY': ':white_check_mark:', 'WARNING': ':warning:', 'CRITICAL': ':x:'}
    icon = status_icons.get(overall_status, ':question:')

    # Build instrument summary
    healthy_count = sum(1 for i in instruments.values() if i.get('status') == 'HEALTHY')
    warning_count = sum(1 for i in instruments.values() if i.get('status') == 'WARNING')
    critical_count = sum(1 for i in instruments.values() if i.get('status') == 'CRITICAL')

    # Build message
    message = f"{icon} **Overall Status: {overall_status}**\n\n"
    message += f":chart_with_upwards_trend: Instruments: {healthy_count} healthy"
    if warning_count:
        message += f", {warning_count} warning"
    if critical_count:
        message += f", {critical_count} critical"

    # Add issue summary
    if issues:
        message += f"\n\n:exclamation: **Issues ({len(issues)})**:\n"
        for issue in issues[:5]:  # Limit to first 5
            message += f"- {issue}\n"
        if len(issues) > 5:
            message += f"_...and {len(issues) - 5} more_"

    # Fields
    fields = {
        "Environment": ENV,
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    }

    if backfill_recommended:
        fields["Backfill Needed"] = ", ".join(backfill_recommended[:5])
        if len(backfill_recommended) > 5:
            fields["Backfill Needed"] += f" (+{len(backfill_recommended) - 5} more)"

    return send_alert(
        title="Signal Health Check Report",
        message=message,
        level=level,
        fields=fields,
        webhook_url=webhook_url
    )


def send_gap_alert(
    instrument: str,
    gap_start: datetime,
    gap_end: datetime,
    gap_minutes: int,
    webhook_url: str = None
) -> bool:
    """
    Send data gap alert to Discord.

    Args:
        instrument: Instrument with gap
        gap_start: Gap start timestamp
        gap_end: Gap end timestamp
        gap_minutes: Gap duration in minutes

    Returns:
        True if sent successfully
    """
    return send_alert(
        title=f"Data Gap Detected: {instrument}",
        message=f"Missing data detected for **{instrument}**",
        level=AlertLevel.WARNING,
        fields={
            "Gap Start": gap_start.strftime("%Y-%m-%d %H:%M"),
            "Gap End": gap_end.strftime("%Y-%m-%d %H:%M"),
            "Duration": f"{gap_minutes} minutes"
        },
        webhook_url=webhook_url
    )


def send_stale_alert(
    instrument: str,
    last_update: datetime,
    age_minutes: float,
    webhook_url: str = None
) -> bool:
    """
    Send stale data alert to Discord.

    Args:
        instrument: Instrument with stale data
        last_update: Last data timestamp
        age_minutes: Age in minutes

    Returns:
        True if sent successfully
    """
    return send_alert(
        title=f"Stale Data: {instrument}",
        message=f"Data for **{instrument}** is stale",
        level=AlertLevel.WARNING if age_minutes < 60 else AlertLevel.CRITICAL,
        fields={
            "Last Update": last_update.strftime("%Y-%m-%d %H:%M"),
            "Age": f"{age_minutes:.0f} minutes"
        },
        webhook_url=webhook_url
    )


def send_service_alert(
    service_name: str,
    status: str,
    message: str,
    webhook_url: str = None
) -> bool:
    """
    Send service status alert to Discord.

    Args:
        service_name: Name of service
        status: UP, DOWN, DEGRADED
        message: Status message

    Returns:
        True if sent successfully
    """
    level_map = {
        'UP': AlertLevel.SUCCESS,
        'DEGRADED': AlertLevel.WARNING,
        'DOWN': AlertLevel.CRITICAL
    }

    return send_alert(
        title=f"Service Status: {service_name}",
        message=message,
        level=level_map.get(status, AlertLevel.INFO),
        fields={
            "Service": service_name,
            "Status": status
        },
        webhook_url=webhook_url
    )


# Test function
if __name__ == "__main__":
    # Test alert
    print(f"Testing Discord alerts for environment: {ENV}")

    success = send_alert(
        title="Test Alert",
        message="This is a test alert from the Signal Service healthcheck system.",
        level=AlertLevel.INFO,
        fields={
            "Environment": ENV,
            "Test": "Successful"
        }
    )

    print(f"Alert sent: {success}")
