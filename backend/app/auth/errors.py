"""Stable auth error taxonomy (M-02).

All auth failures surface through ``AuthError`` subclasses with a stable
machine-readable ``code``. Cross-user access is reported as ``NotFoundError``
(404) so resources never reveal whether they exist.
"""

from __future__ import annotations


class AuthError(Exception):
    code: str = "AUTH_ERROR"
    status_code: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class AuthenticationRequiredError(AuthError):
    code = "AUTH_REQUIRED"
    status_code = 401


class InvalidCredentialsError(AuthError):
    code = "INVALID_CREDENTIALS"
    status_code = 401


class EmailTakenError(AuthError):
    code = "EMAIL_TAKEN"
    status_code = 409


class RateLimitedError(AuthError):
    code = "RATE_LIMITED"
    status_code = 429


class NotFoundError(AuthError):
    code = "NOT_FOUND"
    status_code = 404
