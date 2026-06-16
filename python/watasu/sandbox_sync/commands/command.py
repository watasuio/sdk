from __future__ import annotations

from typing import Callable, Dict, List, Literal, Optional, Union, overload

from watasu._transport.data_plane import DataPlaneClient
from watasu._transport.process_ws import (
    ProcessEventStream,
    ProcessSocket,
    QueuedProcessEventStream,
)
from watasu.connection_config import ConnectionConfig, Username
from watasu.sandbox.commands.main import ProcessInfo
from watasu.sandbox_sync.commands.command_handle import CommandHandle


class Commands:
    """Command runner for a sandbox data-plane session.

    Commands execute through the streaming WebSocket runtime. The SDK sends
    periodic WebSocket keepalive pings so silent commands, such as long sleeps
    or build steps with no output, can keep the connection open.
    """

    def __init__(
        self,
        data_plane: DataPlaneClient,
        connection_config: ConnectionConfig,
        sandbox_envs: Optional[Dict[str, str]] = None,
    ) -> None:
        self._data_plane = data_plane
        self._connection_config = connection_config
        self._sandbox_envs = dict(sandbox_envs or {})

    def list(self, request_timeout: Optional[float] = None) -> List[ProcessInfo]:
        """Return processes currently known by the sandbox runtime."""
        payload = self._data_plane.get_json(
            "/runtime/v1/process",
            request_timeout=request_timeout,
        )
        return [_process_info(item) for item in payload.get("processes", [])]

    def kill(self, pid, request_timeout: Optional[float] = None) -> bool:
        """Send ``SIGKILL`` to a sandbox process by pid."""
        self._data_plane.post_json(
            f"/runtime/v1/process/{pid}/signal",
            json={"signal": "SIGKILL"},
            request_timeout=request_timeout,
        )
        return True

    def send_stdin(
        self, pid, data: Union[str, bytes], request_timeout: Optional[float] = None
    ):
        """Attach to a process and send stdin bytes or text."""
        handle = self.connect(pid, request_timeout=request_timeout)
        try:
            handle.send_stdin(data, request_timeout=request_timeout)
        finally:
            handle.disconnect()

    @overload
    def run(
        self,
        cmd: str,
        background: Union[Literal[False], None] = None,
        envs: Optional[Dict[str, str]] = None,
        user: Optional[Username] = None,
        cwd: Optional[str] = None,
        on_stdout: Optional[Callable[[str], None]] = None,
        on_stderr: Optional[Callable[[str], None]] = None,
        stdin: Optional[bool] = None,
        timeout: Optional[float] = 60,
        request_timeout: Optional[float] = None,
    ): ...

    @overload
    def run(
        self,
        cmd: str,
        background: Literal[True],
        envs: Optional[Dict[str, str]] = None,
        user: Optional[Username] = None,
        cwd: Optional[str] = None,
        on_stdout: None = None,
        on_stderr: None = None,
        stdin: Optional[bool] = None,
        timeout: Optional[float] = 60,
        request_timeout: Optional[float] = None,
    ) -> CommandHandle: ...

    def run(
        self,
        cmd: str,
        background: Union[bool, None] = None,
        envs: Optional[Dict[str, str]] = None,
        user: Optional[Username] = None,
        cwd: Optional[str] = None,
        on_stdout: Optional[Callable[[str], None]] = None,
        on_stderr: Optional[Callable[[str], None]] = None,
        stdin: Optional[bool] = None,
        timeout: Optional[float] = 60,
        request_timeout: Optional[float] = None,
    ):
        """Run a shell command.

        By default this waits for exit and returns ``CommandResult``. With
        ``background=True`` it returns ``CommandHandle`` immediately while the
        WebSocket remains attached.
        """
        handle = self._start(
            cmd, envs, user, cwd, stdin or False, timeout, request_timeout
        )
        if background:
            return handle
        return handle.wait(on_stdout=on_stdout, on_stderr=on_stderr)

    def connect(
        self,
        pid,
        timeout: Optional[float] = 60,
        request_timeout: Optional[float] = None,
    ) -> CommandHandle:
        """Reconnect to a live process stream by pid."""
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
                data
            ),
            handle_close_stdin=lambda request_timeout=None: socket.send_json(
                {"type": "close_stdin"}
            ),
        )

    def _start(
        self,
        cmd: str,
        envs: Optional[Dict[str, str]],
        user: Optional[Username],
        cwd: Optional[str],
        stdin: bool,
        timeout: Optional[float],
        request_timeout: Optional[float],
    ) -> CommandHandle:
        socket = ProcessSocket(
            self._data_plane.base_url,
            self._data_plane.token,
            "/runtime/v1/process",
            request_timeout=request_timeout,
            headers=self._connection_config.sandbox_headers,
        ).connect()
        environment = {**self._sandbox_envs, **dict(envs or {})}
        socket.send_json(
            {
                "type": "start",
                "cmd": "/bin/bash",
                "args": ["-l", "-c", cmd],
                "cwd": cwd,
                "user": user,
                "environment": environment,
                "envs": environment,
                "stdin": stdin,
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
                data
            ),
            handle_close_stdin=lambda request_timeout=None: socket.send_json(
                {"type": "close_stdin"}
            ),
        )


def _next_started(frames):
    for frame in frames:
        if frame.get("type") == "started":
            return frame
    raise RuntimeError("process ended before started frame")


def _process_info(payload) -> ProcessInfo:
    process = payload.get("process") if isinstance(payload, dict) else None
    item = process or payload or {}
    return ProcessInfo(
        pid=item.get("pid") or item.get("id"),
        tag=item.get("tag"),
        cmd=item.get("cmd"),
        args=list(item.get("args") or []),
        envs=dict(item.get("envs") or item.get("environment") or {}),
        cwd=item.get("cwd"),
    )
