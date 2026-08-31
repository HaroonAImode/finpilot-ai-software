"""Base exception types shared across services."""


class FinPilotError(Exception):
    """Root of every deliberate, expected error a service raises."""

    status_code = 500
    error = "Internal Server Error"


class NotFoundError(FinPilotError):
    status_code = 404
    error = "Not Found"


class UnauthorizedError(FinPilotError):
    status_code = 401
    error = "Unauthorized"


class ForbiddenError(FinPilotError):
    status_code = 403
    error = "Forbidden"


class ConflictError(FinPilotError):
    status_code = 409
    error = "Conflict"


class RateLimitedError(FinPilotError):
    status_code = 429
    error = "Too Many Requests"
