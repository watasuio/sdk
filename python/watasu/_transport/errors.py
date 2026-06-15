from __future__ import annotations

from typing import Any, Mapping, Optional

from watasu.exceptions import (
    AuthenticationException,
    ConflictException,
    FileNotFoundException,
    InvalidArgumentException,
    NotEnoughSpaceException,
    NotFoundException,
    RateLimitException,
    SandboxException,
    SandboxNotFoundException,
    TimeoutException,
)


def error_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, Mapping):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            return "; ".join(str(item) for item in errors)
        for key in ("message", "reason", "error"):
            value = payload.get(key)
            if value:
                return str(value)
    return fallback


def map_http_error(
    status_code: int,
    payload: Any,
    fallback: str,
    *,
    resource: Optional[str] = None,
) -> Exception:
    message = error_message(payload, fallback)

    if status_code in {401, 403}:
        return AuthenticationException(message)
    if status_code == 404:
        if resource in {"file", "directory"}:
            return FileNotFoundException(message)
        if resource == "sandbox":
            return SandboxNotFoundException(message)
        return NotFoundException(message)
    if status_code == 409:
        return ConflictException(message)
    if status_code in {408, 504}:
        return TimeoutException(message)
    if status_code == 429:
        return RateLimitException(message)
    if status_code == 507:
        return NotEnoughSpaceException(message)
    if status_code in {400, 413, 422}:
        return InvalidArgumentException(message)
    return SandboxException(message)
