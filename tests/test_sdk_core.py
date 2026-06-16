from __future__ import annotations

import asyncio
import base64
import datetime
import inspect
import json

import pytest

import watasu
import watasu_code_interpreter
import watasu_code_interpreter.charts as ci_charts
import watasu_code_interpreter.models as ci_models
from watasu import (
    ALL_TRAFFIC,
    AsyncCommandHandle as TopLevelAsyncCommandHandle,
    AsyncSandbox,
    AsyncTemplate,
    BuildException,
    CommandExitException,
    CommandResult,
    ConnectionConfig,
    ConflictException,
    InvalidArgumentException,
    LogEntry,
    LogEntryEnd,
    LogEntryStart,
    Sandbox,
    SandboxBase,
    SandboxException,
    SandboxOpts,
    McpServer,
    Template,
    Volume,
    AsyncVolume,
    TemplateBuildStatus,
    default_build_logger,
    get_signature,
    wait_for_timeout,
)
from watasu._transport.data_plane import DataPlaneClient
from watasu._transport.process_ws import ProcessSocket
from watasu.sandbox.commands.command_handle import PtySize
from watasu.sandbox.filesystem.filesystem import FileType
from watasu.sandbox.sandbox_api import sandbox_info_from_api, sandbox_metrics_from_api
from watasu.sandbox_async.main import (
    AsyncCommandHandle,
    AsyncCommands,
    AsyncFilesystem,
    AsyncGit,
    AsyncPty,
)
from watasu.sandbox_sync.commands.command import Commands as SyncCommands
from watasu.sandbox_sync.filesystem.filesystem import Filesystem
from watasu.sandbox_sync.commands.command_handle import CommandHandle
from watasu.sandbox_sync.filesystem.watch_handle import WatchHandle
from watasu.sandbox_sync.git import Git
from watasu.template_async import AsyncTemplate as TemplateAsyncExport
from watasu.template_async.main import AsyncTemplate as TemplateAsyncMain
from watasu.template_sync import Template as TemplateSyncExport
from watasu.template_sync.main import Template as TemplateSyncMain
from watasu_code_interpreter import Sandbox as CodeInterpreterSandbox
from watasu_code_interpreter.charts import BarChart, ChartType


def test_connection_config_defaults_to_watasu_hosts(monkeypatch):
    monkeypatch.setenv("WATASU_API_KEY", "key")
    config = ConnectionConfig()

    assert ConnectionConfig.envd_port == 49983
    assert config.api_key == "key"
    assert config.api_url == "https://api.watasu.io/v1"
    assert config.data_plane_domain == "watasuhost.com"
    assert config.auth_headers["Authorization"] == "Bearer key"


def test_connection_config_accepts_access_token_alias(monkeypatch):
    monkeypatch.delenv("WATASU_API_KEY", raising=False)

    config = ConnectionConfig(access_token="alias-key")

    assert config.api_key == "alias-key"
    assert config.auth_headers["Authorization"] == "Bearer alias-key"


def test_connection_config_zero_request_timeout_disables_timeout():
    config = ConnectionConfig(api_key="key", request_timeout=0)

    assert config.request_timeout is None
    assert config.get_request_timeout() is None
    assert config.get_request_timeout(0) is None
    assert config.get_request_timeout(2) == 2.0


def test_connection_config_accepts_sandbox_url_override(monkeypatch):
    monkeypatch.delenv("WATASU_SANDBOX_URL", raising=False)

    config = ConnectionConfig(
        api_key="key",
        sandbox_url="http://localhost:49983",
        headers={"x-shared": "shared"},
        extra_sandbox_headers={"x-sandbox": "sandbox"},
    )

    assert config.sandbox_url == "http://localhost:49983"
    assert config.get_api_params()["sandbox_url"] == "http://localhost:49983"
    assert config.sandbox_headers == {"x-shared": "shared", "x-sandbox": "sandbox"}
    assert config.get_api_params()["extra_sandbox_headers"] == {
        "x-sandbox": "sandbox"
    }


def test_connection_config_exposes_watasu_sandbox_host_helpers():
    config = ConnectionConfig(api_key="key")

    assert (
        config.get_host("route-token", "watasuhost.com", 3000)
        == "p3000-route-token.sandbox.watasuhost.com"
    )
    assert (
        config.get_sandbox_url("route-token", "watasuhost.com")
        == "https://route-token.sandbox.watasuhost.com"
    )
    assert (
        ConnectionConfig(api_key="key", debug=True).get_host(
            "route-token", "watasuhost.com", 3000
        )
        == "localhost:3000"
    )


def test_public_team_ref_signatures_are_explicit():
    assert AsyncSandbox.mcp_port == Sandbox.mcp_port
    assert AsyncSandbox.default_sandbox_timeout == Sandbox.default_sandbox_timeout
    assert not hasattr(Sandbox, "checkpoint")
    assert not hasattr(AsyncSandbox, "checkpoint")
    assert "team" in inspect.signature(Sandbox.create).parameters
    assert "team" in inspect.signature(Sandbox.list).parameters
    assert "team" in inspect.signature(AsyncSandbox.create).parameters
    assert "team" in inspect.signature(AsyncSandbox.list).parameters
    assert "team" in inspect.signature(Volume.create).parameters
    assert "team" in inspect.signature(Volume.list).parameters
    assert "team" in inspect.signature(AsyncVolume.create).parameters
    assert "team" in inspect.signature(AsyncVolume.list).parameters


def test_template_sync_and_async_import_paths_match_top_level_exports():
    assert TemplateSyncExport is Template
    assert TemplateSyncMain is Template
    assert TemplateAsyncExport is AsyncTemplate
    assert TemplateAsyncMain is AsyncTemplate
    assert watasu.template_sync.Template is Template
    assert watasu.template_async.AsyncTemplate is AsyncTemplate


def test_code_interpreter_package_re_exports_core_sdk_helpers():
    import watasu_code_interpreter.code_interpreter_async as ci_async
    import watasu_code_interpreter.code_interpreter_sync as ci_sync
    import watasu_code_interpreter.constants as ci_constants
    import watasu_code_interpreter.exceptions as ci_exceptions

    assert watasu_code_interpreter.Sandbox is CodeInterpreterSandbox
    assert watasu_code_interpreter.Sandbox is not watasu.Sandbox
    assert watasu_code_interpreter.Sandbox.default_template == "code-interpreter"
    assert watasu_code_interpreter.ProcessInfo is watasu.ProcessInfo
    assert watasu_code_interpreter.ConnectionConfig is watasu.ConnectionConfig
    assert watasu_code_interpreter.Template is watasu.Template
    assert watasu_code_interpreter.PtySize is watasu.PtySize
    assert watasu.AsyncCommandHandle is TopLevelAsyncCommandHandle
    assert watasu.AsyncCommandHandle is AsyncCommandHandle
    assert issubclass(watasu_code_interpreter.MIMEType, str)
    assert callable(watasu_code_interpreter.OutputHandler)
    assert ci_sync.Sandbox is CodeInterpreterSandbox
    assert ci_async.AsyncSandbox is watasu_code_interpreter.AsyncSandbox
    assert watasu_code_interpreter.charts is ci_charts
    assert watasu_code_interpreter.models is ci_models
    assert ci_constants.DEFAULT_TEMPLATE == "code-interpreter"
    assert ci_constants.DEFAULT_TIMEOUT == 300
    assert ci_constants.JUPYTER_PORT == 49999
    assert isinstance(
        ci_exceptions.format_request_timeout_error(), watasu.TimeoutException
    )
    assert isinstance(
        ci_exceptions.format_execution_timeout_error(), watasu.TimeoutException
    )


def test_template_logger_exports_are_real_helpers(capsys):
    logger = default_build_logger(min_level="debug")
    start = LogEntryStart(message="start")
    end = LogEntryEnd(message="end")

    assert callable(logger)
    assert start.level == "debug"
    assert end.level == "debug"

    logger(LogEntry(timestamp=None, level="info", message="hello"))

    assert "[info] hello" in capsys.readouterr().out


def test_code_interpreter_result_matches_chart_and_mapping_helpers():
    result = watasu_code_interpreter.Result(
        text="summary",
        chart={
            "type": "bar",
            "title": "Builds",
            "x_label": "day",
            "y_label": "count",
            "elements": [{"label": "Mon", "value": "4", "group": "week"}],
        },
        extra={"application/vnd.custom": {"ok": True}},
    )

    assert result["text"] == "summary"
    assert repr(result) == "Result(summary)"
    assert result.chart is not None
    assert isinstance(result.chart, BarChart)
    assert result.chart.type == ChartType.BAR
    assert result.chart.elements[0].label == "Mon"
    assert "chart" in result.formats()
    assert "application/vnd.custom" in result.formats()


def test_code_interpreter_models_use_reference_json_shape():
    context = watasu_code_interpreter.Context.from_json(
        {"id": "ctx-1", "language": "python", "cwd": "/workspace"}
    )
    assert context.id == "ctx-1"
    assert context.language == "python"
    assert context.cwd == "/workspace"

    execution = watasu_code_interpreter.Execution(
        results=[
            watasu_code_interpreter.Result(text="ok", is_main_result=True),
        ],
        logs=watasu_code_interpreter.Logs(
            stdout=[watasu_code_interpreter.OutputMessage("hello")],
            stderr=[watasu_code_interpreter.OutputMessage("warn", error=True)],
        ),
        error=watasu_code_interpreter.ExecutionError(
            name="ValueError",
            value="bad",
            traceback="trace",
        ),
        execution_count=2,
    )

    payload = json.loads(execution.to_json())
    assert payload["results"] == [{"text": "ok"}]
    assert json.loads(payload["logs"]) == {"stdout": ["hello"], "stderr": ["warn"]}
    assert json.loads(payload["error"]) == {
        "name": "ValueError",
        "value": "bad",
        "traceback": "trace",
    }
    assert "execution_count" not in payload
    assert json.loads(
        watasu_code_interpreter.Logs(stdout=["plain"], stderr=["err"]).to_json()
    ) == {"stdout": ["plain"], "stderr": ["err"]}


def test_async_sandbox_forwards_base_connection_properties():
    sync_sandbox = Sandbox(
        "sbx-1",
        connection_config=ConnectionConfig(
            api_key="key",
            sandbox_url="https://route.sandbox.watasuhost.com",
        ),
        session={
            "data_plane_url": "https://route.sandbox.watasuhost.com",
            "token": "data-token",
            "sandbox_domain": "sandbox.watasuhost.com",
            "traffic_access_token": "traffic-token",
        },
        sandbox={"route_token": "route-token"},
    )
    async_sandbox = AsyncSandbox(sync_sandbox=sync_sandbox)

    assert async_sandbox.connection_config is sync_sandbox.connection_config
    assert async_sandbox.sandbox_domain == "sandbox.watasuhost.com"
    assert async_sandbox.traffic_access_token == "traffic-token"
    assert async_sandbox.envd_api_url == "https://route.sandbox.watasuhost.com"
    assert async_sandbox.envd_direct_url == "https://route.sandbox.watasuhost.com"


