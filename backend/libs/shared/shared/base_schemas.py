"""The standard error response every service returns (architecture report §9).

One shape everywhere means the frontend has a single error path instead of
guessing per service.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """`{ "error": "Not Found", "detail": "...", "code": 404, "trace_id": "..." }`"""

    error: str = Field(description="Short, human-readable class of failure")
    detail: str = Field(description="What went wrong, safe to show a user")
    code: int = Field(description="HTTP status code")
    trace_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Correlates this response with the server logs",
    )


def error_response(*, error: str, detail: str, code: int, trace_id: str | None = None) -> dict:
    payload = ErrorResponse(error=error, detail=detail, code=code, **({"trace_id": trace_id} if trace_id else {}))
    return payload.model_dump()
