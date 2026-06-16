from __future__ import annotations

import asyncio
import datetime
import inspect
from typing import Any, Callable, Dict, List, Optional, Union

from watasu.connection_config import ApiParams, ConnectionConfig, Username
from watasu.exceptions import InvalidArgumentException, SandboxException
from watasu.sandbox.commands.command_handle import CommandResult, PtyOutput, Stderr, Stdout
from watasu.sandbox.commands.command_handle import PtySize
from watasu.sandbox.commands.main import ProcessInfo
from watasu.sandbox.filesystem.filesystem import EntryInfo, FilesystemEvent, WriteInfo
from watasu.sandbox.sandbox_api import FileUrlInfo, SandboxInfo, SandboxMetrics, SnapshotInfo
from watasu.sandbox_sync.commands.command_handle import CommandHandle
from watasu.sandbox_sync.filesystem.watch_handle import WatchHandle
from watasu.sandbox_sync.git import GitCommandResult, GitStatus
from watasu.sandbox_sync.main import Sandbox
from watasu.sandbox_sync.paginator import SandboxPaginator


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

    def __init__(
        self,
        handle: CommandHandle,
        on_stdout: Optional[Callable[[Stdout], Any]] = None,
        on_stderr: Optional[Callable[[Stderr], Any]] = None,
        on_pty: Optional[Callable[[PtyOutput], Any]] = None,
    ):
        self._handle = handle
        self._wait_task: Optional[asyncio.Task] = None
        if on_stdout is not None or on_stderr is not None or on_pty is not None:
            loop = asyncio.get_running_loop()
            self._wait_task = asyncio.create_task(
                self._wait_with_callbacks(loop, on_stdout, on_stderr, on_pty)
            )

    async def _wait_with_callbacks(
        self,
        loop,
        on_stdout: Optional[Callable[[Stdout], Any]],
        on_stderr: Optional[Callable[[Stderr], Any]],
        on_pty: Optional[Callable[[PtyOutput], Any]],
    ) -> CommandResult:
        return await asyncio.to_thread(
            self._handle.wait,
            on_pty=_thread_callback(loop, on_pty),
            on_stdout=_thread_callback(loop, on_stdout),
            on_stderr=_thread_callback(loop, on_stderr),
        )

    async def wait(
        self,
        on_pty: Optional[Callable[[PtyOutput], Any]] = None,
        on_stdout: Optional[Callable[[Stdout], Any]] = None,
        on_stderr: Optional[Callable[[Stderr], Any]] = None,
    ) -> CommandResult:
        """Wait for process exit and return captured stdout/stderr."""
        if self._wait_task is not None:
            return await self._wait_task

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
        if self._wait_task is not None:
            self._wait_task.cancel()


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
        pid: int,
        timeout: Optional[float] = 60,
        request_timeout: Optional[float] = None,
        on_stdout: Optional[Callable[[Stdout], Any]] = None,
        on_stderr: Optional[Callable[[Stderr], Any]] = None,
    ) -> AsyncCommandHandle:
        """Reconnect to a live process stream by pid."""
        handle = await asyncio.to_thread(
            self._commands.connect,
            pid,
            timeout=timeout,
            request_timeout=request_timeout,
        )
        return AsyncCommandHandle(handle, on_stdout=on_stdout, on_stderr=on_stderr)

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
            return AsyncCommandHandle(result, on_stdout=on_stdout, on_stderr=on_stderr)
        return result


