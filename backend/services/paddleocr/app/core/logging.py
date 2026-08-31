"""Structured logging — same shape as ai-engine's own app/core/logging.py.
OCR'd text is real business document content; route handlers must only
ever log ids/counts/status/timings, never a recognized text value itself.
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
