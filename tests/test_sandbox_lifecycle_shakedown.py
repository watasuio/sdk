import importlib.util
import datetime as dt
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "sandbox_lifecycle_shakedown.py"
)
SPEC = importlib.util.spec_from_file_location("sandbox_lifecycle_shakedown", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
shakedown = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = shakedown
SPEC.loader.exec_module(shakedown)


def test_redact_never_serializes_control_or_data_plane_credentials():
    value = {
        "session": {
            "access_token": "access-secret",
            "token": "data-secret",
            "data_plane_url": "https://secret-route.example",
            "runtime_generation": 7,
        },
        "nested": [{"authorization": "Bearer secret"}],
    }

    rendered = shakedown._safe_json(value)

    assert "access-secret" not in rendered
    assert "data-secret" not in rendered
    assert "secret-route" not in rendered
    assert "Bearer secret" not in rendered
    assert rendered.count("[redacted]") == 4
    assert '"runtime_generation": 7' in rendered


def test_session_info_requires_a_complete_session_and_prefers_access_token():
    session = shakedown._session_info(
        {
            "session": {
                "data_plane_url": "https://route.example",
                "access_token": "access",
                "token": "fallback",
                "runtime_generation": 9,
            }
        },
        "test",
    )

    assert session.data_plane_url == "https://route.example"
    assert session.token == "access"
    assert session.runtime_generation == 9

    with pytest.raises(shakedown.ShakedownFailure, match="data_plane_url"):
        shakedown._session_info({"session": {"token": "present"}}, "test")


def test_percentile_uses_nearest_rank_for_small_and_large_samples():
    assert shakedown._percentile([5], 0.95) == 5
    assert shakedown._percentile([10, 20, 30, 40], 0.50) == 20
    assert shakedown._percentile(range(1, 101), 0.95) == 95

    with pytest.raises(ValueError, match="at least one"):
        shakedown._percentile([], 0.95)


def test_cleanup_deletes_once_then_waits_for_terminal_state(monkeypatch):
    runner = shakedown.LifecycleShakedown.__new__(shakedown.LifecycleShakedown)
    runner.args = SimpleNamespace(
        keep_on_failure=False,
        request_timeout_seconds=30,
        cleanup_timeout_seconds=1,
        state_poll_seconds=0,
    )
    runner._created = {"41": object()}
    runner._failures = []
    runner._cleanup_failures = []
    runner.recorder = SimpleNamespace(note=lambda *_args, **_kwargs: None)

    listed = iter(
        [
            {"41": "ready", "42": "destroyed"},
            {"41": "destroying", "42": "destroyed"},
            {"41": "destroyed", "42": "destroyed"},
        ]
    )
    runner._list_owned_sandbox_states = lambda: next(listed)
    deletes = []

    def control(operation, method, path, **_kwargs):
        deletes.append((operation, method, path))
        return 202, {}

    runner.http = SimpleNamespace(control=control)
    monkeypatch.setattr(shakedown.time, "sleep", lambda _seconds: None)

    runner._cleanup_owned_sandboxes()

    assert deletes == [("cleanup_delete", "DELETE", "/sandboxes/41")]
    assert runner._cleanup_failures == []


def test_timeout_soak_observes_an_already_stopped_sandbox_after_its_deadline():
    runner = shakedown.LifecycleShakedown.__new__(shakedown.LifecycleShakedown)
    runner.args = SimpleNamespace(
        soak_grace_seconds=0,
        soak_poll_seconds=0,
        request_timeout_seconds=1,
        state_timeout_seconds=1,
    )
    runner.started_at = shakedown.time.monotonic() - 60
    notes = []
    runner.recorder = SimpleNamespace(
        note=lambda kind, **values: notes.append((kind, values))
    )
    requests = []

    def control(operation, method, path, **_kwargs):
        requests.append((operation, method, path))
        return 200, {"sandbox": {"id": "41", "state": "stopped", "node": "node-a"}}

    runner.http = SimpleNamespace(control=control)
    runner._read_file = lambda *_args, **_kwargs: "marker"
    runner._wait_for_state = lambda *_args, **_kwargs: {}
    runner._connect = lambda *_args, **_kwargs: None
    runner._assert_process_running = lambda *_args, **_kwargs: None
    runner._assert_marker = lambda *_args, **_kwargs: None

    record = SimpleNamespace(
        sandbox_id="41",
        timeout_seconds=5,
        deadline_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1),
        session=shakedown.SessionInfo("https://route.example", "token"),
        sandbox_payload={},
        marker_path="/tmp/marker",
        marker_value="marker",
        heartbeat_pid="123",
        node="node-a",
        node_history=["node-a"],
    )

    runner._run_timeout_soak(record)

    assert requests == [("soak_state_probe", "GET", "/sandboxes/41")]
    assert notes[-1][0] == "timeout_soak_complete"


def test_parse_api_datetime_requires_an_absolute_timestamp():
    assert shakedown._parse_api_datetime("2026-08-06T02:10:00Z", "deadline") == dt.datetime(
        2026, 8, 6, 2, 10, tzinfo=dt.timezone.utc
    )

    with pytest.raises(shakedown.ShakedownFailure, match="timezone"):
        shakedown._parse_api_datetime("2026-08-06T02:10:00", "deadline")