class AsyncFilesystem:
    """Async filesystem helper for Watasu sandboxes."""

    def __init__(self, files):
        self._files = files

    async def read(self, *args, **kwargs):
        """Read a file as text, bytes, or stream."""
        return await asyncio.to_thread(self._files.read, *args, **kwargs)

    async def read_bytes(self, *args, **kwargs) -> bytes:
        """Read a file as bytes."""
        return await asyncio.to_thread(self._files.read_bytes, *args, **kwargs)

    async def write(self, *args, **kwargs) -> WriteInfo:
        """Write bytes, text, or a file-like object."""
        return await asyncio.to_thread(self._files.write, *args, **kwargs)

    async def write_files(self, *args, **kwargs) -> List[WriteInfo]:
        """Write several files in one runtime API call."""
        return await asyncio.to_thread(self._files.write_files, *args, **kwargs)

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

    async def watch_dir(
        self,
        path: str,
        on_event: Optional[Callable[[FilesystemEvent], None]] = None,
        user=None,
        request_timeout: Optional[float] = None,
        recursive: bool = False,
        include_entry: bool = False,
    ) -> "AsyncWatchHandle":
        """Watch a directory for filesystem events."""
        handle = await asyncio.to_thread(
            self._files.watch_dir,
            path,
            user=user,
            request_timeout=request_timeout,
            recursive=recursive,
            include_entry=include_entry,
        )
        async_handle = AsyncWatchHandle(handle)
        if on_event is not None:
            async_handle.start_callback(on_event)
        return async_handle


class AsyncWatchHandle:
    """Async wrapper for a filesystem watcher."""

    def __init__(self, handle: WatchHandle) -> None:
        self._handle = handle
        self._task: Optional[asyncio.Task] = None

    async def stop(self) -> None:
        """Stop watching the directory."""
        await asyncio.to_thread(self._handle.stop)
        if self._task is not None:
            self._task.cancel()

    close = stop

    async def get_new_events(self) -> List[FilesystemEvent]:
        """Return queued filesystem events."""
        return await asyncio.to_thread(self._handle.get_new_events)

    def start_callback(self, on_event: Callable[[FilesystemEvent], None]) -> None:
        async def pump():
            while True:
                for event in await self.get_new_events():
                    result = on_event(event)
                    if inspect.isawaitable(result):
                        await result
                await asyncio.sleep(0.1)

        self._task = asyncio.create_task(pump())


class AsyncPty:
    """Async PTY helper."""

    def __init__(self, pty):
        self._pty = pty

    async def create(
        self,
        size: PtySize,
        on_data: Callable[[PtyOutput], Any],
        user: Optional[Username] = None,
        cwd: Optional[str] = None,
        envs: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = 60,
        request_timeout: Optional[float] = None,
    ) -> AsyncCommandHandle:
        handle = await asyncio.to_thread(
            self._pty.create,
            size,
            user=user,
            cwd=cwd,
            envs=envs,
            timeout=timeout,
            request_timeout=request_timeout,
        )
        return AsyncCommandHandle(handle, on_pty=on_data)

    async def connect(
        self,
        pid: int,
        on_data: Callable[[PtyOutput], Any],
        timeout: Optional[float] = 60,
        request_timeout: Optional[float] = None,
    ) -> AsyncCommandHandle:
        handle = await asyncio.to_thread(
            self._pty.connect,
            pid,
            timeout=timeout,
            request_timeout=request_timeout,
        )
        return AsyncCommandHandle(handle, on_pty=on_data)

    async def send_stdin(
        self, pid, data: Union[str, bytes], request_timeout: Optional[float] = None
    ) -> None:
        await asyncio.to_thread(
            self._pty.send_stdin, pid, data, request_timeout=request_timeout
        )

    send_input = send_stdin

    async def resize(
        self, pid, size: PtySize, request_timeout: Optional[float] = None
    ) -> None:
        await asyncio.to_thread(
            self._pty.resize, pid, size, request_timeout=request_timeout
        )

    async def kill(self, pid, request_timeout: Optional[float] = None) -> bool:
        return await asyncio.to_thread(
            self._pty.kill, pid, request_timeout=request_timeout
        )


