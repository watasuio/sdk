from __future__ import annotations

import json

import pytest

from watasu import CommandExitException, CommandResult, ConnectionConfig, Sandbox
from watasu._transport.process_ws import ProcessSocket
from watasu.sandbox.filesystem.filesystem import FileType
from watasu.sandbox_sync.filesystem.filesystem import Filesystem
from watasu.sandbox_sync.commands.command_handle import CommandHandle


def test_connection_config_defaults_to_watasu_hosts(monkeypatch):
    monkeypatch.setenv("WATASU_API_KEY", "key")
    config = ConnectionConfig()

    assert config.api_key == "key"
    assert config.api_url == "https://api.watasu.io/v1"
    assert config.data_plane_domain == "watasuhost.com"
    assert config.auth_headers["Authorization"] == "Bearer key"


def test_connection_config_accepts_access_token_alias(monkeypatch):
    monkeypatch.delenv("WATASU_API_KEY", raising=False)

    config = ConnectionConfig(access_token="alias-key")

    assert config.api_key == "alias-key"
    assert config.auth_headers["Authorization"] == "Bearer alias-key"


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


def test_process_socket_base64_encodes_stdin_frames():
    sent = []

    class FakeWebSocket:
        def send(self, payload):
            sent.append(payload)

    socket = ProcessSocket("https://sandbox.example", "token", "/runtime/v1/process")
    socket._ws = FakeWebSocket()

    socket.send_stdin("hi\n")

    assert json.loads(sent[0]) == {"type": "stdin", "data": "aGkK"}


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
        team="bridgeapp",
        template="base:82",
        envs={"HELLO": "world"},
    )

    assert sbx.sandbox_id == "42"
    assert captured["path"] == "/sandboxes"
    assert captured["kwargs"]["json"]["template_id"] == "base:82"
    assert captured["kwargs"]["json"]["env_vars"] == {"HELLO": "world"}
    assert captured["kwargs"]["json"]["team"] == "bridgeapp"


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
    assert captured["kwargs"]["json"]["template_id"] == "base"
    assert captured["kwargs"]["json"]["metadata"] == {"purpose": "compat"}


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
            if path.endswith("/connect"):
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
        "/sandboxes/existing/connect",
        {"json": {"timeout": 600}, "resource": "sandbox", "request_timeout": 150},
    )
    assert calls[2] == (
        "post",
        "/sandboxes/existing/timeout",
        {"json": {"timeout": 900}, "resource": "sandbox"},
    )


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
