from __future__ import annotations

from typing import Dict, Optional, Union

from watasu._transport.data_plane import DataPlaneClient
from watasu._transport.process_ws import (
    ProcessEventStream,
    ProcessSocket,
    QueuedProcessEventStream,
)
from watasu.connection_config import ConnectionConfig, Username
from watasu.exceptions import SandboxException
from watasu.sandbox.commands.command_handle import PtySize
from watasu.sandbox_sync.commands.command_handle import CommandHandle


class Pty:
    """Module for interacting with PTYs in the sandbox."""

    def __init__(
        self, data_plane: DataPlaneClient, connection_config: ConnectionConfig
    ) -> None:
        self._data_plane = data_plane
        self._connection_config = connection_config

    def create(
        self,
        size: PtySize,
        user: Optional[Username] = None,
        cwd: Optional[str] = None,
        envs: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = 60,
        request_timeout: Optional[float] = None,
    ) -> CommandHandle:
        """Start a new interactive shell PTY."""
        socket = ProcessSocket(
            self._data_plane.base_url,
            self._data_plane.token,
            "/runtime/v1/process",
            request_timeout=request_timeout,
            headers=self._connection_config.sandbox_headers,
        ).connect()
        environment = {
            "TERM": "xterm-256color",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            **dict(envs or {}),
        }
        socket.send_json(
            {
                "type": "start",
                "cmd": "/bin/bash",
                "args": ["-i", "-l"],
                "cwd": cwd,
                "user": user,
                "environment": environment,
                "envs": environment,
                "stdin": True,
                "pty": {"cols": size.cols, "rows": size.rows},
                "timeout_ms": int(timeout * 1000) if timeout else None,
            }
        )
        frames = socket.frames(timeout=timeout)
        first = _next_started(frames)
        pid = first.get("pid") or first.get("process", {}).get("pid")
        return CommandHandle(
            pid=pid,
            handle_kill=lambda: self.kill(pid),
            events=QueuedProcessEventStream(socket, first, frames),
            handle_send_stdin=lambda data, request_timeout=None: socket.send_stdin(
                data, wait_ack=False
            ),
            handle_close_stdin=lambda request_timeout=None: socket.close_stdin(
                wait_ack=False
            ),
        )

    def connect(
        self,
        pid,
        timeout: Optional[float] = 60,
        request_timeout: Optional[float] = None,
    ) -> CommandHandle:
        """Connect to a running PTY by process id."""
        socket = ProcessSocket(
            self._data_plane.base_url,
            self._data_plane.token,
            f"/runtime/v1/process/{pid}/connect?since=0",
            request_timeout=request_timeout,
            headers=self._connection_config.sandbox_headers,
        ).connect()
        frames = socket.frames(timeout=timeout)
        first = _next_started(frames)
        actual_pid = first.get("pid") or first.get("process", {}).get("pid") or pid
        return CommandHandle(
            pid=actual_pid,
            handle_kill=lambda: self.kill(actual_pid),
            events=ProcessEventStream(socket, frames),
            handle_send_stdin=lambda data, request_timeout=None: socket.send_stdin(
                data, wait_ack=True, request_timeout=request_timeout
            ),
        )

    def send_stdin(
        self, pid, data: Union[str, bytes], request_timeout: Optional[float] = None
    ) -> None:
        """Send input bytes or text to a PTY."""
        handle = self.connect(pid, request_timeout=request_timeout)
        try:
            handle.send_stdin(data, request_timeout=request_timeout)
        finally:
            handle.disconnect()

    send_input = send_stdin

    def resize(
        self, pid, size: PtySize, request_timeout: Optional[float] = None
    ) -> None:
        """Resize a running PTY."""
        handle = self.connect(pid, request_timeout=request_timeout)
        try:
            handle._events.socket.send_json(
                {"type": "resize", "cols": size.cols, "rows": size.rows}
            )
        finally:
            handle.disconnect()

    def kill(self, pid, request_timeout: Optional[float] = None) -> bool:
        """Kill a running PTY by process id."""
        self._data_plane.post_json(
            f"/runtime/v1/process/{pid}/signal",
            json={"signal": "SIGKILL"},
            request_timeout=request_timeout,
        )
        return True


def _next_started(frames):
    for frame in frames:
        if frame.get("type") == "started":
            return frame
    raise SandboxException("PTY ended before started frame")
