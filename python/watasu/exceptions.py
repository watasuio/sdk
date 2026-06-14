class SandboxException(Exception):
    """Base class for sandbox errors."""


class TimeoutException(SandboxException):
    """Raised when a sandbox or request times out."""


class InvalidArgumentException(SandboxException):
    """Raised when an invalid argument is provided."""


class NotEnoughSpaceException(SandboxException):
    """Raised when there is not enough disk space."""


class NotFoundException(SandboxException):
    """Raised when a resource is not found."""


class FileNotFoundException(NotFoundException):
    """Raised when a file or directory is not found inside a sandbox."""


class SandboxNotFoundException(NotFoundException):
    """Raised when a sandbox is not found."""


class AuthenticationException(Exception):
    """Raised when authentication fails."""


class GitAuthException(AuthenticationException):
    """Raised when git authentication fails."""


class GitUpstreamException(SandboxException):
    """Raised when git upstream tracking is missing."""


class TemplateException(SandboxException):
    """Raised when template operations fail."""


class RateLimitException(SandboxException):
    """Raised when the API rate limit is exceeded."""


class BuildException(Exception):
    """Raised when a template build fails."""


class FileUploadException(BuildException):
    """Raised when file upload fails."""


class VolumeException(Exception):
    """Base class for volume errors."""


def format_request_timeout_error() -> Exception:
    return TimeoutException(
        "Request timed out - the 'request_timeout' option can be used to increase this timeout"
    )


def format_sandbox_timeout_exception(message: str):
    return TimeoutException(
        f"{message}: This error is likely due to sandbox timeout. You can modify the sandbox timeout by passing 'timeout' when starting the sandbox or calling '.set_timeout' on the sandbox with the desired timeout."
    )
