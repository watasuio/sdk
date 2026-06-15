from __future__ import annotations

import asyncio
import inspect
from typing import Callable, Dict, List, Optional, Union

from watasu.connection_config import ApiParams, ConnectionConfig, Username
from watasu.exceptions import InvalidArgumentException, SandboxException
from watasu.sandbox.commands.command_handle import CommandResult, PtyOutput, Stderr, Stdout
from watasu.sandbox.commands.main import ProcessInfo
from watasu.sandbox.filesystem.filesystem import EntryInfo, WriteInfo
from watasu.sandbox.sandbox_api import SandboxInfo, SandboxMetrics, SnapshotInfo
from watasu.sandbox_sync.commands.command_handle import CommandHandle
from watasu.sandbox_sync.main import Sandbox
from watasu.sandbox_sync.paginator import SandboxPaginator
from watasu.stubs import unsupported


class _AsyncDualMethod:
    def __init__(self, instance_func, class_func):
        self.instance_func = instance_func
        self.class_func = class_func

    def __get__(self, obj, cls):
        if obj is None:
            return self.class_func.__get__(cls, cls)
        return self.instance_func.__get__(obj, cls)


class AsyncCommandHandle:
    """Async wrapper for a running Watasu command stream."""

    @property
    def pid(self):
        """Command process ID."""
        return self._handle.pid

    @property
    def stdout(self) -> str:
        """Captured stdout received so far."""
        return self._handle._stdout

    @property
    def stderr(self) -> str:
        """Captured stderr received so far."""
        return self._handle._stderr

    @property
    def error(self) -> Optional[str]:
        """Command execution error once the command has exited."""
        if self._handle._result is None:
            return None
        return self._handle._result.error

    @property
    def exit_code(self) -> Optional[int]:
        """Command exit code once the command has exited."""
        if self._handle._result is None:
            return None
        return self._handle._result.exit_code

    def __init__(self, handle: CommandHandle):
        self._handle = handle

    async def wait(
        self,
        on_pty: Optional[Callable[[PtyOutput], None]] = None,
        on_stdout: Optional[Callable[[Stdout], None]] = None,
        on_stderr: Optional[Callable[[Stderr], None]] = None,
    ) -> CommandResult:
        """Wait for process exit and return captured stdout/stderr."""
        loop = asyncio.get_running_loop()
        return await asyncio.to_thread(
            self._handle.wait,
            on_pty=_thread_callback(loop, on_pty),
            on_stdout=_thread_callback(loop, on_stdout),
            on_stderr=_thread_callback(loop, on_stderr),
        )

    async def kill(self) -> bool:
        """Kill the process behind this handle."""
        return await asyncio.to_thread(self._handle.kill)

    async def send_stdin(
        self, data: Union[str, bytes], request_timeout: Optional[float] = None
    ) -> None:
        """Send stdin bytes or text to the process."""
        await asyncio.to_thread(self._handle.send_stdin, data, request_timeout)

    async def close_stdin(self, request_timeout: Optional[float] = None) -> None:
        """Close stdin for the process."""
        await asyncio.to_thread(self._handle.close_stdin, request_timeout)

    async def disconnect(self) -> None:
        """Close the local stream attachment without killing the process."""
        await asyncio.to_thread(self._handle.disconnect)


