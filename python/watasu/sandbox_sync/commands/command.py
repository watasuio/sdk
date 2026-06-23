from __future__ import annotations

import base64
from typing import Callable, Dict, List, Literal, Optional, Union, overload
from urllib.parse import quote

from watasu._transport.data_plane import DataPlaneClient
from watasu._transport.process_ws import (
    ProcessEventStream,
    ProcessSocket,
    QueuedProcessEventStream,
)
from watasu.connection_config import ConnectionConfig, Username
from watasu.sandbox.commands.main import (
    ProcessInfo,
    ProcessOutputEvent,
    ProcessOutputSnapshot,
    ProcessStatus,
)
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
        self.stop_process(pid, signal="SIGKILL", request_timeout=request_timeout)
        return True

    def process(self, pid, request_timeout: Optional[float] = None) -> ProcessStatus:
        """Return current process status without attaching a WebSocket."""
        payload = self._data_plane.get_json(
            f"/runtime/v1/process/{_path_component(pid)}",
            request_timeout=request_timeout,
        )
        return _process_status(payload)

    def read_process_output(
        self,
        pid,
        since: int = 0,
        limit_bytes: Optional[int] = None,
        request_timeout: Optional[float] = None,
    ) -> ProcessOutputSnapshot:
        """Read currently available process output since a cursor without blocking."""
        payload = self._data_plane.get_json(
            f"/runtime/v1/process/{_path_component(pid)}/output",
            params={"since": since, "limit_bytes": limit_bytes},
            request_timeout=request_timeout,
        )
        return _process_output_snapshot(payload)

    def stop_process(
        self,
        pid,
        signal: str = "TERM",
        kill_group: bool = True,
        grace_ms: int = 0,
        request_timeout: Optional[float] = None,
    ) -> ProcessStatus:
        """Stop a process, optionally signalling the full process group."""
        payload = self._data_plane.delete_json(
            f"/runtime/v1/process/{_path_component(pid)}",
            params={
                "signal": signal,
                "kill_group": str(kill_group).lower(),
                "grace_ms": grace_ms,
            },
            request_timeout=request_timeout,
        )
        return _process_status(payload)

    def send_stdin(
        self, pid, data: Union[str, bytes], request_timeout: Optional[float] = None
    ):
        """Attach to a process and send stdin bytes or text."""
        handle = self.connect(pid, request_timeout=request_timeout)
        try:
            handle.send_stdin(data, request_timeout=request_timeout)
        finally:
            handle.disconnect()

    def close_stdin(self, pid, request_timeout: Optional[float] = None) -> None:
        """Attach to a process and close stdin, signalling EOF."""
        handle = self.connect(pid, request_timeout=request_timeout)
        try:
            handle.close_stdin(request_timeout=request_timeout)
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
        process_id: Optional[str] = None,
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
        process_id: Optional[str] = None,
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
        process_id: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ):
        """Run a shell command.

        By default this waits for exit and returns ``CommandResult``. With
        ``background=True`` it returns ``CommandHandle`` immediately while the
        WebSocket remains attached.
        """
        handle = self._start(
            cmd, envs, user, cwd, stdin or False, timeout, process_id, request_timeout
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
        return self.connect_since(pid, 0, timeout=timeout, request_timeout=request_timeout)

    def connect_since(
        self,
        pid,
        cursor: int = 0,
        timeout: Optional[float] = 60,
        request_timeout: Optional[float] = None,
    ) -> CommandHandle:
        """Reconnect to a live process stream by pid starting at a cursor."""
        socket = ProcessSocket(
            self._data_plane.base_url,
            self._data_plane.token,
            f"/runtime/v1/process/{_path_component(pid)}/connect?since={cursor}",
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
            handle_close_stdin=lambda request_timeout=None: socket.close_stdin(
                wait_ack=True, request_timeout=request_timeout
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
        process_id: Optional[str],
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
                "id": process_id,
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
                data, wait_ack=False
            ),
            handle_close_stdin=lambda request_timeout=None: socket.close_stdin(
                wait_ack=False
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
        pid=item.get("id") or item.get("pid"),
        tag=item.get("tag"),
        cmd=item.get("cmd") or item.get("command"),
        args=list(item.get("args") or []),
        envs=dict(item.get("envs") or item.get("environment") or {}),
        cwd=item.get("cwd"),
    )


def _process_status(payload) -> ProcessStatus:
    process = payload.get("process") if isinstance(payload, dict) else None
    item = process or payload or {}
    return ProcessStatus(
        pid=_value(item, "pid", "id", default=""),
        id=_value(item, "id"),
        os_pid=_value(item, "os_pid", "osPid"),
        command=_value(item, "command", "cmd"),
        args=list(_value(item, "args", "arguments", default=[]) or []),
        cwd=_value(item, "cwd", "working_directory"),
        user=item.get("user"),
        pty=item.get("pty"),
        status=_value(item, "status", default=""),
        started_at=_value(item, "started_at", "startedAt"),
        finished_at=_value(item, "finished_at", "finishedAt"),
        exit_code=_value(item, "exit_code", "exitCode"),
    )


def _process_output_snapshot(payload) -> ProcessOutputSnapshot:
    return ProcessOutputSnapshot(
        pid=_value(payload, "pid", "id", default=""),
        status=_value(payload, "status", default=""),
        exit_code=_value(payload, "exit_code", "exitCode"),
        finished_at=_value(payload, "finished_at", "finishedAt"),
        next_cursor=_value(payload, "next_cursor", "nextCursor", default=0),
        truncated_before_cursor=bool(
            payload.get("truncated_before_cursor")
            or payload.get("truncatedBeforeCursor")
        ),
        events=[_process_output_event(event) for event in payload.get("events", [])],
    )


def _process_output_event(payload) -> ProcessOutputEvent:
    return ProcessOutputEvent(
        cursor=_value(payload, "cursor", default=0),
        type=_value(payload, "type", default=""),
        data=base64.b64decode(_value(payload, "data", default=b"")),
    )


def _path_component(value) -> str:
    return quote(str(value), safe="")


def _value(mapping, *keys, default=None):
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default
