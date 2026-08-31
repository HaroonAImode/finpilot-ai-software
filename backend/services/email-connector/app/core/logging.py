"""
Structured logging configuration with automatic secret redaction.

Same convention as slack-connector's core/logging.py, patterns swapped for
Google OAuth token shapes. A mailbox connector must never log message
subjects or bodies either — see docs/email-connector-plan.md §9 — so callers
should pass only ids/counts in `extra`, never raw email content.
"""
import logging
import re


class SecretRedactingFormatter(logging.Formatter):
    """Redacts sensitive values from log records before they are written."""

    PATTERNS = [
        # Google OAuth access tokens (ya29....) and refresh tokens (1//...)
        (re.compile(r'ya29\.[^\s"\']+'), '[REDACTED_GOOGLE_ACCESS_TOKEN]'),
        (re.compile(r'1//[^\s"\']+'), '[REDACTED_GOOGLE_REFRESH_TOKEN]'),
        # Bearer tokens in URLs/headers
        (re.compile(r'Bearer\s+[A-Za-z0-9\-_.~+/]+=*', re.IGNORECASE), 'Bearer [REDACTED_TOKEN]'),
        # API keys and secrets (generic patterns)
        (re.compile(r'"secret"\s*:\s*"([^"]+)"', re.IGNORECASE), '"secret": "[REDACTED]"'),
        (re.compile(r'"token"\s*:\s*"([^"]+)"', re.IGNORECASE), '"token": "[REDACTED]"'),
        (re.compile(r'"password"\s*:\s*"([^"]+)"', re.IGNORECASE), '"password": "[REDACTED]"'),
        (re.compile(r'"client_secret"\s*:\s*"([^"]+)"', re.IGNORECASE), '"client_secret": "[REDACTED]"'),
        (re.compile(r'"access_key"\s*:\s*"([^"]+)"', re.IGNORECASE), '"access_key": "[REDACTED]"'),
        (re.compile(r'"aws_secret"\s*:\s*"([^"]+)"', re.IGNORECASE), '"aws_secret": "[REDACTED]"'),
        (re.compile(r'Authorization\s*:\s*[^\s]+', re.IGNORECASE), 'Authorization: [REDACTED]'),
    ]

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        for pattern, replacement in self.PATTERNS:
            msg = pattern.sub(replacement, msg)
        return msg


def configure_logging(log_level: str = "INFO") -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(
        SecretRedactingFormatter(fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    root_logger.addHandler(console_handler)

    logging.info("Structured logging configured", extra={"event": "logging_configured", "level": log_level})
