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
    SandboxOverloadedException,
    TimeoutException,
)


def error_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, Mapping):
        value = payload.get("message")
        if value:
            return str(value)
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            return "; ".join(str(item) for item in errors)
        reason = payload.get("reason")
        if isinstance(reason, Mapping):
            value = reason.get("message")
            if value:
                return str(value)
            reason_errors = reason.get("errors")
            if isinstance(reason_errors, list) and reason_errors:
                return "; ".join(str(item) for item in reason_errors)
        elif reason:
            return str(reason)
        value = payload.get("error")
        if value:
            return str(value)
    return fallback


def error_code(payload: Any) -> Optional[str]:
    if isinstance(payload, Mapping):
        value = payload.get("error")
        if isinstance(value, str) and value:
            return value
    return None


def map_http_error(
    status_code: int,
    payload: Any,
    fallback: str,
    *,
    resource: Optional[str] = None,
) -> Exception:
    code = error_code(payload)
    message = error_message(payload, fallback)

    if code == "sandbox_overloaded":
        return SandboxOverloadedException(message)
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