def test_commands_list_prefers_stable_process_id_over_guest_os_pid():
    class FakeDataPlane:
        base_url = "http://localhost:49983"
        token = "data-token"

        def get_json(self, path, request_timeout=None):
            assert path == "/runtime/v1/process"
            return {
                "processes": [
                    {
                        "id": "proc-123",
                        "pid": 456,
                        "command": "bash",
                        "args": ["-lc", "sleep 60"],
                        "cwd": "/workspace",
                    }
                ]
            }

    commands = SyncCommands(FakeDataPlane(), ConnectionConfig(api_key="key"))

    process = commands.list()[0]

    assert process.pid == "proc-123"
    assert process.cmd == "bash"
    assert process.args == ["-lc", "sleep 60"]
    assert process.cwd == "/workspace"


def test_commands_close_stdin_connects_and_disconnects():
    calls = []
    commands = SyncCommands(object(), ConnectionConfig(api_key="key"))

    class FakeHandle:
        def close_stdin(self, request_timeout=None):
            calls.append(("close_stdin", request_timeout))

        def disconnect(self):
            calls.append(("disconnect", None))

    commands.connect = lambda pid, request_timeout=None: (
        calls.append(("connect", pid, request_timeout)) or FakeHandle()
    )

    commands.close_stdin(123, request_timeout=5)

    assert calls == [
        ("connect", 123, 5),
        ("close_stdin", 5),
        ("disconnect", None),
    ]


def test_volume_helper_uses_control_api_paths_and_snake_case_payloads(monkeypatch):
    import watasu.volume as volume_module

    calls = []

    class FakeControl:
        def __init__(self, config):
            self.config = config

        def post(self, path, **kwargs):
            calls.append(("POST", path, kwargs))
            if path == "/volumes":
                return {
                    "volume": {
                        "id": 42,
                        "name": "cache",
                        "token": "wvol_secret",
                        "state": "ready",
                        "size_mb": 10240,
                        "metadata": {"purpose": "tests"},
                    }
                }
            raise AssertionError(path)

        def put(self, path, **kwargs):
            calls.append(("PUT", path, kwargs))
            return {
                "file": {
                    "path": "/workspace/a.txt",
                    "name": "a.txt",
                    "type": "file",
                    "bytes": 5,
                }
            }

        def get(self, path, **kwargs):
            calls.append(("GET", path, kwargs))
            if path.endswith("/files"):
                return {"file": {"path": "/workspace/a.txt", "content_b64": "aGVsbG8="}}
            if path.endswith("/directories"):
                return {
                    "entries": [
                        {
                            "path": "/workspace/a.txt",
                            "name": "a.txt",
                            "type": "file",
                            "bytes": 5,
                        }
                    ]
                }
            return {"volume": {"id": 42, "name": "cache"}}

        def delete(self, path, **kwargs):
            calls.append(("DELETE", path, kwargs))
            return {"deleted": True}

    monkeypatch.setattr(volume_module, "ControlClient", FakeControl)

    volume = Volume.create("cache", api_key="key", team="watasu")
    assert isinstance(volume, Volume)
    assert volume.id == "42"
    assert volume.name == "cache"
    assert volume.token == "wvol_secret"

    written = volume.write_file("/workspace/a.txt", "hello", mode="0644")
    assert written.path == "/workspace/a.txt"
    assert volume.read_file("/workspace/a.txt") == "hello"
    assert volume.list("/workspace", depth=2)[0].name == "a.txt"
    assert volume.remove("/workspace/a.txt") is True
    assert volume.destroy() is True

    assert calls[0] == (
        "POST",
        "/volumes",
        {
            "json": {"name": "cache", "team": "watasu"},
            "resource": "volume",
            "request_timeout": None,
        },
    )
    assert calls[1] == (
        "PUT",
        "/volumes/42/files",
        {
            "json": {
                "path": "/workspace/a.txt",
                "content_b64": "aGVsbG8=",
                "mode": "0644",
            },
            "resource": "volume",
            "request_timeout": None,
        },
    )
    assert calls[2] == (
        "GET",
        "/volumes/42/files",
        {
            "params": {"path": "/workspace/a.txt"},
            "resource": "volume",
            "request_timeout": None,
        },
    )
    assert calls[3] == (
        "GET",
        "/volumes/42/directories",
        {
            "params": {"path": "/workspace", "depth": 2},
            "resource": "volume",
            "request_timeout": None,
        },
    )
    assert calls[4] == (
        "DELETE",
        "/volumes/42/path",
        {
            "params": {"path": "/workspace/a.txt"},
            "resource": "volume",
            "request_timeout": None,
        },
    )
    assert calls[5][0:2] == ("DELETE", "/volumes/42")


def test_async_volume_wraps_sync_volume_methods(monkeypatch):
    import watasu.volume as volume_module

    class FakeControl:
        def __init__(self, config):
            self.config = config

        def post(self, path, **kwargs):
            return {"volume": {"id": 42, "name": "cache", "token": "wvol_secret"}}

        def get(self, path, **kwargs):
            if path.endswith("/directories"):
                return {"entries": [{"path": "/workspace", "name": "workspace", "type": "directory"}]}
            return {"volume": {"id": 42, "name": "cache"}}

    monkeypatch.setattr(volume_module, "ControlClient", FakeControl)

    async def scenario():
        volume = await AsyncVolume.create("cache", api_key="key")
        assert isinstance(volume, AsyncVolume)
        assert volume.id == "42"
        assert (await volume.get_info()).name == "cache"
        assert (await volume.list("/"))[0].name == "workspace"

    asyncio.run(scenario())


def test_command_handle_raises_on_non_zero_exit():
    frames = iter(
        [
            {"type": "stdout", "data": "before\n"},
            {"type": "stderr", "data": "bad\n"},
            {"type": "exit", "exit_code": 7, "error": None},
        ]
    )
    handle = CommandHandle(pid=123, handle_kill=lambda: True, events=frames)

    with pytest.raises(CommandExitException) as exc:
        handle.wait()

    assert exc.value.exit_code == 7
    assert exc.value.stdout == "before\n"
    assert exc.value.stderr == "bad\n"


def test_command_handle_streams_stdout_callback_before_exit():
    frames = iter(
        [
            {"type": "stdout", "data": "1\n"},
            {"type": "stdout", "data": "2\n"},
            {"type": "exit", "exit_code": 0, "error": None},
        ]
    )
    handle = CommandHandle(pid=123, handle_kill=lambda: True, events=frames)
    seen = []

    result = handle.wait(on_stdout=seen.append)

    assert seen == ["1\n", "2\n"]
    assert result == CommandResult(stderr="", stdout="1\n2\n", exit_code=0, error=None)


def test_command_handle_decodes_runtime_base64_stream_frames():
    frames = iter(
        [
            {"type": "stdout", "data": "NAo="},
            {"type": "stderr", "data": "ZXJyCg=="},
            {"type": "exit", "exit_code": 0, "error": None},
        ]
    )
    handle = CommandHandle(pid=123, handle_kill=lambda: True, events=frames)

    result = handle.wait()

    assert result == CommandResult(
        stderr="err\n", stdout="4\n", exit_code=0, error=None
    )


def test_command_handle_decodes_pty_frames_as_terminal_output():
    frames = iter(
        [
            {"type": "pty", "data": "dGVybQo="},
            {"type": "exit", "exit_code": 0, "error": None},
        ]
    )
    handle = CommandHandle(pid=123, handle_kill=lambda: True, events=frames)
    seen = []

    result = handle.wait(on_pty=seen.append)

    assert seen == [b"term\n"]
    assert result.stdout == "term\n"


def test_async_command_connect_pumps_configured_output_callbacks():
    class FakeCommands:
        def connect(self, pid, timeout=60, request_timeout=None):
            assert pid == 123
            assert timeout == 5
            assert request_timeout == 2
            frames = iter(
                [
                    {"type": "stdout", "data": "connected\n"},
                    {"type": "stderr", "data": "warning\n"},
                    {"type": "exit", "exit_code": 0, "error": None},
                ]
            )
            return CommandHandle(pid=pid, handle_kill=lambda: True, events=frames)

    async def scenario():
        stdout = []
        stderr = []

        async def on_stdout(chunk):
            stdout.append(chunk)

        def on_stderr(chunk):
            stderr.append(chunk)

        handle = await AsyncCommands(FakeCommands()).connect(
            123,
            timeout=5,
            request_timeout=2,
            on_stdout=on_stdout,
            on_stderr=on_stderr,
        )
        result = await handle.wait()
        return stdout, stderr, result

    stdout, stderr, result = asyncio.run(scenario())

    assert stdout == ["connected\n"]
    assert stderr == ["warning\n"]
    assert result.exit_code == 0


def test_async_commands_close_stdin_forwards_to_sync_commands():
    calls = []

    class FakeCommands:
        def close_stdin(self, pid, request_timeout=None):
            calls.append((pid, request_timeout))

    async def scenario():
        await AsyncCommands(FakeCommands()).close_stdin(123, request_timeout=5)

    asyncio.run(scenario())

    assert calls == [(123, 5)]


def test_async_command_handle_observes_background_callback_failures():
    frames = iter([{"type": "exit", "exit_code": -9, "error": "killed"}])
    handle = CommandHandle(pid=123, handle_kill=lambda: True, events=frames)

    async def scenario():
        async_handle = AsyncCommandHandle(handle, on_stdout=lambda _chunk: None)

        for _ in range(20):
            if async_handle._wait_task is not None and async_handle._wait_task.done():
                break
            await asyncio.sleep(0.01)

        task = async_handle._wait_task
        assert task is not None
        assert task.done()
        assert getattr(task, "_log_traceback", False) is False

        with pytest.raises(CommandExitException) as exc:
            await async_handle.wait()

        assert exc.value.exit_code == -9

    asyncio.run(scenario())


def test_async_pty_create_pumps_configured_data_callback():
    class FakePty:
        def create(
            self,
            size,
            user=None,
            cwd=None,
            envs=None,
            timeout=60,
            request_timeout=None,
        ):
            assert size == PtySize(rows=24, cols=80)
            assert timeout == 7
            assert request_timeout == 3
            frames = iter(
                [
                    {"type": "pty", "data": base64.b64encode(b"$ ").decode("ascii")},
                    {"type": "exit", "exit_code": 0, "error": None},
                ]
            )
            return CommandHandle(pid=456, handle_kill=lambda: True, events=frames)

    async def scenario():
        chunks = []

        async def on_data(chunk):
            chunks.append(chunk)

        handle = await AsyncPty(FakePty()).create(
            PtySize(rows=24, cols=80),
            on_data,
            timeout=7,
            request_timeout=3,
        )
        result = await handle.wait()
        return chunks, result

    chunks, result = asyncio.run(scenario())

    assert chunks == [b"$ "]
    assert result.exit_code == 0


