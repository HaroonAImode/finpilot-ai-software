"""Payment status detection — only ever set from direct textual evidence.
"Not detected" must stay UNKNOWN (not_found), never silently become
PENDING or any other assumed state.
"""
import re

from invoice_extraction.labels import PAYMENT_STATUS_KEYWORDS
from invoice_extraction.schema import FieldValue, not_found

_COMPILED = [(re.compile(pattern, re.IGNORECASE), status) for pattern, status in PAYMENT_STATUS_KEYWORDS]


def detect_payment_status(text: str) -> FieldValue:
    for pattern, status in _COMPILED:
        if pattern.search(text):
            return FieldValue(value=status, confidence=1.0, method="pattern_match")
    return not_found()