class AsyncCommands:
    """Async command runner for Watasu sandboxes."""

    def __init__(self, commands):
        self._commands = commands

    async def list(self, request_timeout: Optional[float] = None) -> List[ProcessInfo]:
        """Return processes currently known by the sandbox runtime."""
        return await asyncio.to_thread(
            self._commands.list, request_timeout=request_timeout
        )

    async def kill(self, pid, request_timeout: Optional[float] = None) -> bool:
        """Send ``SIGKILL`` to a sandbox process by pid."""
        return await asyncio.to_thread(
            self._commands.kill, pid, request_timeout=request_timeout
        )

    async def send_stdin(
        self, pid, data: Union[str, bytes], request_timeout: Optional[float] = None
    ) -> None:
        """Attach to a process and send stdin bytes or text."""
        await asyncio.to_thread(
            self._commands.send_stdin, pid, data, request_timeout=request_timeout
        )

    async def connect(
        self,
        pid,
        timeout: Optional[float] = 60,
        request_timeout: Optional[float] = None,
    ) -> AsyncCommandHandle:
        """Reconnect to a live process stream by pid."""
        handle = await asyncio.to_thread(
            self._commands.connect,
            pid,
            timeout=timeout,
            request_timeout=request_timeout,
        )
        return AsyncCommandHandle(handle)

    async def run(
        self,
        cmd: str,
        background: Optional[bool] = None,
        envs: Optional[Dict[str, str]] = None,
        user: Optional[Username] = None,
        cwd: Optional[str] = None,
        on_stdout: Optional[Callable[[Stdout], None]] = None,
        on_stderr: Optional[Callable[[Stderr], None]] = None,
        stdin: Optional[bool] = None,
        timeout: Optional[float] = 60,
        request_timeout: Optional[float] = None,
    ):
        """Run a shell command asynchronously."""
        loop = asyncio.get_running_loop()
        result = await asyncio.to_thread(
            self._commands.run,
            cmd,
            background=background,
            envs=envs,
            user=user,
            cwd=cwd,
            on_stdout=_thread_callback(loop, on_stdout),
            on_stderr=_thread_callback(loop, on_stderr),
            stdin=stdin,
            timeout=timeout,
            request_timeout=request_timeout,
        )
        if background:
            return AsyncCommandHandle(result)
        return result


class AsyncFilesystem:
    """Async filesystem helper for Watasu sandboxes."""

    def __init__(self, files):
        self._files = files

    async def read(self, *args, **kwargs):
        """Read a file as text, bytes, or stream."""
        return await asyncio.to_thread(self._files.read, *args, **kwargs)

    async def write(self, *args, **kwargs) -> WriteInfo:
        """Write bytes, text, or a file-like object."""
        return await asyncio.to_thread(self._files.write, *args, **kwargs)

    async def list(self, *args, **kwargs) -> List[EntryInfo]:
        """List directory entries."""
        return await asyncio.to_thread(self._files.list, *args, **kwargs)

    async def exists(self, *args, **kwargs) -> bool:
        """Return whether a file or directory exists."""
        return await asyncio.to_thread(self._files.exists, *args, **kwargs)

    async def get_info(self, *args, **kwargs) -> EntryInfo:
        """Return stat metadata for a file or directory."""
        return await asyncio.to_thread(self._files.get_info, *args, **kwargs)

    async def remove(self, *args, **kwargs) -> None:
        """Remove a file."""
        await asyncio.to_thread(self._files.remove, *args, **kwargs)

    async def rename(self, *args, **kwargs) -> EntryInfo:
        """Move or rename a file."""
        return await asyncio.to_thread(self._files.rename, *args, **kwargs)

    async def make_dir(self, *args, **kwargs) -> bool:
        """Create a directory."""
        return await asyncio.to_thread(self._files.make_dir, *args, **kwargs)

    async def watch_dir(self, *args, **kwargs):
        """Directory watching is not implemented yet."""
        unsupported("sandbox.files.watch_dir")