def test_command_handle_close_stdin_sends_eof_frame():
    sent = []
    handle = CommandHandle(
        pid=123,
        handle_kill=lambda: True,
        events=iter([]),
        handle_close_stdin=lambda request_timeout=None: sent.append(
            {"type": "close_stdin", "request_timeout": request_timeout}
        ),
    )

    handle.close_stdin(request_timeout=5)

    assert sent == [{"type": "close_stdin", "request_timeout": 5}]


def test_process_socket_base64_encodes_stdin_frames():
    sent = []

    class FakeWebSocket:
        def send(self, payload):
            sent.append(payload)

    socket = ProcessSocket(
        "https://sandbox.example",
        "token",
        "/runtime/v1/process",
        headers={"x-sandbox": "sandbox"},
    )
    socket._ws = FakeWebSocket()

    socket.send_stdin("hi\n", wait_ack=False)

    assert socket.headers == {"x-sandbox": "sandbox"}
    assert json.loads(sent[0]) == {"type": "stdin", "data": "aGkK"}


def test_process_socket_waits_for_stdin_ack_and_buffers_output():
    sent = []
    received = [
        json.dumps({"type": "stdout", "data": "YmVmb3JlCg=="}),
        json.dumps({"type": "stdin_ack", "pid": "123"}),
    ]

    class FakeWebSocket:
        def send(self, payload):
            sent.append(payload)

        def settimeout(self, timeout):
            self.timeout = timeout

        def ping(self, payload):
            self.ping_payload = payload

        def recv(self):
            return received.pop(0)

    socket = ProcessSocket("https://sandbox.example", "token", "/runtime/v1/process")
    socket._ws = FakeWebSocket()

    socket.send_stdin("hi\n", request_timeout=1)

    assert json.loads(sent[0]) == {"type": "stdin", "data": "aGkK"}
    assert next(socket.frames(timeout=1)) == {"type": "stdout", "data": "YmVmb3JlCg=="}


def test_data_plane_client_uses_sandbox_headers():
    config = ConnectionConfig(
        api_key="key",
        headers={"x-shared": "shared"},
        extra_sandbox_headers={"x-sandbox": "sandbox"},
    )
    data_plane = DataPlaneClient("https://sandbox.example", "data-token", config)

    assert data_plane.headers == {
        "Connection": "close",
        "x-shared": "shared",
        "x-sandbox": "sandbox",
        "Authorization": "Bearer data-token",
    }


def test_get_host_returns_host_only():
    config = ConnectionConfig(api_key="key")
    calls = []

    class Control:
        def get(self, path, **kwargs):
            calls.append(path)
            return {
                "sandbox_port": {"url": "https://p8000-token.sandbox.watasuhost.com"}
            }

    sbx = Sandbox(
        "123",
        connection_config=config,
        control=Control(),
        session={
            "data_plane_url": "https://token.sandbox.watasuhost.com",
            "token": "data",
        },
        sandbox={},
    )

    assert sbx.get_host(8000) == "p8000-token.sandbox.watasuhost.com"
    assert calls == ["/sandboxes/123/ports/8000"]


def test_sandbox_url_override_replaces_data_plane_url_without_changing_public_hosts():
    config = ConnectionConfig(api_key="key", sandbox_url="http://localhost:49983")
    calls = []

    class Control:
        def get(self, path, **kwargs):
            calls.append(path)
            return {
                "sandbox_port": {"url": "https://p8000-token.sandbox.watasuhost.com"}
            }

    sbx = Sandbox(
        "123",
        connection_config=config,
        control=Control(),
        session={
            "data_plane_url": "https://token.sandbox.watasuhost.com",
            "token": "data",
        },
        sandbox={},
    )

    assert sbx.envd_api_url == "http://localhost:49983"
    assert sbx.envd_direct_url == "http://localhost:49983"
    assert sbx._require_data_plane().base_url == "http://localhost:49983"
    assert sbx.get_host(8000) == "p8000-token.sandbox.watasuhost.com"
    assert calls == ["/sandboxes/123/ports/8000"]


def test_filesystem_maps_watasu_file_entries():
    class DataPlane:
        def __init__(self):
            self.calls = []

        def put_json(self, path, **kwargs):
            self.calls.append((path, kwargs))
            return {
                "file": {
                    "path": "/home/user/a.txt",
                    "name": "a.txt",
                    "type": "file",
                    "bytes": 3,
                }
            }

        def get_bytes(self, path, **kwargs):
            self.calls.append((path, kwargs))
            return b"hello"

        def get_json(self, path, **kwargs):
            self.calls.append((path, kwargs))
            return {
                "entries": [
                    {
                        "path": "/home/user/a.txt",
                        "name": "a.txt",
                        "type": "file",
                        "bytes": 3,
                    }
                ]
            }

        def post_json(self, path, **kwargs):
            self.calls.append((path, kwargs))
            if path.endswith("/write_files"):
                return {
                    "files": [
                        {
                            "path": item["path"],
                            "name": item["path"].rstrip("/").split("/")[-1],
                            "type": "file",
                            "bytes": 3,
                        }
                        for item in kwargs["json"]["files"]
                    ]
                }
            return {
                "file": {
                    "path": "/home/user/b.txt",
                    "name": "b.txt",
                    "type": "file",
                    "bytes": 3,
                }
            }

    fs = Filesystem(DataPlane())

    written = fs.write("/home/user/a.txt", "abc")
    assert written.path == "/home/user/a.txt"
    assert written.type == FileType.FILE

    entries = fs.list("/home/user")
    assert entries[0].name == "a.txt"
    assert entries[0].size == 3

    renamed = fs.rename("/home/user/a.txt", "/home/user/b.txt")
    assert renamed.path == "/home/user/b.txt"

    written = fs.write_files(
        [
            {"path": "/home/user/c.txt", "data": "abc"},
            {"path": "/home/user/d.bin", "data": b"\x00\x01\x02"},
        ]
    )
    assert [item.path for item in written] == ["/home/user/c.txt", "/home/user/d.bin"]
    assert fs._data_plane.calls[-1][0] == "/runtime/v1/files/write_files"
    assert fs._data_plane.calls[-1][1]["json"]["files"][0]["data_base64"] == "YWJj"
    assert fs.read_bytes("/home/user/d.bin") == b"hello"

    fs.write("/home/user/e.txt", "abc", gzip=True)
    assert fs._data_plane.calls[-1][1]["params"] == {
        "path": "/home/user/e.txt",
        "gzip": "true",
    }

    fs.write_files([{"path": "/home/user/e.txt", "data": "abc"}], gzip=True)
    assert fs._data_plane.calls[-1][1]["json"]["files"][0]["gzip"] is True
    compressed = base64.b64decode(
        fs._data_plane.calls[-1][1]["json"]["files"][0]["data_base64"]
    )
    assert compressed.startswith(b"\x1f\x8b")


