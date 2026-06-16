from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional, Union
from urllib.parse import quote

from watasu.connection_config import ApiParams
from watasu.exceptions import InvalidArgumentException
from watasu.sandbox_async.main import AsyncSandbox as BaseAsyncSandbox
from watasu.sandbox_async.main import _AsyncDualMethod, _thread_callback
from watasu.sandbox_sync.main import Sandbox as BaseSandbox

from .constants import DEFAULT_TEMPLATE
from .models import Context, Execution, ExecutionError, OutputMessage, Result
from .models import context_from_api
from .models import execution_from_api


class Sandbox(BaseSandbox):
    """Sandbox specialized for running Python code."""

    default_template = DEFAULT_TEMPLATE

    def run_code(
        self,
        code: str,
        language: Optional[str] = None,
        context: Optional[Context] = None,
        on_stdout: Optional[Callable[[OutputMessage], Any]] = None,
        on_stderr: Optional[Callable[[OutputMessage], Any]] = None,
        on_result: Optional[Callable[[Result], Any]] = None,
        on_error: Optional[Callable[[ExecutionError], Any]] = None,
        envs: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> Execution:
        """Run Python code in the sandbox and return structured execution output."""

        if not isinstance(code, str):
            raise InvalidArgumentException("code must be a string")
        if language is not None and context is not None:
            raise InvalidArgumentException("language and context cannot both be set")

        payload = _compact(
            {
                "code": code,
                "language": language,
                "context_id": _context_id(context),
                "env_vars": envs,
                "timeout_seconds": timeout,
            }
        )
        response = self._require_data_plane().post_json(
            "/runtime/v1/code/run",
            json=payload,
            request_timeout=request_timeout,
        )
        execution = execution_from_api(response)
        _emit_callbacks(execution, on_stdout, on_stderr, on_result, on_error)
        return execution

    def create_code_context(
        self,
        cwd: Optional[str] = None,
        language: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> Context:
        """Create a persistent code context."""

        payload = _compact({"cwd": cwd, "language": language})
        response = self._require_data_plane().post_json(
            "/runtime/v1/code/contexts",
            json=payload,
            request_timeout=request_timeout,
        )
        return context_from_api(response)

    def remove_code_context(
        self,
        context: Union[Context, str],
    ) -> None:
        """Remove a persistent code context."""

        self._require_data_plane().delete_json(
            f"/runtime/v1/code/contexts/{_context_path_id(context)}",
        )

    def list_code_contexts(self) -> List[Context]:
        """List persistent code contexts."""

        response = self._require_data_plane().get_json(
            "/runtime/v1/code/contexts",
        )
        contexts = response if isinstance(response, list) else response.get("contexts", [])
        return [context_from_api(item) for item in contexts]

    def restart_code_context(
        self,
        context: Union[Context, str],
    ) -> None:
        """Restart a persistent code context."""

        self._require_data_plane().post_json(
            f"/runtime/v1/code/contexts/{_context_path_id(context)}/restart",
            json={},
        )


class AsyncSandbox(BaseAsyncSandbox):
    """Async sandbox specialized for running Python code."""

    default_template = Sandbox.default_template

    @classmethod
    async def create(cls, *args: Any, **kwargs: Any) -> "AsyncSandbox":
        """Create a code-interpreter sandbox and return async helpers."""

        return cls(sync_sandbox=await asyncio.to_thread(Sandbox.create, *args, **kwargs))

    beta_create = create

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
        """Connect to an existing code-interpreter sandbox by id."""

        return cls(
            sync_sandbox=await asyncio.to_thread(
                Sandbox.connect, sandbox_id, timeout=timeout, **opts
            )
        )

    connect = _AsyncDualMethod(_connect_instance, _connect_class)

    async def run_code(
        self,
        code: str,
        language: Optional[str] = None,
        context: Optional[Context] = None,
        on_stdout: Optional[Callable[[OutputMessage], Any]] = None,
        on_stderr: Optional[Callable[[OutputMessage], Any]] = None,
        on_result: Optional[Callable[[Result], Any]] = None,
        on_error: Optional[Callable[[ExecutionError], Any]] = None,
        envs: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> Execution:
        """Run Python code in the sandbox and return structured execution output."""

        loop = asyncio.get_running_loop()
        return await asyncio.to_thread(
            self._sync.run_code,
            code,
            language=language,
            context=context,
            on_stdout=_thread_callback(loop, on_stdout),
            on_stderr=_thread_callback(loop, on_stderr),
            on_result=_thread_callback(loop, on_result),
            on_error=_thread_callback(loop, on_error),
            envs=envs,
            timeout=timeout,
            request_timeout=request_timeout,
        )

    async def create_code_context(
        self,
        cwd: Optional[str] = None,
        language: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> Context:
        """Create a persistent code context."""

        return await asyncio.to_thread(
            self._sync.create_code_context,
            cwd=cwd,
            language=language,
            request_timeout=request_timeout,
        )

    async def remove_code_context(
        self,
        context: Union[Context, str],
    ) -> None:
        """Remove a persistent code context."""

        await asyncio.to_thread(
            self._sync.remove_code_context,
            context,
        )

    async def list_code_contexts(self) -> List[Context]:
        """List persistent code contexts."""

        return await asyncio.to_thread(
            self._sync.list_code_contexts,
        )

    async def restart_code_context(
        self,
        context: Union[Context, str],
    ) -> None:
        """Restart a persistent code context."""

        await asyncio.to_thread(
            self._sync.restart_code_context,
            context,
        )


def _emit_callbacks(
    execution: Execution,
    on_stdout: Optional[Callable[[OutputMessage], Any]],
    on_stderr: Optional[Callable[[OutputMessage], Any]],
    on_result: Optional[Callable[[Result], Any]],
    on_error: Optional[Callable[[ExecutionError], Any]],
) -> None:
    for message in execution.logs.stdout:
        if on_stdout is not None:
            on_stdout(message)
    for message in execution.logs.stderr:
        if on_stderr is not None:
            on_stderr(message)
    for result in execution.results:
        if on_result is not None:
            on_result(result)
    if execution.error is not None and on_error is not None:
        on_error(execution.error)


def _context_id(context: Optional[Union[Context, str]]) -> Optional[str]:
    if context is None:
        return None
    return _context_id_required(context)


def _context_id_required(context: Union[Context, str]) -> str:
    if isinstance(context, str):
        context_id = context
    elif isinstance(context, dict):
        value = context.get("id")
        context_id = str(value) if value is not None else ""
    else:
        value = getattr(context, "id", None)
        context_id = str(value) if value is not None else ""

    if not context_id:
        raise InvalidArgumentException("context id is required")
    return context_id


def _context_path_id(context: Union[Context, str]) -> str:
    return quote(_context_id_required(context), safe="")


def _compact(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


__all__ = [
    "AsyncSandbox",
    "Context",
    "Execution",
    "ExecutionError",
    "OutputMessage",
    "Result",
    "Sandbox",
]
