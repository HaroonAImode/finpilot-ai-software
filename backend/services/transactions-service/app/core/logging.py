"""Structured logging configuration with automatic secret redaction — same
convention as every other service in this codebase (see
vendors-service/app/core/logging.py)."""
import logging
import re


class SecretRedactingFormatter(logging.Formatter):
    PATTERNS = [
        (re.compile(r'Bearer\s+[A-Za-z0-9\-_.~+/]+=*', re.IGNORECASE), 'Bearer [REDACTED_TOKEN]'),
        (re.compile(r'"secret"\s*:\s*"([^"]+)"', re.IGNORECASE), '"secret": "[REDACTED]"'),
        (re.compile(r'"password"\s*:\s*"([^"]+)"', re.IGNORECASE), '"password": "[REDACTED]"'),
        (re.compile(r'"access_key"\s*:\s*"([^"]+)"', re.IGNORECASE), '"access_key": "[REDACTED]"'),
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