def test_git_helper_uses_data_plane_routes_and_parses_status():
    calls = []

    class DataPlane:
        def post_json(self, path, **kwargs):
            calls.append((path, kwargs))
            if path.endswith("/status"):
                return {
                    "git": {
                        "path": "/workspace/repo",
                        "stdout": "## main...origin/main [ahead 1]\n M a.txt\n?? b.txt\n",
                        "stderr": "",
                    }
                }
            if path.endswith("/branches"):
                return {
                    "git": {
                        "path": "/workspace/repo",
                        "branches": ["main", "feature/test"],
                        "current_branch": "main",
                        "stdout": "",
                        "stderr": "",
                    }
                }
            if path.endswith("/get_config"):
                return {
                    "git": {
                        "path": "/workspace/repo",
                        "key": "pull.rebase",
                        "value": "false",
                        "stdout": "false\n",
                        "stderr": "",
                    }
                }
            if path.endswith("/remote_get"):
                return {
                    "git": {
                        "path": "/workspace/repo",
                        "name": "origin",
                        "value": "https://git.example/repo.git",
                        "url": "https://git.example/repo.git",
                        "stdout": "https://git.example/repo.git\n",
                        "stderr": "",
                    }
                }
            return {
                "git": {
                    "path": "/workspace/repo",
                    "url": "https://git.example/repo.git",
                    "branch": "feature/test",
                    "remote": "origin",
                    "name": "origin",
                    "stdout": "ok\n",
                    "stderr": "",
                }
            }

    git = Git(DataPlane())

    cloned = git.clone(
        "https://git.example/repo.git",
        path="/workspace/repo",
        branch="main",
        depth=1,
        envs={"GIT_TRACE": "1"},
        user="sandbox",
        cwd="/workspace",
        timeout=10,
    )
    git.dangerously_authenticate("user", "token", host="git.example.com", protocol="https", timeout=5)
    git.configure_user("Watasu Test", "test@watasu.local", scope="local", path="/workspace/repo")
    git.init("/workspace/repo", initial_branch="main")
    status = git.status("/workspace/repo", user="sandbox", cwd="/workspace")
    branches = git.branches("/workspace/repo")
    git.create_branch("/workspace/repo", "feature/test")
    git.delete_branch("/workspace/repo", "feature/test", force=True)
    git.add("/workspace/repo", files=["README.md"], all=True, user="sandbox", cwd="/workspace/repo")
    git.commit(
        "/workspace/repo",
        "change",
        author_name="Watasu Test",
        author_email="test@watasu.local",
        allow_empty=True,
    )
    git.reset("/workspace/repo", mode="hard", target="HEAD", paths=["README.md"])
    git.restore("/workspace/repo", paths=["README.md"], staged=True)
    git.pull("/workspace/repo", remote="origin", branch="main", username="user", password="token")
    git.push(
        "/workspace/repo",
        remote="origin",
        branch="main",
        username="user",
        password="token",
    )
    git.checkout("/workspace/repo", "main")
    git.checkout_branch("/workspace/repo", "main")
    git.remote_add(
        "/workspace/repo",
        "origin",
        "https://git.example/repo.git",
        fetch=True,
        overwrite=True,
    )
    remote_url = git.remote_get("/workspace/repo", "origin")
    git.set_config("pull.rebase", "false", scope="local", path="/workspace/repo")
    config_value = git.get_config("pull.rebase", scope="local", path="/workspace/repo")

    assert cloned.path == "/workspace/repo"
    assert status.current_branch == "main"
    assert status.ahead == 1
    assert status.has_changes is True
    assert status.untracked_count == 1
    assert branches.branches == ["main", "feature/test"]
    assert branches.current_branch == "main"
    assert remote_url == "https://git.example/repo.git"
    assert config_value == "false"
    assert calls == [
        (
            "/runtime/v1/git/clone",
            {
                "json": {
                    "url": "https://git.example/repo.git",
                    "path": "/workspace/repo",
                    "branch": "main",
                    "depth": 1,
                    "envs": {"GIT_TRACE": "1"},
                    "user": "sandbox",
                    "cwd": "/workspace",
                    "timeout_seconds": 10,
                },
                "request_timeout": None,
            },
        ),
        (
            "/runtime/v1/git/dangerously_authenticate",
            {
                "json": {
                    "username": "user",
                    "password": "token",
                    "host": "git.example.com",
                    "protocol": "https",
                    "timeout_seconds": 5,
                },
                "request_timeout": None,
            },
        ),
        (
            "/runtime/v1/git/configure_user",
            {
                "json": {
                    "name": "Watasu Test",
                    "email": "test@watasu.local",
                    "scope": "local",
                    "path": "/workspace/repo",
                },
                "request_timeout": None,
            },
        ),
        (
            "/runtime/v1/git/init",
            {
                "json": {"path": "/workspace/repo", "initial_branch": "main"},
                "request_timeout": None,
            },
        ),
        (
            "/runtime/v1/git/status",
            {
                "json": {"path": "/workspace/repo", "user": "sandbox", "cwd": "/workspace"},
                "request_timeout": None,
            },
        ),
        (
            "/runtime/v1/git/branches",
            {
                "json": {"path": "/workspace/repo"},
                "request_timeout": None,
            },
        ),
        (
            "/runtime/v1/git/create_branch",
            {
                "json": {"path": "/workspace/repo", "branch": "feature/test"},
                "request_timeout": None,
            },
        ),
        (
            "/runtime/v1/git/delete_branch",
            {
                "json": {"path": "/workspace/repo", "branch": "feature/test", "force": True},
                "request_timeout": None,
            },
        ),
        (
            "/runtime/v1/git/add",
            {
                "json": {
                    "path": "/workspace/repo",
                    "files": ["README.md"],
                    "all": True,
                    "user": "sandbox",
                    "cwd": "/workspace/repo",
                },
                "request_timeout": None,
            },
        ),
        (
            "/runtime/v1/git/commit",
            {
                "json": {
                    "path": "/workspace/repo",
                    "message": "change",
                    "author_name": "Watasu Test",
                    "author_email": "test@watasu.local",
                    "allow_empty": True,
                },
                "request_timeout": None,
            },
        ),
        (
            "/runtime/v1/git/reset",
            {
                "json": {
                    "path": "/workspace/repo",
                    "mode": "hard",
                    "target": "HEAD",
                    "paths": ["README.md"],
                },
                "request_timeout": None,
            },
        ),
        (
            "/runtime/v1/git/restore",
            {
                "json": {
                    "path": "/workspace/repo",
                    "paths": ["README.md"],
                    "staged": True,
                },
                "request_timeout": None,
            },
        ),
        (
            "/runtime/v1/git/pull",
            {
                "json": {
                    "path": "/workspace/repo",
                    "branch": "main",
                    "remote": "origin",
                    "username": "user",
                    "password": "token",
                },
                "request_timeout": None,
            },
        ),
        (
            "/runtime/v1/git/push",
            {
                "json": {
                    "path": "/workspace/repo",
                    "branch": "main",
                    "remote": "origin",
                    "set_upstream": True,
                    "username": "user",
                    "password": "token",
                },
                "request_timeout": None,
            },
        ),
        (
            "/runtime/v1/git/checkout",
            {
                "json": {"path": "/workspace/repo", "ref": "main"},
                "request_timeout": None,
            },
        ),
        (
            "/runtime/v1/git/checkout",
            {
                "json": {"path": "/workspace/repo", "ref": "main"},
                "request_timeout": None,
            },
        ),
        (
            "/runtime/v1/git/remote_add",
            {
                "json": {
                    "path": "/workspace/repo",
                    "name": "origin",
                    "url": "https://git.example/repo.git",
                    "fetch": True,
                    "overwrite": True,
                },
                "request_timeout": None,
            },
        ),
        (
            "/runtime/v1/git/remote_get",
            {
                "json": {"path": "/workspace/repo", "name": "origin"},
                "request_timeout": None,
            },
        ),
        (
            "/runtime/v1/git/set_config",
            {
                "json": {
                    "key": "pull.rebase",
                    "value": "false",
                    "scope": "local",
                    "path": "/workspace/repo",
                },
                "request_timeout": None,
            },
        ),
        (
            "/runtime/v1/git/get_config",
            {
                "json": {"key": "pull.rebase", "scope": "local", "path": "/workspace/repo"},
                "request_timeout": None,
            },
        ),
    ]


def test_git_helper_keeps_reference_positional_argument_order():
    calls = []

    class DataPlane:
        def post_json(self, path, **kwargs):
            calls.append((path, kwargs["json"]))
            return {"git": {"path": "/workspace/repo", "stdout": "", "stderr": ""}}

    git = Git(DataPlane())

    git.clone("https://git.example/repo.git", "/workspace/repo", "main", 1, "user", "token")
    git.pull("/workspace/repo", "origin", "main", "user", "token")
    git.push("/workspace/repo", "origin", "main", True, "user", "token")

    assert calls[0] == (
        "/runtime/v1/git/clone",
        {
            "url": "https://git.example/repo.git",
            "path": "/workspace/repo",
            "branch": "main",
            "depth": 1,
            "username": "user",
            "password": "token",
        },
    )
    assert calls[1] == (
        "/runtime/v1/git/pull",
        {
            "path": "/workspace/repo",
            "remote": "origin",
            "branch": "main",
            "username": "user",
            "password": "token",
        },
    )
    assert calls[2] == (
        "/runtime/v1/git/push",
        {
            "path": "/workspace/repo",
            "remote": "origin",
            "branch": "main",
            "set_upstream": True,
            "username": "user",
            "password": "token",
        },
    )


def test_async_git_exposes_explicit_reference_signatures():
    clone = inspect.signature(AsyncGit.clone)
    pull = inspect.signature(AsyncGit.pull)
    push = inspect.signature(AsyncGit.push)

    assert list(clone.parameters)[:7] == [
        "self",
        "url",
        "path",
        "branch",
        "depth",
        "username",
        "password",
    ]
    assert list(pull.parameters)[:4] == ["self", "path", "remote", "branch"]
    assert list(push.parameters)[:5] == [
        "self",
        "path",
        "remote",
        "branch",
        "set_upstream",
    ]


def test_watch_handle_drains_runtime_events():
    class Socket:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    socket = Socket()
    frames = iter(
        [
            {
                "type": "events",
                "events": [
                    {
                        "type": "modify",
                        "path": "/tmp/a.txt",
                        "file": {
                            "path": "/tmp/a.txt",
                            "name": "a.txt",
                            "type": "file",
                            "bytes": 2,
                        },
                    }
                ],
            }
        ]
    )

    handle = WatchHandle(socket, frames)
    import time

    time.sleep(0.05)
    events = handle.get_new_events()

    assert events[0].type.value == "write"
    assert events[0].name == "a.txt"
    assert events[0].entry.name == "a.txt"


def test_sandbox_create_uses_base_template_and_watasu_payload(monkeypatch):
    captured = {}

    class FakeControl:
        def __init__(self, config):
            captured["config"] = config

        def post(self, path, **kwargs):
            captured["path"] = path
            captured["kwargs"] = kwargs
            return {
                "sandbox": {"id": 42},
                "session": {
                    "data_plane_url": "https://route.sandbox.watasuhost.com",
                    "token": "data",
                },
            }

    monkeypatch.setattr("watasu.sandbox_sync.main.ControlClient", FakeControl)

    sbx = Sandbox.create(
        api_key="key",
        team="watasu",
        template="base:82",
        envs={"HELLO": "world"},
        lifecycle={"on_timeout": "pause", "auto_resume": True},
        volume_mounts={
            "/workspace/cache": "cache",
            "/data/models": {"name": "models"},
        },
        network={
            "allowOut": lambda ctx: list(ctx.rules.keys()) + ["pypi.org:443"],
            "denyOut": ["10.0.0.0/8"],
            "allowPackageRegistryAccess": True,
            "rules": {
                "api.example.com": [
                    {"transform": {"headers": {"authorization": "Bearer token"}}}
                ]
            },
            "maskRequestHost": "${PORT}-sandbox.example.com",
        },
    )

    assert sbx.sandbox_id == "42"
    assert captured["path"] == "/sandboxes"
    assert captured["kwargs"]["json"]["template"] == "base:82"
    assert captured["kwargs"]["json"]["envs"] == {"HELLO": "world"}
    assert captured["kwargs"]["json"]["lifecycle"] == {
        "on_timeout": "pause",
        "auto_resume": True,
    }
    assert captured["kwargs"]["json"]["volume_mounts"] == [
        {"path": "/workspace/cache", "name": "cache"},
        {"path": "/data/models", "name": "models"},
    ]
    assert captured["kwargs"]["json"]["team"] == "watasu"
    assert captured["kwargs"]["json"]["allow_out"] == [
        "api.example.com",
        "pypi.org:443",
    ]
    assert captured["kwargs"]["json"]["deny_out"] == ["10.0.0.0/8"]
    assert captured["kwargs"]["json"]["allow_package_registry_access"] is True
    assert captured["kwargs"]["json"]["rules"] == {
        "api.example.com": [
            {"transform": {"headers": {"authorization": "Bearer token"}}}
        ]
    }
    assert captured["kwargs"]["json"]["mask_request_host"] == "${PORT}-sandbox.example.com"


def test_sandbox_create_rejects_invalid_lifecycle_boolean():
    with pytest.raises(SandboxException, match="lifecycle.auto_resume must be a boolean"):
        Sandbox.create(
            api_key="key",
            lifecycle={"on_timeout": "pause", "auto_resume": "sometimes"},
        )


def test_code_interpreter_sandbox_uses_code_interpreter_template(monkeypatch):
    captured = {}

    class FakeControl:
        def __init__(self, config):
            captured["config"] = config

        def post(self, path, **kwargs):
            captured["path"] = path
            captured["kwargs"] = kwargs
            return {
                "sandbox": {"id": "code-created"},
                "session": {
                    "data_plane_url": "https://route.sandbox.watasuhost.com",
                    "token": "data",
                },
            }

    monkeypatch.setattr("watasu.sandbox_sync.main.ControlClient", FakeControl)

    sbx = CodeInterpreterSandbox.create(api_key="key")

    assert sbx.sandbox_id == "code-created"
    assert captured["path"] == "/sandboxes"
    assert captured["kwargs"]["json"]["template"] == "code-interpreter"


