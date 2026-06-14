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


@dataclass
class CommandExitException(SandboxException, CommandResult):
    def __str__(self):
        return f"Command exited with code {self.exit_code} and error:\n{self.stderr}"
