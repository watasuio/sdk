from __future__ import annotations

import base64

from typing import Callable, Iterator, Optional, Tuple, Union

from watasu.exceptions import SandboxException
from watasu.sandbox.commands.command_handle import (
    CommandExitException,
    CommandResult,
    PtyOutput,
    Stderr,
    Stdout,
)


class CommandHandle:
    """Handle for a running sandbox process stream."""

    @property
    def pid(self):
        return self._pid

    def __init__(
        self,
        pid,
        handle_kill: Callable[[], bool],
        events,
        handle_send_stdin: Optional[Callable[[Union[str, bytes], Optional[float]], None]] = None,
        handle_close_stdin: Optional[Callable[[Optional[float]], None]] = None,
    ):
        self._pid = pid
        self._handle_kill = handle_kill
        self._handle_send_stdin = handle_send_stdin
        self._handle_close_stdin = handle_close_stdin
        self._events = events
        self._stdout = ""
        self._stderr = ""
        self._result: Optional[CommandResult] = None
        self._iteration_exception: Optional[Exception] = None

    def __iter__(self):
        return self._handle_events()

    def _handle_events(self) -> Iterator[Tuple[Optional[Stdout], Optional[Stderr], Optional[PtyOutput]]]:
        try:
            for frame in self._events:
                frame_type = frame.get("type")
                if frame_type == "stdout":
                    out = _frame_data(frame)
                    self._stdout += out
                    yield out, None, None
                elif frame_type == "stderr":
                    out = _frame_data(frame)
                    self._stderr += out
                    yield None, out, None
                elif frame_type == "exit":
                    self._result = CommandResult(
                        stderr=self._stderr,
                        stdout=self._stdout,
                        exit_code=int(frame.get("exit_code") or 0),
                        error=frame.get("error"),
                    )
                    return
                elif frame_type in {"started", "ready", "pong"}:
                    continue
                elif frame_type == "error":
                    raise SandboxException(frame.get("message") or frame.get("code") or "process error")
        except Exception as error:
            raise error

    def disconnect(self) -> None:
        """Close the local WebSocket attachment without killing the process."""
        self._events.close()

    def wait(
        self,
        on_pty: Optional[Callable[[PtyOutput], None]] = None,
        on_stdout: Optional[Callable[[str], None]] = None,
        on_stderr: Optional[Callable[[str], None]] = None,
    ) -> CommandResult:
        """Wait for process exit and return captured stdout/stderr.

        Raises ``CommandExitException`` for non-zero exits while preserving the
        captured output on the exception object.
        """
        try:
            for stdout, stderr, pty in self:
                if stdout is not None and on_stdout:
                    on_stdout(stdout)
                elif stderr is not None and on_stderr:
                    on_stderr(stderr)
                elif pty is not None and on_pty:
                    on_pty(pty)
        except Exception as error:
            self._iteration_exception = error

        if self._iteration_exception:
            raise self._iteration_exception
        if self._result is None:
            raise SandboxException("Command ended without an exit event")
        if self._result.exit_code != 0:
            raise CommandExitException(
                stdout=self._stdout,
                stderr=self._stderr,
                exit_code=self._result.exit_code,
                error=self._result.error,
            )
        return self._result

    def kill(self) -> bool:
        """Kill the process behind this handle."""
        return self._handle_kill()

    def send_stdin(self, data: Union[str, bytes], request_timeout: Optional[float] = None) -> None:
        """Send stdin bytes or text to the process."""
        if self._handle_send_stdin is None:
            raise SandboxException("Sending stdin is not supported for this command handle.")
        self._handle_send_stdin(data, request_timeout)

    def close_stdin(self, request_timeout: Optional[float] = None) -> None:
        """Close the stdin stream for the process."""
        if self._handle_close_stdin is None:
            raise SandboxException("Closing stdin is not supported for this command handle.")
        self._handle_close_stdin(request_timeout)


def _frame_data(frame) -> str:
    data = frame.get("data", "")
    if isinstance(data, str):
        try:
            return base64.b64decode(data, validate=True).decode("utf-8", "replace")
        except Exception:
            return data
    if isinstance(data, bytes):
        return data.decode("utf-8", "replace")
    return str(data)