class AsyncGit:
    """Async Git helper."""

    def __init__(self, git):
        self._git = git

    async def clone(self, *args, **kwargs) -> GitCommandResult:
        return await asyncio.to_thread(self._git.clone, *args, **kwargs)

    async def dangerously_authenticate(self, *args, **kwargs) -> GitCommandResult:
        return await asyncio.to_thread(self._git.dangerously_authenticate, *args, **kwargs)

    async def configure_user(self, *args, **kwargs) -> GitCommandResult:
        return await asyncio.to_thread(self._git.configure_user, *args, **kwargs)

    async def init(self, *args, **kwargs) -> GitCommandResult:
        return await asyncio.to_thread(self._git.init, *args, **kwargs)

    async def status(self, *args, **kwargs) -> GitStatus:
        return await asyncio.to_thread(self._git.status, *args, **kwargs)

    async def branches(self, *args, **kwargs):
        return await asyncio.to_thread(self._git.branches, *args, **kwargs)

    async def create_branch(self, *args, **kwargs) -> GitCommandResult:
        return await asyncio.to_thread(self._git.create_branch, *args, **kwargs)

    async def delete_branch(self, *args, **kwargs) -> GitCommandResult:
        return await asyncio.to_thread(self._git.delete_branch, *args, **kwargs)

    async def add(self, *args, **kwargs) -> GitCommandResult:
        return await asyncio.to_thread(self._git.add, *args, **kwargs)

    async def commit(self, *args, **kwargs) -> GitCommandResult:
        return await asyncio.to_thread(self._git.commit, *args, **kwargs)

    async def reset(self, *args, **kwargs) -> GitCommandResult:
        return await asyncio.to_thread(self._git.reset, *args, **kwargs)

    async def restore(self, *args, **kwargs) -> GitCommandResult:
        return await asyncio.to_thread(self._git.restore, *args, **kwargs)

    async def pull(self, *args, **kwargs) -> GitCommandResult:
        return await asyncio.to_thread(self._git.pull, *args, **kwargs)

    async def push(self, *args, **kwargs) -> GitCommandResult:
        return await asyncio.to_thread(self._git.push, *args, **kwargs)

    async def checkout(self, *args, **kwargs) -> GitCommandResult:
        return await asyncio.to_thread(self._git.checkout, *args, **kwargs)

    async def checkout_branch(self, *args, **kwargs) -> GitCommandResult:
        return await asyncio.to_thread(self._git.checkout_branch, *args, **kwargs)

    async def remote_add(self, *args, **kwargs) -> GitCommandResult:
        return await asyncio.to_thread(self._git.remote_add, *args, **kwargs)

    async def remote_get(self, *args, **kwargs) -> Optional[str]:
        return await asyncio.to_thread(self._git.remote_get, *args, **kwargs)

    async def set_config(self, *args, **kwargs) -> GitCommandResult:
        return await asyncio.to_thread(self._git.set_config, *args, **kwargs)

    async def get_config(self, *args, **kwargs) -> str:
        return await asyncio.to_thread(self._git.get_config, *args, **kwargs)


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


