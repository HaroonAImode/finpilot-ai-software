"""Structured logging. Unlike email-connector's equivalent, there is no
OAuth token traffic here to redact — but extracted invoice content (vendor
names, amounts, line items) is still real business data, and route handlers
must only ever log ids/counts/status, never a field's actual extracted
value, matching the same "log metadata, not content" principle.
"""
import logging


def configure_logging(log_level: str = "INFO") -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    root_logger.addHandler(console_handler)

    logging.info("Structured logging configured")