class AsyncSnapshotPaginator:
    """Async one-page paginator for Watasu checkpoint snapshots."""

    def __init__(self, load_items):
        self._load_items = load_items
        self._items: Optional[List[SnapshotInfo]] = None
        self._iter_items = None

    async def list_items(self) -> List[SnapshotInfo]:
        """Return all snapshot entries."""
        if self._items is None:
            paginator = await asyncio.to_thread(self._load_items)
            self._items = paginator.list_items()
        return list(self._items)

    async def next_items(self) -> List[SnapshotInfo]:
        """Alias for ``list_items``."""
        return await self.list_items()

    def __aiter__(self):
        self._iter_items = None
        return self

    async def __anext__(self):
        if self._iter_items is None:
            self._iter_items = iter(await self.list_items())
        try:
            return next(self._iter_items)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class AsyncSandbox:
    """Async Watasu sandbox with sync and async helpers."""

    default_template = Sandbox.default_template

    @property
    def sandbox_id(self):
        """Sandbox identifier."""
        return self._sync.sandbox_id

    @property
    def files(self) -> AsyncFilesystem:
        """Async filesystem helper."""
        return self._files

    @property
    def commands(self) -> AsyncCommands:
        """Async command runner."""
        return self._commands

    @property
    def pty(self):
        unsupported("sandbox.pty")

    @property
    def git(self):
        unsupported("sandbox.git")

    def __init__(
        self,
        sandbox_id: Optional[str] = None,
        *,
        connection_config: Optional[ConnectionConfig] = None,
        session: Optional[Dict] = None,
        sandbox: Optional[Dict] = None,
        sync_sandbox: Optional[Sandbox] = None,
        **opts: ApiParams,
    ) -> None:
        if sync_sandbox is not None:
            self._set_sync(sync_sandbox)
            return
        if sandbox_id is None or connection_config is None or session is None:
            raise InvalidArgumentException(
                "Use 'await AsyncSandbox.create(...)' or 'await AsyncSandbox.connect(...)'"
            )
        self._set_sync(
            Sandbox(
                sandbox_id,
                connection_config=connection_config,
                session=session,
                sandbox=sandbox,
                **opts,
            )
        )

    @classmethod
    async def create(cls, *args, **kwargs) -> "AsyncSandbox":
        """Create a sandbox and return it with async helpers ready."""
        return cls(sync_sandbox=await asyncio.to_thread(Sandbox.create, *args, **kwargs))

    async def _connect_instance(
        self, timeout: Optional[int] = None, **opts: ApiParams
    ) -> "AsyncSandbox":
        """Reconnect this sandbox and refresh its data-plane session."""
        await asyncio.to_thread(self._sync.connect, timeout=timeout, **opts)
        self._set_sync(self._sync)
        return self

    @classmethod
    async def _connect_class(
        cls, sandbox_id: str, timeout: Optional[int] = None, **opts: ApiParams
    ) -> "AsyncSandbox":
        """Connect to an existing sandbox by id."""
        return cls(
            sync_sandbox=await asyncio.to_thread(
                Sandbox.connect, sandbox_id, timeout=timeout, **opts
            )
        )

    connect = _AsyncDualMethod(_connect_instance, _connect_class)

    async def is_running(self, request_timeout: Optional[float] = None) -> bool:
        """Return whether this sandbox is in a runtime-active lifecycle state."""
        return await asyncio.to_thread(
            self._sync.is_running, request_timeout=request_timeout
        )

    async def _kill_instance(self, **opts: ApiParams) -> bool:
        """Destroy this sandbox."""
        return await asyncio.to_thread(self._sync.kill, **opts)

    @classmethod
    async def _kill_class(cls, sandbox_id: str, **opts: ApiParams) -> bool:
        """Destroy a sandbox by id."""
        return await asyncio.to_thread(Sandbox.kill, sandbox_id, **opts)

    kill = _AsyncDualMethod(_kill_instance, _kill_class)

    async def _set_timeout_instance(self, timeout: int, **opts: ApiParams) -> None:
        """Set this sandbox's remaining lifetime in seconds."""
        await asyncio.to_thread(self._sync.set_timeout, timeout, **opts)

    @classmethod
    async def _set_timeout_class(
        cls, sandbox_id: str, timeout: int, **opts: ApiParams
    ) -> None:
        """Set a sandbox's remaining lifetime in seconds."""
        await asyncio.to_thread(Sandbox.set_timeout, sandbox_id, timeout, **opts)

    set_timeout = _AsyncDualMethod(_set_timeout_instance, _set_timeout_class)

    async def _get_info_instance(self, **opts: ApiParams) -> SandboxInfo:
        """Fetch the latest control-plane metadata for this sandbox."""
        return await asyncio.to_thread(self._sync.get_info, **opts)

    @classmethod
    async def _get_info_class(cls, sandbox_id: str, **opts: ApiParams) -> SandboxInfo:
        """Fetch control-plane metadata for a sandbox by id."""
        return await asyncio.to_thread(Sandbox.get_info, sandbox_id, **opts)

    get_info = _AsyncDualMethod(_get_info_instance, _get_info_class)

    async def _get_metrics_instance(self, **opts: ApiParams) -> List[SandboxMetrics]:
        """Fetch latest sandbox metrics."""
        return await asyncio.to_thread(self._sync.get_metrics, **opts)

    @classmethod
    async def _get_metrics_class(
        cls, sandbox_id: str, **opts: ApiParams
    ) -> List[SandboxMetrics]:
        """Fetch sandbox metrics by id."""
        return await asyncio.to_thread(Sandbox.get_metrics, sandbox_id, **opts)

    get_metrics = _AsyncDualMethod(_get_metrics_instance, _get_metrics_class)

    async def _create_snapshot_instance(self, *args, **kwargs) -> SnapshotInfo:
        """Create a Watasu checkpoint using snapshot naming."""
        return await asyncio.to_thread(self._sync.create_snapshot, *args, **kwargs)

    @classmethod
    async def _create_snapshot_class(
        cls, sandbox_id: str, *args, **kwargs
    ) -> SnapshotInfo:
        """Create a Watasu checkpoint by sandbox id."""
        return await asyncio.to_thread(
            Sandbox.create_snapshot, sandbox_id, *args, **kwargs
        )

    create_snapshot = _AsyncDualMethod(
        _create_snapshot_instance, _create_snapshot_class
    )

    async def checkpoint(self, *args, **kwargs) -> SnapshotInfo:
        """Watasu-native alias for ``create_snapshot``."""
        return await self.create_snapshot(*args, **kwargs)

    def _list_snapshots_instance(self, **opts: ApiParams) -> AsyncSnapshotPaginator:
        """List checkpoints for this sandbox using snapshot naming."""
        return AsyncSnapshotPaginator(lambda: self._sync.list_snapshots(**opts))

    @classmethod
    def _list_snapshots_class(
        cls, sandbox_id: str, **opts: ApiParams
    ) -> AsyncSnapshotPaginator:
        """List checkpoints for a sandbox by id."""
        return AsyncSnapshotPaginator(lambda: Sandbox.list_snapshots(sandbox_id, **opts))

    list_snapshots = _AsyncDualMethod(_list_snapshots_instance, _list_snapshots_class)

    async def restore(self, *args, **kwargs) -> SandboxInfo:
        """Restore a checkpoint into a new sandbox and return its metadata."""
        return await asyncio.to_thread(self._sync.restore, *args, **kwargs)

    @staticmethod
    async def list(**opts: ApiParams) -> SandboxPaginator[SandboxInfo]:
        """List sandboxes visible to the configured API token."""
        return await asyncio.to_thread(Sandbox.list, **opts)

    def get_host(self, port: int) -> str:
        """Return the public hostname for an exposed sandbox port."""
        return self._sync.get_host(port)

    async def update_network(self, *args, **kwargs):
        unsupported("Sandbox.update_network")

    async def pause(self, *args, **kwargs) -> bool:
        unsupported("Sandbox.pause")

    beta_pause = pause

    async def resume(self, *args, **kwargs) -> bool:
        unsupported("Sandbox.resume")

    async def delete_snapshot(self, *args, **kwargs):
        unsupported("Sandbox.delete_snapshot")

    async def __aenter__(self):
        """Enter an async context manager without changing sandbox state."""
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        """Destroy the sandbox when leaving an async context manager."""
        await self.kill()

    def _set_sync(self, sandbox: Sandbox) -> None:
        self._sync = sandbox
        self._files = AsyncFilesystem(sandbox.files)
        self._commands = AsyncCommands(sandbox.commands)


def _thread_callback(loop, callback):
    if callback is None:
        return None

    def wrapped(value):
        result = callback(value)
        if inspect.isawaitable(result):
            asyncio.run_coroutine_threadsafe(result, loop).result()

    return wrapped


__all__ = [
    "AsyncCommandHandle",
    "AsyncCommands",
    "AsyncFilesystem",
    "AsyncSandbox",
    "AsyncSnapshotPaginator",
]
