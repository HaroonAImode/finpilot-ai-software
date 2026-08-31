"""No `extra={...}` key may collide with a reserved LogRecord attribute.

Python's logging module raises KeyError when `extra` carries a name it already
uses — "filename", "module", "args" and friends. It only raises when the record
is actually built, so the failure hides completely at WARNING and appears the
moment a service runs at INFO, which is how the containers are configured.

Three calls in reconciliation.py passed `filename`, so retrying a failed
download raised KeyError in any real deployment while every unit test passed.
The static check below catches the whole class rather than the three instances.
"""
import ast
import logging
import pathlib

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[2] / "app"

RESERVED = set(
    logging.LogRecord("n", logging.INFO, "p", 1, "m", None, None).__dict__
) | {"message", "asctime"}


def _extra_keys_in_source() -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for path in APP_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "extra" and isinstance(keyword.value, ast.Dict):
                    for key in keyword.value.keys:
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            found.append((path.name, node.lineno, key.value))
    return found


def test_no_logging_extra_key_shadows_a_logrecord_attribute() -> None:
    collisions = [
        f"{name}:{line} passes reserved key '{key}'"
        for name, line, key in _extra_keys_in_source()
        if key in RESERVED
    ]
    assert not collisions, (
        "logging.extra keys must not shadow LogRecord attributes — these raise "
        "KeyError at INFO level:\n  " + "\n  ".join(collisions)
    )


def test_the_check_would_actually_catch_a_collision() -> None:
    """Guards the guard: prove Python still rejects a reserved key, so this file
    cannot quietly become a no-op if logging behaviour changes."""
    logging.getLogger("collision-probe").setLevel(logging.INFO)
    handler = logging.StreamHandler()
    logger = logging.getLogger("collision-probe")
    logger.addHandler(handler)
    try:
        with pytest.raises(KeyError):
            logger.info("probe", extra={"filename": "invoice.pdf"})
    finally:
        logger.removeHandler(handler)


def test_a_safe_key_still_works() -> None:
    logger = logging.getLogger("collision-probe")
    logger.setLevel(logging.INFO)
    logger.info("probe", extra={"file_name": "invoice.pdf", "event": "test"})