def test_code_interpreter_run_code_uses_runtime_api_and_callbacks():
    calls = []

    class DataPlane:
        def post_json(self, path, **kwargs):
            calls.append((path, kwargs))
            return {
                "execution": {
                    "results": [
                        {"text": "5", "json": 5, "is_main_result": True},
                    ],
                    "logs": {
                        "stdout": ["hello"],
                        "stderr": ["warn"],
                    },
                    "error": None,
                    "execution_count": None,
                }
            }

    sbx = CodeInterpreterSandbox(
        "code",
        connection_config=ConnectionConfig(api_key="key"),
        session={
            "data_plane_url": "https://route.sandbox.watasuhost.com",
            "token": "data",
        },
    )
    sbx._data_plane = DataPlane()
    stdout = []
    stderr = []
    results = []

    execution = sbx.run_code(
        "print('hello')\n2 + 3",
        language="python",
        envs={"A": "B"},
        timeout=5,
        request_timeout=10,
        on_stdout=stdout.append,
        on_stderr=stderr.append,
        on_result=results.append,
    )

    assert execution.text == "5"
    assert stdout[0].line == "hello"
    assert stderr[0].line == "warn"
    assert stderr[0].error is True
    assert results[0].formats() == ["text", "json"]
    assert calls == [
        (
            "/runtime/v1/code/run",
            {
                "json": {
                    "code": "print('hello')\n2 + 3",
                    "language": "python",
                    "envs": {"A": "B"},
                    "timeout_seconds": 5,
                },
                "request_timeout": 10,
            },
        )
    ]


def test_code_interpreter_context_methods_use_runtime_api():
    calls = []

    class DataPlane:
        def post_json(self, path, **kwargs):
            calls.append(("POST", path, kwargs))
            if path == "/runtime/v1/code/contexts":
                return {"id": "ctx-1", "language": "python", "cwd": "/workspace/app"}
            return {}

        def get_json(self, path, **kwargs):
            calls.append(("GET", path, kwargs))
            return [{"id": "ctx-1", "language": "python", "cwd": "/workspace/app"}]

        def delete_json(self, path, **kwargs):
            calls.append(("DELETE", path, kwargs))
            return {}

    sbx = CodeInterpreterSandbox(
        "code",
        connection_config=ConnectionConfig(api_key="key"),
        session={
            "data_plane_url": "https://route.sandbox.watasuhost.com",
            "token": "data",
        },
    )
    sbx._data_plane = DataPlane()

    context = sbx.create_code_context(
        cwd="/workspace/app", language="python", request_timeout=5
    )
    contexts = sbx.list_code_contexts(request_timeout=6)
    sbx.restart_code_context(context, request_timeout=7)
    sbx.remove_code_context("ctx-1", request_timeout=8)

    assert context.id == "ctx-1"
    assert contexts[0].cwd == "/workspace/app"
    assert calls == [
        (
            "POST",
            "/runtime/v1/code/contexts",
            {
                "json": {"cwd": "/workspace/app", "language": "python"},
                "request_timeout": 5,
            },
        ),
        ("GET", "/runtime/v1/code/contexts", {"request_timeout": 6}),
        (
            "POST",
            "/runtime/v1/code/contexts/ctx-1/restart",
            {"json": {}, "request_timeout": 7},
        ),
        ("DELETE", "/runtime/v1/code/contexts/ctx-1", {"request_timeout": 8}),
    ]


def test_data_plane_post_json_accepts_empty_success_response(monkeypatch):
    calls = []

    class Response:
        status_code = 204
        content = b""
        text = ""

        def json(self):
            raise AssertionError("empty 204 responses should not be parsed")

    def request(self, method, url, **kwargs):
        calls.append((method, url, kwargs.get("json")))
        return Response()

    monkeypatch.setattr("requests.Session.request", request)

    client = DataPlaneClient(
        "https://route.sandbox.watasuhost.com",
        "data",
        ConnectionConfig(api_key="key"),
    )

    assert client.post_json("/runtime/v1/code/contexts/ctx-1/restart", json={}) == {}
    assert calls == [
        (
            "POST",
            "https://route.sandbox.watasuhost.com/runtime/v1/code/contexts/ctx-1/restart",
            {},
        )
    ]


def test_data_plane_reads_success_body_before_closing_session(monkeypatch):
    session_closed = False

    class Response:
        status_code = 200
        text = '{"ok": true}'
        _content = None

        @property
        def content(self):
            nonlocal session_closed
            if self._content is None:
                assert session_closed is False
                self._content = b'{"ok": true}'
            return self._content

        def json(self):
            return json.loads(self.content)

    def request(self, method, url, **kwargs):
        return Response()

    def close(self):
        nonlocal session_closed
        session_closed = True

    monkeypatch.setattr("requests.Session.request", request)
    monkeypatch.setattr("requests.Session.close", close)

    client = DataPlaneClient(
        "https://route.sandbox.watasuhost.com",
        "data",
        ConnectionConfig(api_key="key"),
    )

    assert client.post_json("/runtime/v1/files/write_files", json={"files": []}) == {"ok": True}
    assert session_closed is True


def test_sandbox_create_with_mcp_sends_config_to_api_without_sdk_bootstrap(monkeypatch):
    captured = {}
    commands = []

    class FakeControl:
        def __init__(self, config):
            captured["config"] = config

        def post(self, path, **kwargs):
            captured["path"] = path
            captured["kwargs"] = kwargs
            return {
                "sandbox": {
                    "id": "mcp-created",
                    "template_id": "mcp-gateway",
                    "route_token": "route-token",
                },
                "session": {
                    "data_plane_url": "https://route.sandbox.watasuhost.com",
                    "token": "data",
                },
            }

    class FakeResult:
        exit_code = 0
        stderr = ""

    def fake_run(self, cmd, **kwargs):
        commands.append((cmd, kwargs))
        return FakeResult()

    monkeypatch.setattr("watasu.sandbox_sync.main.ControlClient", FakeControl)
    monkeypatch.setattr("watasu.sandbox_sync.commands.command.Commands.run", fake_run)

    sbx = Sandbox.create(
        api_key="key",
        mcp={"server": "it's-fine", "config": {"enabled": True}},
    )

    assert sbx.sandbox_id == "mcp-created"
    assert captured["path"] == "/sandboxes"
    assert captured["kwargs"]["json"] == {
        "timeout": 300,
        "metadata": {},
        "envs": {},
        "secure": True,
        "allow_internet_access": True,
        "mcp": {"server": "it's-fine", "config": {"enabled": True}},
    }
    assert commands == []


def test_python_sandbox_main_imports():
    from watasu.sandbox.main import SandboxBase as ImportedSandboxBase
    from watasu.sandbox.main import SandboxOpts as ImportedSandboxOpts
    from watasu.sandbox.mcp import McpServer as ImportedMcpServer
    from watasu.sandbox.network import ALL_TRAFFIC as ImportedAllTraffic
    from watasu.sandbox.signature import get_signature as imported_get_signature

    assert ImportedSandboxBase is SandboxBase
    assert ImportedSandboxOpts is SandboxOpts
    assert ImportedMcpServer is McpServer
    assert ImportedAllTraffic == ALL_TRAFFIC
    assert imported_get_signature is get_signature
    signature = get_signature("/workspace/a.txt", "read", "user", "token")
    assert signature["expiration"] is None
    assert signature["signature"].startswith("v1_")
    assert Sandbox.default_template == "base"
    assert Sandbox.default_sandbox_timeout == 300
    assert Sandbox.default_mcp_template == "mcp-gateway"


def test_sandbox_list_returns_paginator_and_uses_nested_query_params(monkeypatch):
    calls = []

    class FakeControl:
        def __init__(self, config):
            pass

        def get(self, path, **kwargs):
            calls.append((path, kwargs))
            params = dict(kwargs["params"])
            if params.get("next_token") == "2":
                return {"sandboxes": [{"id": "1", "state": "ready"}]}
            return {
                "sandboxes": [{"id": "2", "state": "creating"}],
                "next_token": "2",
            }

    monkeypatch.setattr("watasu.sandbox_sync.main.ControlClient", FakeControl)

    paginator = Sandbox.list(
        api_key="key",
        team="watasu",
        query={"metadata": {"purpose": "ci"}, "state": ["running"]},
        limit=1,
    )

    first_page = paginator.next_items(request_timeout=5)
    assert paginator.has_next is True
    assert paginator.next_token == "2"
    second_page = paginator.next_items()
    assert paginator.has_next is False

    assert [item.sandbox_id for item in first_page + second_page] == ["2", "1"]
    assert calls == [
        (
            "/sandboxes",
            {
                "params": [
                    ("team", "watasu"),
                    ("limit", "1"),
                    ("query[metadata][purpose]", "ci"),
                    ("query[state][]", "running"),
                ],
                "resource": "sandbox",
                "request_timeout": 5,
            },
        ),
        (
            "/sandboxes",
            {
                "params": [
                    ("team", "watasu"),
                    ("limit", "1"),
                    ("next_token", "2"),
                    ("query[metadata][purpose]", "ci"),
                    ("query[state][]", "running"),
                ],
                "resource": "sandbox",
                "request_timeout": None,
            },
        ),
    ]


def test_sandbox_update_network_uses_snake_case_payload(monkeypatch):
    calls = []

    class FakeControl:
        def put(self, path, **kwargs):
            calls.append(("put", path, kwargs))
            return {
                "sandbox": {
                    "id": "network-sandbox",
                    "network_policy": kwargs["json"],
                }
            }

    sbx = Sandbox(
        "network-sandbox",
        connection_config=ConnectionConfig(api_key="key"),
        control=FakeControl(),
        session={
            "data_plane_url": "https://route.sandbox.watasuhost.com",
            "token": "data",
        },
    )

    assert (
        sbx.update_network(
            {
                "allowOut": lambda ctx: list(ctx.rules.keys()),
                "denyOut": ["10.0.0.0/8"],
                "allowInternetAccess": False,
                "rules": {
                    "registry.npmjs.org": [
                        {
                            "transform": {
                                "headers": {"authorization": "Bearer token"}
                            }
                        }
                    ]
                },
                "maskRequestHost": "${PORT}-sandbox.example.com",
            },
            allow_package_registry_access=True,
            request_timeout=3,
        )
        is None
    )

    assert calls == [
        (
            "put",
            "/sandboxes/network-sandbox/network",
            {
                "json": {
                    "allow_out": ["registry.npmjs.org"],
                    "deny_out": ["10.0.0.0/8"],
                    "allow_internet_access": False,
                    "allow_package_registry_access": True,
                    "rules": {
                        "registry.npmjs.org": [
                            {
                                "transform": {
                                    "headers": {"authorization": "Bearer token"}
                                }
                            }
                        ]
                    },
                    "mask_request_host": "${PORT}-sandbox.example.com",
                },
                "resource": "sandbox",
                "request_timeout": 3,
            },
        )
    ]