class AsyncSandboxPaginator:
    """Async paginator for listing Watasu sandboxes."""

    def __init__(self, paginator: SandboxPaginator[SandboxInfo]) -> None:
        self._paginator = paginator
        self._iter_items = None

    @property
    def has_next(self) -> bool:
        """Return whether another page can be fetched."""
        return self._paginator.has_next

    @property
    def next_token(self) -> Optional[str]:
        """Return the next pagination cursor."""
        return self._paginator.next_token

    async def list_items(self) -> List[SandboxInfo]:
        """Drain all remaining pages and return them as a list."""
        return await asyncio.to_thread(self._paginator.list_items)

    async def next_items(self, **opts: ApiParams) -> List[SandboxInfo]:
        """Fetch and return the next page of sandboxes."""
        return await asyncio.to_thread(self._paginator.next_items, **opts)

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
    default_mcp_template = Sandbox.default_mcp_template

    @property
    def sandbox_id(self):
        """Sandbox identifier."""
        return self._sync.sandbox_id

    @property
    def id(self):
        """Sandbox id alias."""
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
        return self._pty

    @property
    def git(self):
        return self._git

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
    async def create(
        cls,
        template: Optional[str] = None,
        timeout: Optional[int] = None,
        metadata: Optional[Dict[str, str]] = None,
        envs: Optional[Dict[str, str]] = None,
        secure: bool = True,
        allow_internet_access: bool = True,
        mcp: Optional[Dict[str, Any]] = None,
        network=None,
        volume_mounts: Optional[Dict[str, Any]] = None,
        lifecycle=None,
        auto_pause: Optional[bool] = None,
        team: Optional[str] = None,
        **opts: ApiParams,
    ) -> "AsyncSandbox":
        """Create a sandbox and return it with async helpers ready."""
        return cls(
            sync_sandbox=await asyncio.to_thread(
                Sandbox.create,
                template=template,
                timeout=timeout,
                metadata=metadata,
                envs=envs,
                secure=secure,
                allow_internet_access=allow_internet_access,
                mcp=mcp,
                network=network,
                volume_mounts=volume_mounts,
                lifecycle=lifecycle,
                auto_pause=auto_pause,
                team=team,
                **opts,
            )
        )

    @classmethod
    async def beta_create(
        cls,
        template: Optional[str] = None,
        timeout: Optional[int] = None,
        auto_pause: bool = False,
        metadata: Optional[Dict[str, str]] = None,
        envs: Optional[Dict[str, str]] = None,
        secure: bool = True,
        allow_internet_access: bool = True,
        mcp: Optional[Dict[str, Any]] = None,
        network=None,
        team: Optional[str] = None,
        **opts: ApiParams,
    ) -> "AsyncSandbox":
        """Create a sandbox with beta lifecycle options such as auto_pause."""
        return cls(
            sync_sandbox=await asyncio.to_thread(
                Sandbox.beta_create,
                template=template,
                timeout=timeout,
                metadata=metadata,
                envs=envs,
                secure=secure,
                allow_internet_access=allow_internet_access,
                mcp=mcp,
                network=network,
                auto_pause=auto_pause,
                team=team,
                **opts,
            )
        )

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
    reconnect = connect

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

    async def close(self) -> None:
        """Close the local SDK attachment without destroying the sandbox."""
        return None

    async def _beta_pause_instance(self, **opts: ApiParams) -> bool:
        """Pause this sandbox. Returns ``False`` if it is already paused."""
        return await asyncio.to_thread(self._sync.beta_pause, **opts)

    @classmethod
    async def _beta_pause_class(cls, sandbox_id: str, **opts: ApiParams) -> bool:
        """Pause a sandbox by id."""
        return await asyncio.to_thread(Sandbox.beta_pause, sandbox_id, **opts)

    beta_pause = _AsyncDualMethod(_beta_pause_instance, _beta_pause_class)
    pause = beta_pause

    async def _resume_instance(
        self, timeout: Optional[int] = None, **opts: ApiParams
    ) -> bool:
        """Resume this sandbox and refresh its data-plane session."""
        await asyncio.to_thread(self._sync.resume, timeout=timeout, **opts)
        self._set_sync(self._sync)
        return True

    @classmethod
    async def _resume_class(
        cls, sandbox_id: str, timeout: Optional[int] = None, **opts: ApiParams
    ) -> bool:
        """Resume a sandbox by id."""
        return await asyncio.to_thread(Sandbox.resume, sandbox_id, timeout=timeout, **opts)

    resume = _AsyncDualMethod(_resume_instance, _resume_class)

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

    async def _get_metrics_instance(
        self,
        start: Optional[datetime.datetime] = None,
        end: Optional[datetime.datetime] = None,
        **opts: ApiParams,
    ) -> List[SandboxMetrics]:
        """Fetch latest sandbox metrics."""
        return await asyncio.to_thread(self._sync.get_metrics, start=start, end=end, **opts)

    @classmethod
    async def _get_metrics_class(
        cls,
        sandbox_id: str,
        start: Optional[datetime.datetime] = None,
        end: Optional[datetime.datetime] = None,
        **opts: ApiParams,
    ) -> List[SandboxMetrics]:
        """Fetch sandbox metrics by id."""
        return await asyncio.to_thread(
            Sandbox.get_metrics, sandbox_id, start=start, end=end, **opts
        )

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
        cls, sandbox_id: Optional[str] = None, **opts: ApiParams
    ) -> AsyncSnapshotPaginator:
        """List checkpoints visible to the configured API token."""
        return AsyncSnapshotPaginator(lambda: Sandbox.list_snapshots(sandbox_id, **opts))

    list_snapshots = _AsyncDualMethod(_list_snapshots_instance, _list_snapshots_class)

    async def _delete_snapshot_instance(self, *args, **kwargs) -> bool:
        """Delete a snapshot by id."""
        return await asyncio.to_thread(self._sync.delete_snapshot, *args, **kwargs)

    @classmethod
    async def _delete_snapshot_class(cls, snapshot_id: str, **opts: ApiParams) -> bool:
        """Delete a snapshot by id."""
        return await asyncio.to_thread(Sandbox.delete_snapshot, snapshot_id, **opts)

    delete_snapshot = _AsyncDualMethod(_delete_snapshot_instance, _delete_snapshot_class)

    async def restore(self, *args, **kwargs) -> SandboxInfo:
        """Restore a checkpoint into a new sandbox and return its metadata."""
        return await asyncio.to_thread(self._sync.restore, *args, **kwargs)

    @staticmethod
    async def list(
        query: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
        next_token: Optional[str] = None,
        team: Optional[str] = None,
        **opts: ApiParams,
    ) -> AsyncSandboxPaginator:
        """Return an async paginator for visible sandboxes."""
        paginator = await asyncio.to_thread(
            Sandbox.list,
            query=query,
            limit=limit,
            next_token=next_token,
            team=team,
            **opts,
        )
        return AsyncSandboxPaginator(paginator)

    def get_host(self, port: int) -> str:
        """Return the public hostname for an exposed sandbox port."""
        return self._sync.get_host(port)

    def get_mcp_url(self) -> str:
        """Return the conventional MCP URL for this sandbox."""
        return self._sync.get_mcp_url()

    async def get_mcp_token(self, request_timeout: Optional[float] = None) -> Optional[str]:
        """Return the MCP gateway token when the sandbox contains one."""
        return await asyncio.to_thread(
            self._sync.get_mcp_token,
            request_timeout=request_timeout,
        )

    async def upload_url(self, *args, **kwargs) -> str:
        """Get a signed URL for uploading a file."""
        return await asyncio.to_thread(self._sync.upload_url, *args, **kwargs)

    async def download_url(self, *args, **kwargs) -> str:
        """Get a signed URL for downloading a file."""
        return await asyncio.to_thread(self._sync.download_url, *args, **kwargs)

    async def upload_url_info(self, *args, **kwargs) -> FileUrlInfo:
        """Get signed upload URL metadata."""
        return await asyncio.to_thread(self._sync.upload_url_info, *args, **kwargs)

    async def download_url_info(self, *args, **kwargs) -> FileUrlInfo:
        """Get signed download URL metadata."""
        return await asyncio.to_thread(self._sync.download_url_info, *args, **kwargs)

    async def _update_network_instance(self, *args, **kwargs):
        """Atomically replace this sandbox's network egress policy."""
        return await asyncio.to_thread(self._sync.update_network, *args, **kwargs)

    @classmethod
    async def _update_network_class(cls, sandbox_id: str, *args, **kwargs):
        """Atomically replace a sandbox network egress policy by id."""
        return await asyncio.to_thread(Sandbox.update_network, sandbox_id, *args, **kwargs)

    update_network = _AsyncDualMethod(_update_network_instance, _update_network_class)

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
        self._pty = AsyncPty(sandbox.pty)
        self._git = AsyncGit(sandbox.git)


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
    "AsyncGit",
    "AsyncPty",
    "AsyncSandbox",
    "AsyncSandboxPaginator",
    "AsyncSnapshotPaginator",
    "AsyncWatchHandle",
]
