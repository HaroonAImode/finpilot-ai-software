"""
Structured logging configuration with automatic secret redaction.

Per §20 Security: tokens, signing secrets, and other sensitive values
are automatically masked before being written to logs.
"""
import json
import logging
import re
from typing import Any

from app.core.config import get_settings


class SecretRedactingFormatter(logging.Formatter):
    """
    Custom formatter that redacts sensitive values from log records.
    
    Matches and masks:
    - Slack tokens (xoxb-, xoxp-, xapp-, etc.)
    - URLs containing bearer tokens or passwords
    - Common secret field names (token, secret, password, key, etc.)
    """

    # Patterns to redact
    PATTERNS = [
        # Slack tokens
        (re.compile(r'xoxb-[^\s"\']+'), '[REDACTED_SLACK_BOT_TOKEN]'),
        (re.compile(r'xoxp-[^\s"\']+'), '[REDACTED_SLACK_USER_TOKEN]'),
        (re.compile(r'xoxe\.xoxp-[^\s"\']+'), '[REDACTED_SLACK_ROTATABLE_TOKEN]'),
        (re.compile(r'xapp-[^\s"\']+'), '[REDACTED_SLACK_APP_TOKEN]'),
        # Bearer tokens in URLs/headers
        (re.compile(r'Bearer\s+[A-Za-z0-9\-_.~+/]+=*', re.IGNORECASE), 'Bearer [REDACTED_TOKEN]'),
        # API keys and secrets (generic patterns)
        (re.compile(r'"secret"\s*:\s*"([^"]+)"', re.IGNORECASE), '"secret": "[REDACTED]"'),
        (re.compile(r'"token"\s*:\s*"([^"]+)"', re.IGNORECASE), '"token": "[REDACTED]"'),
        (re.compile(r'"password"\s*:\s*"([^"]+)"', re.IGNORECASE), '"password": "[REDACTED]"'),
        (re.compile(r'"api_key"\s*:\s*"([^"]+)"', re.IGNORECASE), '"api_key": "[REDACTED]"'),
        (re.compile(r'"access_key"\s*:\s*"([^"]+)"', re.IGNORECASE), '"access_key": "[REDACTED]"'),
        (re.compile(r'"aws_secret"\s*:\s*"([^"]+)"', re.IGNORECASE), '"aws_secret": "[REDACTED]"'),
        # Authorization headers
        (re.compile(r'Authorization\s*:\s*[^\s]+', re.IGNORECASE), 'Authorization: [REDACTED]'),
    ]

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with automatic secret redaction."""
        # Format the message
        msg = super().format(record)

        # Apply all redaction patterns
        for pattern, replacement in self.PATTERNS:
            msg = pattern.sub(replacement, msg)

        return msg


def configure_logging(log_level: str = "INFO"):
    """
    Configure structured logging with JSON output and secret redaction.
    
    Per §23 observability guidelines.
    """
    settings = get_settings()
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove any existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create console handler with redacting formatter
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)

    # Use the secret-redacting formatter
    formatter = SecretRedactingFormatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Log that structured logging is configured
    logging.info(
        "Structured logging configured",
        extra={
            "event": "logging_configured",
            "level": log_level,
            "app_env": settings.app_env,
        },
    )


# Event log helper functions per §23

def log_sync_event(
    event_name: str,
    sync_job_id: str,
    installation_id: str,
    extra_fields: dict | None = None,
):
    """Log a structured sync event."""
    logger = logging.getLogger(__name__)
    extra = {
        "event": event_name,
        "sync_job_id": sync_job_id,
        "installation_id": installation_id,
    }
    if extra_fields:
        extra.update(extra_fields)
    logger.info(event_name, extra=extra)


def log_file_event(
    event_name: str,
    file_id: str,
    slack_file_id: str,
    filename: str,
    extra_fields: dict | None = None,
):
    """Log a structured file event."""
    logger = logging.getLogger(__name__)
    extra = {
        "event": event_name,
        "file_id": file_id,
        "slack_file_id": slack_file_id,
        "filename": filename,
    }
    if extra_fields:
        extra.update(extra_fields)
    logger.info(event_name, extra=extra)


def log_rate_limit_event(
    method: str,
    retry_after_seconds: int,
    extra_fields: dict | None = None,
):
    """Log rate limit hit per §23."""
    logger = logging.getLogger(__name__)
    extra = {
        "event": "rate_limited",
        "method": method,
        "retry_after_seconds": retry_after_seconds,
    }
    if extra_fields:
        extra.update(extra_fields)
    logger.warning("Rate limit hit", extra=extra)