def test_sandbox_update_network_class_uses_snake_case_payload(monkeypatch):
    calls = []

    class FakeControl:
        def __init__(self, config):
            calls.append(("config", config.api_key))

        def put(self, path, **kwargs):
            calls.append(("put", path, kwargs))
            return {}

    monkeypatch.setattr("watasu.sandbox_sync.main.ControlClient", FakeControl)

    assert (
        Sandbox.update_network(
            "network-sandbox",
            {
                "allowOut": lambda ctx: list(ctx.rules.keys()),
                "denyOut": ["10.0.0.0/8"],
                "allowInternetAccess": False,
                "rules": {
                    "registry.npmjs.org": [
                        {
                            "transform": {
                                "headers": {"authorization": "Bearer token"}
                            }
                        }
                    ]
                },
            },
            allow_package_registry_access=True,
            api_key="key",
            request_timeout=3,
        )
        is None
    )

    assert calls == [
        ("config", "key"),
        (
            "put",
            "/sandboxes/network-sandbox/network",
            {
                "json": {
                    "allow_out": ["registry.npmjs.org"],
                    "deny_out": ["10.0.0.0/8"],
                    "allow_internet_access": False,
                    "allow_package_registry_access": True,
                    "rules": {
                        "registry.npmjs.org": [
                            {
                                "transform": {
                                    "headers": {"authorization": "Bearer token"}
                                }
                            }
                        ]
                    },
                },
                "resource": "sandbox",
                "request_timeout": 3,
            },
        ),
    ]


def test_sandbox_constructor_creates_with_default_template(monkeypatch):
    captured = {}

    class FakeControl:
        def __init__(self, config):
            captured["config"] = config

        def post(self, path, **kwargs):
            captured["path"] = path
            captured["kwargs"] = kwargs
            return {
                "sandbox": {"id": "new-sandbox"},
                "session": {
                    "data_plane_url": "https://route.sandbox.watasuhost.com",
                    "token": "data",
                },
            }

    monkeypatch.setattr("watasu.sandbox_sync.main.ControlClient", FakeControl)

    sbx = Sandbox(api_key="key", metadata={"purpose": "compat"})

    assert sbx.sandbox_id == "new-sandbox"
    assert captured["path"] == "/sandboxes"
    assert captured["kwargs"]["json"]["template"] == "base"
    assert captured["kwargs"]["json"]["metadata"] == {"purpose": "compat"}


def test_sandbox_beta_create_uses_auto_pause_payload(monkeypatch):
    captured = {}

    class FakeControl:
        def __init__(self, config):
            captured["config"] = config

        def post(self, path, **kwargs):
            captured["path"] = path
            captured["kwargs"] = kwargs
            return {
                "sandbox": {"id": "beta-created"},
                "session": {
                    "data_plane_url": "https://route.sandbox.watasuhost.com",
                    "token": "data",
                },
            }

    monkeypatch.setattr("watasu.sandbox_sync.main.ControlClient", FakeControl)

    sbx = Sandbox.beta_create(
        api_key="key", template="base", timeout=60, auto_pause=True, team="watasu"
    )

    assert sbx.sandbox_id == "beta-created"
    assert captured["path"] == "/sandboxes"
    assert captured["kwargs"]["json"]["template"] == "base"
    assert captured["kwargs"]["json"]["timeout"] == 60
    assert captured["kwargs"]["json"]["auto_pause"] is True
    assert captured["kwargs"]["json"]["team"] == "watasu"


def test_sandbox_info_parses_lifecycle():
    info = sandbox_info_from_api(
        {
            "id": "sandbox-1",
            "sandbox_domain": "sandbox.watasuhost.com",
            "template_id": "base",
            "lifecycle": {"on_timeout": "pause", "auto_resume": True},
            "cpu_count": 2,
            "memory_mb": 512,
            "envd_version": "0.6.3",
            "envd_access_token": "envd-token",
            "started_at": "2026-01-01T00:00:00Z",
            "end_at": "2026-01-01T00:05:00Z",
        }
    )

    assert info.sandbox_domain == "sandbox.watasuhost.com"
    assert info.template_id == "base"
    assert info.cpu_count == 2
    assert info.memory_mb == 512
    assert info.envd_version == "0.6.3"
    assert info._envd_access_token == "envd-token"
    assert info.started_at.isoformat() == "2026-01-01T00:00:00+00:00"
    assert info.end_at.isoformat() == "2026-01-01T00:05:00+00:00"
    assert info.lifecycle is not None
    assert info.lifecycle.on_timeout == "pause"
    assert info.lifecycle.auto_resume is True

    metrics = sandbox_metrics_from_api(
        {
            "sandbox_id": "sandbox-1",
            "cpu_count": 2,
            "cpu_used_pct": 12.5,
            "disk_total": 8192,
            "disk_used": 4096,
            "mem_total": 2048,
            "mem_used": 1024,
            "timestamp": "2026-01-01T00:00:00Z",
        }
    )

    assert metrics.sandbox_id == "sandbox-1"
    assert metrics.cpu_count == 2
    assert metrics.cpu_used_pct == 12.5
    assert metrics.disk_total == 8192
    assert metrics.disk_used == 4096
    assert metrics.mem_total == 2048
    assert metrics.mem_used == 1024
    assert metrics.timestamp.isoformat() == "2026-01-01T00:00:00+00:00"


def test_sandbox_aliases_match_connection_lifecycle_shape(monkeypatch):
    calls = []

    class FakeControl:
        def __init__(self, config):
            pass

        def get(self, path, **kwargs):
            calls.append(("get", path, kwargs))
            return {"sandbox": {"id": "existing"}}

        def post(self, path, **kwargs):
            calls.append(("post", path, kwargs))
            return {
                "sandbox": {"id": "existing"},
                "session": {
                    "data_plane_url": "https://route.sandbox.watasuhost.com",
                    "token": "data",
                },
            }

    monkeypatch.setattr("watasu.sandbox_sync.main.ControlClient", FakeControl)

    sbx = Sandbox.connect("existing", api_key="key", timeout=600)

    assert sbx.id == "existing"
    assert calls[1] == (
        "post",
        "/sandboxes/existing/resume",
        {"json": {"timeout": 600}, "resource": "sandbox", "request_timeout": 150},
    )


def test_sandbox_connect_and_timeout_use_root_snake_case_payloads(monkeypatch):
    calls = []

    class FakeControl:
        def __init__(self, config):
            pass

        def get(self, path, **kwargs):
            calls.append(("get", path, kwargs))
            return {"sandbox": {"id": "existing"}}

        def post(self, path, **kwargs):
            calls.append(("post", path, kwargs))
            if path.endswith("/resume"):
                return {
                    "sandbox": {"id": "existing"},
                    "session": {
                        "data_plane_url": "https://route.sandbox.watasuhost.com",
                        "token": "data",
                    },
                }
            return {"sandbox": {"id": "existing"}}

    monkeypatch.setattr("watasu.sandbox_sync.main.ControlClient", FakeControl)

    sbx = Sandbox.connect("existing", api_key="key", timeout=600)
    sbx.set_timeout(900)

    assert calls[1] == (
        "post",
        "/sandboxes/existing/resume",
        {"json": {"timeout": 600}, "resource": "sandbox", "request_timeout": 150},
    )
    assert calls[2] == (
        "post",
        "/sandboxes/existing/timeout",
        {"json": {"timeout": 900}, "resource": "sandbox"},
    )


def test_sandbox_pause_and_resume_use_lifecycle_routes(monkeypatch):
    calls = []

    class FakeControl:
        def __init__(self, config):
            pass

        def get(self, path, **kwargs):
            calls.append(("get", path, kwargs))
            return {"sandbox": {"id": "existing"}}

        def post(self, path, **kwargs):
            calls.append(("post", path, kwargs))
            if path.endswith("/pause"):
                return {"sandbox": {"id": "existing", "state": "stopped"}}
            if path.endswith("/resume"):
                return {
                    "sandbox": {"id": "existing", "state": "ready"},
                    "session": {
                        "data_plane_url": "https://route.sandbox.watasuhost.com",
                        "token": "data",
                    },
                }
            raise AssertionError(f"unexpected POST {path}")

    monkeypatch.setattr("watasu.sandbox_sync.main.ControlClient", FakeControl)

    sbx = Sandbox.connect("existing", api_key="key")
    calls.clear()

    assert sbx.beta_pause() is True
    assert sbx.pause() is True
    assert Sandbox.pause("existing", api_key="key") is True
    assert sbx.resume(timeout=1_200) is True

    assert calls == [
        ("post", "/sandboxes/existing/pause", {"resource": "sandbox"}),
        ("post", "/sandboxes/existing/pause", {"resource": "sandbox"}),
        ("post", "/sandboxes/existing/pause", {"resource": "sandbox"}),
        (
            "post",
            "/sandboxes/existing/resume",
            {"json": {"timeout": 1_200}, "resource": "sandbox", "request_timeout": 150},
        ),
    ]


def test_sandbox_pause_returns_false_for_already_paused_conflict(monkeypatch):
    class FakeControl:
        def __init__(self, config):
            pass

        def post(self, path, **kwargs):
            raise ConflictException("sandbox_already_paused")

    monkeypatch.setattr("watasu.sandbox_sync.main.ControlClient", FakeControl)

    assert Sandbox.beta_pause("existing", api_key="key") is False


def test_sandbox_context_manager_kills_on_exit(monkeypatch):
    killed = []

    def fake_kill(self, **kwargs):
        killed.append((self.sandbox_id, kwargs))
        return True

    monkeypatch.setattr(Sandbox, "kill", fake_kill)
    sbx = Sandbox(
        "123",
        connection_config=ConnectionConfig(api_key="key"),
        session={
            "data_plane_url": "https://route.sandbox.watasuhost.com",
            "token": "data",
        },
        sandbox={},
    )

    with sbx as active:
        assert active is sbx

    assert killed == [("123", {})]


def test_sandbox_mcp_helpers_use_exposed_port_and_gateway_token():
    calls = []

    class FakeControl:
        def get(self, path, **kwargs):
            calls.append(("control", path, kwargs))
            return {
                "sandbox_port": {
                    "url": "https://p50005-token.sandbox.watasuhost.com"
                }
            }

    class FakeFiles:
        def __init__(self):
            self.calls = []

        def read(self, path, **kwargs):
            self.calls.append((path, kwargs))
            return " gateway-token\n"

    files = FakeFiles()
    sbx = Sandbox(
        "123",
        connection_config=ConnectionConfig(api_key="key"),
        control=FakeControl(),
        session={
            "data_plane_url": "https://route.sandbox.watasuhost.com",
            "token": "data",
        },
        sandbox={},
    )
    sbx._filesystem = files

    assert sbx.get_mcp_url() == "https://p50005-token.sandbox.watasuhost.com/mcp"
    assert sbx.get_mcp_token() == "gateway-token"
    assert sbx.get_mcp_token() == "gateway-token"
    assert files.calls == [
        (
            "/etc/mcp-gateway/.token",
            {"user": "root", "request_timeout": None},
        )
    ]


