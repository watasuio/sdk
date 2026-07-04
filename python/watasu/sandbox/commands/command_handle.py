from dataclasses import dataclass
from typing import Optional

from watasu.exceptions import SandboxException

Stdout = str
Stderr = str
PtyOutput = bytes


@dataclass
class PtySize:
    rows: int
    cols: int


@dataclass
class CommandResult:
    stderr: str
    stdout: str
    exit_code: int
    error: Optional[str]
    # True when the runtime dropped stream output that could not be replayed
    # (retention evicted the events before the SDK could backfill them).
    truncated: bool = False


@dataclass
class CommandExitException(SandboxException, CommandResult):
    def __str__(self):
        message = f"Command exited with code {self.exit_code}"
        if self.error:
            message += f" ({self.error})"
        if self.truncated:
            message += "; some process output was dropped before delivery"
        return f"{message} and error:\n{self.stderr}"
