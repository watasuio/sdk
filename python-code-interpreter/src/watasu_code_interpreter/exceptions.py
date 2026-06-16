from watasu import TimeoutException


def format_request_timeout_error() -> Exception:
    """Build the standard request-timeout exception."""
    return TimeoutException(
        "Request timed out - the 'request_timeout' option can be used to increase this timeout"
    )


def format_execution_timeout_error() -> Exception:
    """Build the standard code-execution-timeout exception."""
    return TimeoutException(
        "Execution timed out - the 'timeout' option can be used to increase this timeout"
    )


__all__ = ["format_execution_timeout_error", "format_request_timeout_error"]