def test_sandbox_is_running_uses_control_state():
    class Control:
        def __init__(self, state):
            self.state = state

        def get(self, path, **kwargs):
            return {"sandbox": {"id": "123", "state": self.state}}

    for state in ("creating", "ready", "checkpointing", "restoring", "stopping"):
        sbx = Sandbox(
            "123",
            connection_config=ConnectionConfig(api_key="key"),
            control=Control(state),
            session={
                "data_plane_url": "https://route.sandbox.watasuhost.com",
                "token": "data",
            },
            sandbox={},
        )
        assert sbx.is_running() is True

    for state in ("stopped", "destroyed", "failed", "lost", "expired"):
        sbx = Sandbox(
            "123",
            connection_config=ConnectionConfig(api_key="key"),
            control=Control(state),
            session={
                "data_plane_url": "https://route.sandbox.watasuhost.com",
                "token": "data",
            },
            sandbox={},
        )
        assert sbx.is_running() is False


def test_sandbox_create_requires_session_from_api(monkeypatch):
    class FakeControl:
        def __init__(self, config):
            pass

        def post(self, path, **kwargs):
            return {"sandbox": {"id": 42, "state": "creating"}}

    monkeypatch.setattr("watasu.sandbox_sync.main.ControlClient", FakeControl)

    with pytest.raises(Exception, match="sandbox session is required"):
        Sandbox.create(api_key="key", template="base:82")


def test_sandbox_metrics_and_snapshots_use_control_plane_routes():
    calls = []

    class Control:
        def get(self, path, **kwargs):
            calls.append(("get", path, kwargs))
            if path.endswith("/metrics"):
                return {
                    "metrics": {
                        "sandbox_id": "123",
                        "state": "ready",
                        "backend": "firecracker",
                    }
                }
            if path == "/sandbox_snapshots":
                return {
                    "snapshots": [
                        {
                            "id": 9,
                            "sandbox_id": "123",
                            "name": "ready",
                            "status": "ready",
                        }
                    ]
                }
            raise AssertionError(f"unexpected GET {path}")

        def post(self, path, **kwargs):
            calls.append(("post", path, kwargs))
            if path.endswith("/snapshots"):
                return {
                    "sandbox_checkpoint": {
                        "id": 9,
                        "sandbox_id": "123",
                        "name": "ready",
                        "status": "pending",
                    }
                }
            if path.endswith("/restore"):
                return {
                    "sandbox": {
                        "id": "restored",
                        "state": "restoring",
                        "template_id": "base",
                    }
                }
            if path.endswith("/files/upload_url"):
                return {
                    "file_url": {
                        "method": "POST",
                        "path": kwargs["json"]["path"],
                        "url": "https://signed.example/upload",
                    }
                }
            if path.endswith("/files/download_url"):
                return {
                    "file_url": {
                        "method": "GET",
                        "path": kwargs["json"]["path"],
                        "url": "https://signed.example/download",
                    }
                }
            raise AssertionError(f"unexpected POST {path}")

        def delete(self, path, **kwargs):
            calls.append(("delete", path, kwargs))
            return {"deleted": True}

    sbx = Sandbox(
        "123",
        connection_config=ConnectionConfig(api_key="key"),
        control=Control(),
        session={
            "data_plane_url": "https://route.sandbox.watasuhost.com",
            "token": "data",
        },
        sandbox={},
    )

    metrics = sbx.get_metrics(
        start=datetime.datetime(2025, 11, 4, 12, 40, tzinfo=datetime.timezone.utc),
        end=datetime.datetime(2025, 11, 4, 12, 41, tzinfo=datetime.timezone.utc),
    )
    snapshot = sbx.create_snapshot(name="ready", metadata={"reason": "test"})
    snapshots = sbx.list_snapshots().list_items()
    restored = sbx.restore(snapshot_id=snapshot.snapshot_id, timeout=120)
    deleted = sbx.delete_snapshot(snapshot.snapshot_id)
    upload_url = sbx.upload_url("/tmp/a.txt", use_signature_expiration=300)
    download_url = sbx.download_url("/tmp/a.txt")

    assert metrics[0].backend == "firecracker"
    assert snapshot.snapshot_id == "9"
    assert snapshots[0].status == "ready"
    assert restored.sandbox_id == "restored"
    assert deleted is True
    assert upload_url == "https://signed.example/upload"
    assert download_url == "https://signed.example/download"
    assert calls == [
        (
            "get",
            "/sandboxes/123/metrics",
            {
                "params": [("start", "1762260000"), ("end", "1762260060")],
                "resource": "sandbox",
                "request_timeout": None,
            },
        ),
        (
            "post",
            "/sandboxes/123/snapshots",
            {
                "json": {"name": "ready", "metadata": {"reason": "test"}},
                "resource": "sandbox",
                "request_timeout": None,
            },
        ),
        (
            "get",
            "/sandbox_snapshots",
            {
                "params": [("sandbox_id", "123")],
                "resource": "sandbox",
                "request_timeout": None,
            },
        ),
        (
            "post",
            "/sandboxes/123/restore",
            {
                "json": {"checkpoint_id": "9", "timeout_seconds": 120},
                "resource": "sandbox",
                "request_timeout": None,
            },
        ),
        (
            "delete",
            "/sandbox_snapshots/9",
            {"resource": "sandbox", "request_timeout": None},
        ),
        (
            "post",
            "/sandboxes/123/files/upload_url",
            {
                "json": {"path": "/tmp/a.txt", "use_signature_expiration": 300},
                "resource": "sandbox",
                "request_timeout": None,
            },
        ),
        (
            "post",
            "/sandboxes/123/files/download_url",
            {
                "json": {"path": "/tmp/a.txt"},
                "resource": "sandbox",
                "request_timeout": None,
            },
        ),
    ]


def test_sandbox_list_snapshots_returns_global_paginator(monkeypatch):
    calls = []

    class FakeControl:
        def __init__(self, config):
            pass

        def get(self, path, **kwargs):
            calls.append((path, kwargs))
            if kwargs["params"] == [("limit", "1")]:
                return {
                    "snapshots": [{"id": 2, "sandbox_id": "sandbox-b"}],
                    "next_token": "2",
                }
            return {"snapshots": [{"id": 1, "sandbox_id": "sandbox-a"}]}

    monkeypatch.setattr("watasu.sandbox_sync.main.ControlClient", FakeControl)

    paginator = Sandbox.list_snapshots(api_key="key", limit=1)
    first_page = paginator.next_items()
    assert paginator.has_next is True
    assert paginator.next_token == "2"
    second_page = paginator.next_items()
    assert paginator.has_next is False

    assert [item.snapshot_id for item in first_page + second_page] == ["2", "1"]
    assert calls == [
        (
            "/sandbox_snapshots",
            {
                "params": [("limit", "1")],
                "resource": "sandbox",
                "request_timeout": None,
            },
        ),
        (
            "/sandbox_snapshots",
            {
                "params": [("limit", "1"), ("next_token", "2")],
                "resource": "sandbox",
                "request_timeout": None,
            },
        ),
    ]


def test_async_sandbox_wraps_supported_control_plane_routes(monkeypatch):
    calls = []

    class FakeControl:
        def __init__(self, config):
            pass

        def post(self, path, **kwargs):
            calls.append(("post", path, kwargs))
            if path == "/sandboxes":
                return {
                    "sandbox": {"id": "async-123"},
                    "session": {
                        "data_plane_url": "https://route.sandbox.watasuhost.com",
                        "token": "data",
                    },
                }
            if path.endswith("/snapshots"):
                return {
                    "sandbox_checkpoint": {
                        "id": 10,
                        "sandbox_id": "async-123",
                        "status": "ready",
                    }
                }
            raise AssertionError(f"unexpected POST {path}")

        def get(self, path, **kwargs):
            calls.append(("get", path, kwargs))
            if path.endswith("/metrics"):
                return {"metrics": {"sandbox_id": "async-123", "cpu_count": 0}}
            if path == "/sandbox_snapshots":
                return {
                    "snapshots": [
                        {"id": 10, "sandbox_id": "async-123", "status": "ready"}
                    ]
                }
            raise AssertionError(f"unexpected GET {path}")

        def put(self, path, **kwargs):
            calls.append(("put", path, kwargs))
            return {"sandbox": {"id": "async-123", "network_policy": kwargs["json"]}}

        def delete(self, path, **kwargs):
            calls.append(("delete", path, kwargs))
            return {}

    monkeypatch.setattr("watasu.sandbox_sync.main.ControlClient", FakeControl)

    async def scenario():
        async with await AsyncSandbox.create(api_key="key") as sbx:
            metrics = await sbx.get_metrics(
                start=datetime.datetime(
                    2025, 11, 4, 12, 40, tzinfo=datetime.timezone.utc
                ),
                end=datetime.datetime(
                    2025, 11, 4, 12, 41, tzinfo=datetime.timezone.utc
                ),
            )
            snapshot = await sbx.create_snapshot()
            snapshots = await sbx.list_snapshots().list_items()
            await sbx.update_network(
                {"allowInternetAccess": False}, request_timeout=2
            )
            await AsyncSandbox.update_network(
                "async-123",
                {"allowInternetAccess": True},
                api_key="key",
                request_timeout=4,
            )
            return sbx.sandbox_id, metrics, snapshot, snapshots

    sandbox_id, metrics, snapshot, snapshots = asyncio.run(scenario())

    assert sandbox_id == "async-123"
    assert metrics[0].cpu_count == 0
    assert snapshot.snapshot_id == "10"
    assert snapshots[0].snapshot_id == "10"
    assert calls == [
        (
            "post",
            "/sandboxes",
            {
                "json": {
                    "template": "base",
                    "timeout": 300,
                    "metadata": {},
                    "envs": {},
                    "secure": True,
                    "allow_internet_access": True,
                },
                "resource": "sandbox",
                "request_timeout": 150,
            },
        ),
        (
            "get",
            "/sandboxes/async-123/metrics",
            {
                "params": [("start", "1762260000"), ("end", "1762260060")],
                "resource": "sandbox",
                "request_timeout": None,
            },
        ),
        (
            "post",
            "/sandboxes/async-123/snapshots",
            {"json": {}, "resource": "sandbox", "request_timeout": None},
        ),
        (
            "get",
            "/sandbox_snapshots",
            {
                "params": [("sandbox_id", "async-123")],
                "resource": "sandbox",
                "request_timeout": None,
            },
        ),
        (
            "put",
            "/sandboxes/async-123/network",
            {
                "json": {"allow_internet_access": False},
                "resource": "sandbox",
                "request_timeout": 2,
            },
        ),
        (
            "put",
            "/sandboxes/async-123/network",
            {
                "json": {"allow_internet_access": True},
                "resource": "sandbox",
                "request_timeout": 4,
            },
        ),
        ("delete", "/sandboxes/async-123", {"resource": "sandbox"}),
    ]


