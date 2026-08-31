"""Small helpers shared by more than one provider adapter — kept out of
sync_orchestrator.py (which has no provider-specific parsing left in it)
and out of any single provider's client (since both need it)."""
from datetime import datetime
from email.utils import parsedate_to_datetime


def parse_received_at(date_header: str | None) -> datetime | None:
    """Parses an RFC 2822 Date header (Gmail's shape). Falls back to None on
    a missing or malformed header rather than raising — a message with a
    bad Date header should still sync, just without a received_at."""
    if not date_header:
        return None
    try:
        return parsedate_to_datetime(date_header)
    except (TypeError, ValueError):
        return None
