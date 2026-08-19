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


def test_destroy_race_issues_only_successful_duplicate_deletes():
    runner = shakedown.LifecycleShakedown.__new__(shakedown.LifecycleShakedown)
    runner.args = SimpleNamespace(
        destroy_race_sandboxes=1,
        sandbox_timeout_seconds=30,
        storm_concurrency=4,
        request_timeout_seconds=30,
        state_timeout_seconds=30,
    )
    runner.recorder = SimpleNamespace(note=lambda *_args, **_kwargs: None)
    runner._sleep_create_interval = lambda _seconds: None
    runner._create_sandbox = lambda **_kwargs: SimpleNamespace(sandbox_id="41")
    runner._prime_sandbox = lambda _record: None
    runner._wait_until_deleted = lambda *_args: None
    calls = []

    def control(operation, method, path, **kwargs):
        calls.append((operation, method, path, kwargs["allowed"]))
        return 202, {"sandbox": {"id": "41", "state": "destroying"}}

    runner.http = SimpleNamespace(control=control)

    runner._run_destroy_races(start_ordinal=1, create_interval=0)

    assert calls == [
        ("destroy_race_delete", "DELETE", "/sandboxes/41", {202})
    ] * 4


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


def test_request_storm_completes_every_request_across_every_worker():
    runner = shakedown.LifecycleShakedown.__new__(shakedown.LifecycleShakedown)
    runner.args = SimpleNamespace(
        request_storm_count=120,
        request_storm_workers=4,
        request_timeout_seconds=1,
        data_plane_timeout_seconds=1,
    )
    runner.started_at = shakedown.time.monotonic()
    runner.recorder = shakedown.Recorder(runner.started_at)
    runner._request_storm_result = None
    records = [
        SimpleNamespace(
            sandbox_id=str(index),
            role="storm-soak" if index == 0 else "avalanche",
            marker_path=f"/tmp/marker-{index}",
            marker_value=f"marker-{index}",
            heartbeat_path=f"/tmp/heartbeat-{index}",
        )
        for index in range(3)
    ]

    def control(operation, method, path, *, sandbox_id, **kwargs):
        assert kwargs["allowed"] == {200}
        assert kwargs["echo"] is False
        runner.recorder.event(
            operation=operation,
            method=method,
            status=200,
            elapsed_ms=1,
            sandbox_id=sandbox_id,
            echo=False,
        )
        state = "stopped" if sandbox_id == "0" else "ready"
        return 200, {"sandbox": {"id": sandbox_id, "state": state}}

    def read_file(record, path, *, operation, echo):
        assert echo is False
        runner.recorder.event(
            operation=operation,
            method="GET",
            status=200,
            elapsed_ms=1,
            sandbox_id=record.sandbox_id,
            echo=False,
        )
        if path == record.marker_path:
            return record.marker_value
        assert path == record.heartbeat_path
        return "1\n2\n"

    runner.http = SimpleNamespace(control=control)
    runner._read_file = read_file

    runner._run_request_storm(records)

    assert runner._request_storm_result == {
        "requested": 120,
        "completed": 120,
        "successful": 120,
        "failed": 0,
        "workers": 4,
        "threads_used": 4,
        "sandboxes": 3,
        "elapsed_seconds": runner._request_storm_result["elapsed_seconds"],
        "http_status_counts": {"200": 120},
        "latencies": {
            "request_storm_heartbeat": {
                "count": 39,
                "min_ms": 1,
                "p50_ms": 1,
                "p95_ms": 1,
                "p99_ms": 1,
                "max_ms": 1,
            },
            "request_storm_marker": {
                "count": 39,
                "min_ms": 1,
                "p50_ms": 1,
                "p95_ms": 1,
                "p99_ms": 1,
                "max_ms": 1,
            },
            "request_storm_show": {
                "count": 42,
                "min_ms": 1,
                "p50_ms": 1,
                "p95_ms": 1,
                "p99_ms": 1,
                "max_ms": 1,
            },
        },
        "failures": [],
    }