def test_template_builder_sends_snake_case_payloads(monkeypatch, tmp_path):
    monkeypatch.setenv("WATASU_API_KEY", "key")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n")
    calls = []

    class Response:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.content = json.dumps(payload).encode()
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    def request(self, method, url, **kwargs):
        calls.append((method, url, kwargs.get("json"), kwargs.get("params")))
        if url.endswith("/templates"):
            return Response(
                201,
                {
                    "template_build": {
                        "template_id": "42",
                        "build_id": "99",
                        "alias": "python-ci",
                        "name": "python-ci:stable",
                        "tags": ["stable"],
                    }
                },
            )
        if "/templates/42/builds/99/status" in url:
            return Response(
                200,
                {
                    "template_id": "42",
                    "build_id": "99",
                    "status": "ready",
                    "log_entries": [
                        {
                            "timestamp": "2026-06-15T00:00:00Z",
                            "level": "info",
                            "message": "done",
                        }
                    ],
                    "logs": ["done"],
                },
            )
        raise AssertionError(f"unexpected request {method} {url}")

    monkeypatch.setattr("requests.Session.request", request)

    template = (
        Template(file_context_path=str(tmp_path))
        .from_python_image("3.12")
        .apt_install(["git"])
        .pip_install(["pytest"])
        .copy("src/app.py", "/workspace/app.py", mode=0o755, user="root:root")
        .set_envs({"TOKEN": "secret"})
        .run_cmd("echo ready")
    )

    build = Template.build_in_background(
        template,
        "python-ci:stable",
        tags=["stable"],
        cpu_count=4,
        memory_mb=4096,
        skip_cache=True,
        team="watasu",
    )
    status = Template.get_build_status(build, logs_offset=1)

    assert build.template_id == "42"
    assert status.status == TemplateBuildStatus.READY
    assert status.log_entries[0].message == "done"
    assert calls == [
        (
            "POST",
            "https://api.watasu.io/v1/templates",
            {
                "name": "python-ci:stable",
                "tags": ["stable"],
                "cpu_count": 4,
                "memory_mb": 4096,
                "skip_cache": True,
                "build_spec": {
                    "from_image": "python:3.12",
                    "packages": {"apt": ["git"], "pip": ["pytest"]},
                    "files": [
                        {
                            "path": "/workspace/app.py",
                            "source_path": "src/app.py",
                            "content_b64": base64.b64encode(b"print('ok')\n").decode(
                                "ascii"
                            ),
                            "mode": 493,
                            "user": "root:root",
                        }
                    ],
                    "setup": ["echo ready"],
                    "env": {"TOKEN": "secret"},
                },
                "team": "watasu",
            },
            None,
        ),
        (
            "GET",
            "https://api.watasu.io/v1/templates/42/builds/99/status",
            None,
            {"logs_offset": 1},
        ),
    ]


def test_template_build_accepts_name_alias_and_tags_shapes(monkeypatch):
    monkeypatch.setenv("WATASU_API_KEY", "key")
    calls = []

    class Response:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.content = json.dumps(payload).encode()
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    def request(self, method, url, **kwargs):
        calls.append(kwargs.get("json"))
        return Response(
            201,
            {
                "template_build": {
                    "template_id": "template-id",
                    "build_id": "build-id",
                    "name": kwargs["json"]["name"],
                    "alias": kwargs["json"]["name"],
                    "tags": kwargs["json"].get("tags", []),
                }
            },
        )

    monkeypatch.setattr("requests.Session.request", request)

    first = Template.build_in_background(Template(), name="python-ci")
    second = Template.build_in_background(
        Template(),
        alias="python-ci:stable",
        tags=["stable"],
    )

    assert first.name == "python-ci"
    assert second.tags == ["stable"]
    assert calls == [
        {
            "name": "python-ci",
            "cpu_count": 2,
            "memory_mb": 1024,
            "skip_cache": False,
            "build_spec": {},
        },
        {
            "name": "python-ci:stable",
            "tags": ["stable"],
            "cpu_count": 2,
            "memory_mb": 1024,
            "skip_cache": False,
            "build_spec": {},
        },
    ]

    with pytest.raises(InvalidArgumentException, match="template build name"):
        Template.build_in_background(Template())


def test_template_builder_serializers_and_mcp_helper(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n")
    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.12\n"
        "WORKDIR /workspace\n"
        "COPY src /workspace/src\n"
        "RUN python3 -m pip install pytest\n"
        "ENV PYTHONUNBUFFERED=1\n"
        "CMD python3 /workspace/src/app.py\n"
    )

    template = (
        Template(file_context_path=str(tmp_path))
        .from_python_image("3.12")
        .apt_install(["git"])
        .pip_install(["pytest"])
        .copy("src/app.py", "/workspace/app.py")
        .run_cmd("echo ready")
    )

    assert json.loads(Template.to_json(template)) == {
        "from_image": "python:3.12",
        "packages": {"apt": ["git"], "pip": ["pytest"]},
        "files": [
            {
                "path": "/workspace/app.py",
                "source_path": "src/app.py",
                "content_b64": base64.b64encode(b"print('ok')\n").decode("ascii"),
            }
        ],
        "setup": ["echo ready"],
    }
    assert Template.to_dockerfile(template) == (
        "FROM python:3.12\n"
        "RUN apt-get update && apt-get install -y git\n"
        "RUN python3 -m pip install pytest\n"
        "COPY src/app.py /workspace/app.py\n"
        "RUN echo ready\n"
    )

    assert Template(file_context_path=str(tmp_path)).from_dockerfile(
        "Dockerfile"
    ).to_build_spec() == {
        "from_image": "python:3.12",
        "files": [
            {
                "path": "/workspace/src/app.py",
                "source_path": "src/app.py",
                "content_b64": base64.b64encode(b"print('ok')\n").decode("ascii"),
            }
        ],
        "setup": ["cd /workspace && python3 -m pip install pytest"],
        "env": {"PYTHONUNBUFFERED": "1"},
        "start_cmd": "python3 /workspace/src/app.py",
        "ready_cmd": "sleep 20",
    }

    assert Template().from_image(
        "python:3.12",
        username="registry-user",
        password="registry-password",
    ).to_build_spec() == {
        "from_image": "python:3.12",
        "from_image_registry": {
            "type": "registry",
            "username": "registry-user",
            "password": "registry-password",
        },
    }
    assert list(inspect.signature(AsyncTemplate.build).parameters)[:6] == [
        "template",
        "name",
        "alias",
        "tags",
        "cpu_count",
        "memory_mb",
    ]
    assert list(inspect.signature(AsyncCommands.connect).parameters)[:5] == [
        "self",
        "pid",
        "timeout",
        "request_timeout",
        "on_stdout",
    ]
    assert list(inspect.signature(AsyncFilesystem.read).parameters)[:5] == [
        "self",
        "path",
        "format",
        "user",
        "request_timeout",
    ]
    assert list(inspect.signature(AsyncFilesystem.write).parameters)[:5] == [
        "self",
        "path",
        "data",
        "user",
        "request_timeout",
    ]
    assert list(inspect.signature(AsyncFilesystem.watch_dir).parameters)[:8] == [
        "self",
        "path",
        "on_event",
        "on_exit",
        "user",
        "request_timeout",
        "timeout",
        "recursive",
    ]
    assert list(inspect.signature(AsyncPty.create).parameters)[:4] == [
        "self",
        "size",
        "on_data",
        "user",
    ]

    mcp_template = Template().from_template("mcp-gateway").add_mcp_server(
        ["exa", "brave"]
    )
    assert mcp_template.to_build_spec() == {
        "from_template": "mcp-gateway",
        "setup": ["mcp-gateway pull exa brave"],
    }

    with pytest.raises(BuildException, match="mcp-gateway"):
        Template().add_mcp_server("exa")

    assert Template().from_aws_registry(
        "image", "key", "secret", "us-east-1"
    ).to_build_spec() == {
        "from_image": "image",
        "from_image_registry": {
            "type": "aws",
            "aws_access_key_id": "key",
            "aws_secret_access_key": "secret",
            "aws_region": "us-east-1",
        },
    }

    assert Template().from_gcp_registry("image", {"project_id": "test"}).to_build_spec() == {
        "from_image": "image",
        "from_image_registry": {
            "type": "gcp",
            "service_account_json": {"project_id": "test"},
        },
    }


def test_ready_timeout_uses_milliseconds_with_minimum_one_second():
    assert wait_for_timeout(500).get_cmd() == "sleep 1"
    assert wait_for_timeout(2500).get_cmd() == "sleep 2"


def test_template_alias_and_tag_helpers(monkeypatch):
    monkeypatch.setenv("WATASU_API_KEY", "key")
    calls = []

    class Response:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload if payload is not None else {}
            self.content = b"" if status_code == 204 else json.dumps(self._payload).encode()
            self.text = "" if status_code == 204 else json.dumps(self._payload)

        def json(self):
            return self._payload

    def request(self, method, url, **kwargs):
        calls.append((method, url, kwargs.get("json")))
        if url.endswith("/templates/aliases/missing"):
            return Response(404, {"error": "not_found"})
        if "/templates/aliases/" in url:
            return Response(200, {"template": {"slug": "python-ci"}})
        if url.endswith("/templates/tags") and method == "POST":
            return Response(200, {"build_id": "99", "tags": ["stable", "prod"]})
        if url.endswith("/templates/tags") and method == "DELETE":
            return Response(204)
        if url.endswith("/templates/python-ci/tags"):
            return Response(
                200,
                [
                    {
                        "tag": "prod",
                        "build_id": "99",
                        "created_at": "2026-06-15T00:00:00Z",
                    }
                ],
            )
        raise AssertionError(f"unexpected request {method} {url}")

    monkeypatch.setattr("requests.Session.request", request)

    assert Template.exists("python-ci") is True
    assert Template.alias_exists("missing") is False
    assert Template.assign_tags("python-ci:stable", ["prod"]).tags == ["stable", "prod"]
    Template.remove_tags("python-ci", "prod")
    tags = Template.get_tags("python-ci")

    assert tags[0].tag == "prod"
    assert tags[0].build_id == "99"
    assert calls == [
        ("GET", "https://api.watasu.io/v1/templates/aliases/python-ci", None),
        ("GET", "https://api.watasu.io/v1/templates/aliases/missing", None),
        (
            "POST",
            "https://api.watasu.io/v1/templates/tags",
            {"target": "python-ci:stable", "tags": ["prod"]},
        ),
        (
            "DELETE",
            "https://api.watasu.io/v1/templates/tags",
            {"name": "python-ci", "tags": ["prod"]},
        ),
        ("GET", "https://api.watasu.io/v1/templates/python-ci/tags", None),
    ]
