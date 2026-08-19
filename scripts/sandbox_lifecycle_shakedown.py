#!/usr/bin/env python3
"""Production-safe, public-API shakedown for the Watasu sandbox lifecycle.

The shakedown owns every sandbox it creates through a unique metadata marker and
deletes those sandboxes in ``finally`` and waits for each delete operation to
reach a terminal state. It deliberately races duplicate stop, pause, connect,
resume, and delete requests while keeping every request valid for the lifecycle
state in which it is issued. It also exercises data-plane auto-resume and a real
timeout soak. A run is successful only when sandbox identity, files, and a live
guest process survive every non-destructive transition, every request returns
its successful contract status, and all test sandboxes are gone at the end.

Authentication is read from ``WATASU_API_KEY`` or, by default, from
``watasu token``. Tokens and data-plane URLs are never written to the report.
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import dataclasses
import datetime as dt
import json
import math
import os
from pathlib import Path
import random
import shlex
import statistics
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlencode

import requests


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PYTHON_PACKAGE_ROOT = REPOSITORY_ROOT / "python"
if str(PYTHON_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_PACKAGE_ROOT))

from watasu.connection_config import ConnectionConfig  # noqa: E402
from watasu.sandbox_sync.main import Sandbox  # noqa: E402


TERMINAL_FAILURE_STATES = {"failed", "lost", "expired", "destroyed"}
TERMINAL_CLEANUP_STATES = {"destroyed", "expired"}
REDACTED_KEYS = {
    "access_token",
    "authorization",
    "data_plane_url",
    "token",
    "traffic_access_token",
}


class ShakedownFailure(RuntimeError):
    """One lifecycle invariant was violated."""


@dataclasses.dataclass(frozen=True)
class SessionInfo:
    data_plane_url: str
    token: str
    runtime_generation: Optional[int] = None


@dataclasses.dataclass
class SandboxRecord:
    sandbox_id: str
    ordinal: int
    role: str
    node: str
    initial_node: str
    timeout_seconds: int
    deadline_at: dt.datetime
    marker_path: str
    marker_value: str
    heartbeat_path: str
    session: SessionInfo
    sandbox_payload: Dict[str, Any]
    heartbeat_pid: Optional[str] = None
    cycles_completed: int = 0
    node_history: List[str] = dataclasses.field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.node_history:
            self.node_history.append(self.node)


@dataclasses.dataclass(frozen=True)
class Event:
    at: str
    elapsed_ms: int
    operation: str
    method: str
    status: int
    sandbox_id: Optional[str]
    state: Optional[str]
    node: Optional[str]


class Recorder:
    def __init__(self, started_at: float) -> None:
        self.started_at = started_at
        self._events: List[Event] = []
        self._notes: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def event(
        self,
        *,
        operation: str,
        method: str,
        status: int,
        elapsed_ms: int,
        sandbox_id: Optional[str] = None,
        state: Optional[str] = None,
        node: Optional[str] = None,
        echo: bool = True,
    ) -> None:
        item = Event(
            at=dt.datetime.now(dt.timezone.utc).isoformat(),
            elapsed_ms=elapsed_ms,
            operation=operation,
            method=method,
            status=status,
            sandbox_id=sandbox_id,
            state=state,
            node=node,
        )
        with self._lock:
            self._events.append(item)
        if not echo:
            return
        identity = f" sandbox={sandbox_id}" if sandbox_id else ""
        state_text = f" state={state}" if state else ""
        node_text = f" node={node}" if node else ""
        print(
            f"[+{time.monotonic() - self.started_at:7.2f}s] "
            f"{operation} {method} HTTP {status}{identity}{state_text}{node_text} "
            f"{elapsed_ms}ms",
            flush=True,
        )

    def note(self, kind: str, **values: Any) -> None:
        item = {
            "at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "kind": kind,
            **values,
        }
        with self._lock:
            self._notes.append(item)
        rendered = " ".join(f"{key}={value}" for key, value in values.items())
        print(
            f"[+{time.monotonic() - self.started_at:7.2f}s] {kind} {rendered}".rstrip(),
            flush=True,
        )

    def snapshot(self) -> Tuple[List[Event], List[Dict[str, Any]]]:
        with self._lock:
            return list(self._events), list(self._notes)


class HttpTransport:
    def __init__(
        self,
        *,
        api_url: str,
        token: str,
        recorder: Recorder,
        request_timeout_seconds: float,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.recorder = recorder
        self.request_timeout_seconds = request_timeout_seconds
        self._local = threading.local()

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.trust_env = False
            self._local.session = session
        return session

    def control(
        self,
        operation: str,
        method: str,
        path: str,
        *,
        sandbox_id: Optional[str] = None,
        body: Optional[Dict[str, Any]] = None,
        params: Optional[Sequence[Tuple[str, str]]] = None,
        headers: Optional[Dict[str, str]] = None,
        allowed: Set[int],
        timeout_seconds: Optional[float] = None,
        echo: bool = True,
    ) -> Tuple[int, Dict[str, Any]]:
        request_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            **dict(headers or {}),
        }
        started = time.monotonic()
        try:
            response = self._session().request(
                method,
                f"{self.api_url}/{path.lstrip('/')}",
                json=body,
                params=params,
                headers=request_headers,
                timeout=(10, timeout_seconds or self.request_timeout_seconds),
            )
        except requests.RequestException as error:
            raise ShakedownFailure(
                f"{operation} transport failure for sandbox "
                f"{sandbox_id or '[unallocated]'}: {type(error).__name__}: {error}"
            ) from error

        elapsed_ms = round((time.monotonic() - started) * 1000)
        payload = _response_json(response)
        sandbox = payload.get("sandbox") if isinstance(payload, dict) else None
        state = sandbox.get("state") if isinstance(sandbox, dict) else None
        node = sandbox.get("node") if isinstance(sandbox, dict) else None
        response_sandbox_id = (
            str(sandbox.get("id"))
            if isinstance(sandbox, dict) and sandbox.get("id") is not None
            else sandbox_id
        )
        self.recorder.event(
            operation=operation,
            method=method,
            status=response.status_code,
            elapsed_ms=elapsed_ms,
            sandbox_id=response_sandbox_id,
            state=state,
            node=node,
            echo=echo,
        )

        if response.status_code >= 500:
            raise ShakedownFailure(
                f"{operation} leaked HTTP {response.status_code} for sandbox "
                f"{sandbox_id or '[unallocated]'}: {_safe_json(payload)}"
            )
        if response.status_code not in allowed:
            raise ShakedownFailure(
                f"{operation} returned HTTP {response.status_code}; expected "
                f"{sorted(allowed)}: {_safe_json(payload)}"
            )
        return response.status_code, payload

    def data_plane(
        self,
        operation: str,
        method: str,
        session: SessionInfo,
        path: str,
        *,
        sandbox_id: str,
        body: Optional[bytes] = None,
        params: Optional[Dict[str, str]] = None,
        allowed: Set[int] = {200},
        timeout_seconds: Optional[float] = None,
        echo: bool = True,
    ) -> Tuple[int, bytes]:
        url = f"{session.data_plane_url.rstrip('/')}/{path.lstrip('/')}"
        if params:
            url = f"{url}?{urlencode(params)}"
        started = time.monotonic()
        try:
            response = self._session().request(
                method,
                url,
                data=body,
                headers={
                    "Authorization": f"Bearer {session.token}",
                    "Connection": "close",
                },
                timeout=(10, timeout_seconds or self.request_timeout_seconds),
            )
        except requests.RequestException as error:
            raise ShakedownFailure(
                f"{operation} data-plane transport failure for sandbox "
                f"{sandbox_id}: {type(error).__name__}: {error}"
            ) from error

        elapsed_ms = round((time.monotonic() - started) * 1000)
        self.recorder.event(
            operation=operation,
            method=method,
            status=response.status_code,
            elapsed_ms=elapsed_ms,
            sandbox_id=sandbox_id,
            echo=echo,
        )
        if response.status_code >= 500:
            raise ShakedownFailure(
                f"{operation} data plane leaked HTTP {response.status_code} "
                f"for sandbox {sandbox_id}: {response.text[:1000]}"
            )
        if response.status_code not in allowed:
            raise ShakedownFailure(
                f"{operation} data plane returned HTTP {response.status_code}; "
                f"expected {sorted(allowed)} for sandbox {sandbox_id}: "
                f"{response.text[:1000]}"
            )
        return response.status_code, response.content


class LifecycleShakedown:
    def __init__(self, args: argparse.Namespace, token: str) -> None:
        self.args = args
        self.token = token
        self.started_at = time.monotonic()
        self.started_wall = dt.datetime.now(dt.timezone.utc)
        self.run_id = args.run_id or f"{self.started_wall:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:10]}"
        self.seed = args.seed if args.seed is not None else random.SystemRandom().randrange(2**63)
        self.recorder = Recorder(self.started_at)
        self.http = HttpTransport(
            api_url=args.api_url,
            token=token,
            recorder=self.recorder,
            request_timeout_seconds=args.request_timeout_seconds,
        )
        self.sdk_config = ConnectionConfig(
            api_key=token,
            api_url=args.api_url,
            request_timeout=args.data_plane_timeout_seconds,
        )
        self._created: Dict[str, SandboxRecord] = {}
        self._created_lock = threading.Lock()
        self._failures: List[str] = []
        self._failure_lock = threading.Lock()
        self._cleanup_failures: List[str] = []
        self._request_storm_result: Optional[Dict[str, Any]] = None

    def run(self) -> Dict[str, Any]:
        self.recorder.note(
            "shakedown_start",
            run_id=self.run_id,
            seed=self.seed,
            sandboxes=self.args.sandboxes,
            cycles=self.args.cycles,
            soak_timeout_seconds=self.args.soak_timeout_seconds,
        )
        try:
            limits = self._limits()
            total_requested = self.args.sandboxes + self.args.destroy_race_sandboxes + 3
            available = limits["max_running_instances"] - limits["running_instances"]
            if total_requested > available:
                raise ShakedownFailure(
                    f"shakedown needs {total_requested} running slots but only {available} "
                    "are currently available"
                )

            create_interval = max(
                self.args.create_interval_seconds,
                1.0 / max(1, limits["create_rate_per_second"]) + 0.10,
            )
            storm_soak = self._create_sandbox(
                ordinal=0,
                role="storm-soak",
                timeout_seconds=self.args.soak_timeout_seconds,
            )
            self._prime_sandbox(storm_soak)

            workers: List[SandboxRecord] = []
            for ordinal in range(1, self.args.sandboxes + 1):
                self._sleep_create_interval(create_interval)
                record = self._create_sandbox(
                    ordinal=ordinal,
                    role="avalanche",
                    timeout_seconds=self.args.sandbox_timeout_seconds,
                )
                self._prime_sandbox(record)
                workers.append(record)

            self._require_fleet_coverage(workers + [storm_soak])
            self._run_random_cycles(workers)
            self._run_overlap_scenarios(workers)
            self._run_request_storm(workers + [storm_soak])
            self._run_destroy_races(
                start_ordinal=self.args.sandboxes + 1,
                create_interval=create_interval,
            )
            self._sleep_create_interval(create_interval)
            replica_freshness = self._create_sandbox(
                ordinal=self.args.sandboxes + self.args.destroy_race_sandboxes + 1,
                role="replica-freshness",
                timeout_seconds=self.args.sandbox_timeout_seconds,
                auto_resume=False,
            )
            self._prime_sandbox(replica_freshness)
            self._run_replica_freshness(replica_freshness)
            self._sleep_create_interval(create_interval)
            timeout_soak = self._create_sandbox(
                ordinal=self.args.sandboxes + self.args.destroy_race_sandboxes + 2,
                role="timeout-soak",
                timeout_seconds=self.args.soak_timeout_seconds,
            )
            self._prime_sandbox(timeout_soak)
            self._run_timeout_soak(timeout_soak)
        except BaseException as error:
            self._record_failure(error)
        finally:
            self._cleanup_owned_sandboxes()

        report = self._report()
        if self.args.report:
            report_path = Path(self.args.report).expanduser().resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            self.recorder.note("report_written", path=str(report_path))

        if report["status"] != "passed":
            raise ShakedownFailure(
                f"shakedown {self.run_id} failed: " + "; ".join(report["failures"])
            )
        self.recorder.note(
            "shakedown_passed",
            run_id=self.run_id,
            requests=report["request_count"],
            sandboxes=report["sandboxes_created"],
            elapsed_seconds=report["elapsed_seconds"],
        )
        return report

    def _limits(self) -> Dict[str, int]:
        _, payload = self.http.control(
            "sandbox_limits",
            "GET",
            "/sandbox_limits",
            params=[("team", self.args.team)],
            allowed={200},
        )
        root = payload.get("sandbox_limits") or payload
        plan = root.get("plan") or root.get("effective_plan") or {}
        quota = root.get("quota") or {}
        usage = quota.get("usage") or {}
        result = {
            "create_rate_per_second": int(plan.get("create_rate_per_second") or 1),
            "max_running_instances": int(plan.get("max_running_instances") or 1),
            "running_instances": int(usage.get("running_instances") or 0),
        }
        self.recorder.note("sandbox_limits_resolved", **result)
        return result

    def _sleep_create_interval(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def _create_sandbox(
        self, *, ordinal: int, role: str, timeout_seconds: int, auto_resume: bool = True
    ) -> SandboxRecord:
        idempotency_key = f"sandbox-shakedown:{self.run_id}:{role}:{ordinal}"
        body = {
            "team": self.args.team,
            "template": self.args.template,
            "timeout": timeout_seconds,
            "metadata": {
                "watasu_shakedown": self.run_id,
                "watasu_shakedown_role": role,
                "watasu_shakedown_ordinal": str(ordinal),
            },
            "envs": {},
            "secure": True,
            "lifecycle": {"on_timeout": "pause", "auto_resume": auto_resume},
        }
        _status, payload = self.http.control(
            "sandbox_create",
            "POST",
            "/sandboxes",
            body=body,
            headers={"Idempotency-Key": idempotency_key},
            allowed={200, 201},
            timeout_seconds=self.args.request_timeout_seconds,
        )

        sandbox = _require_dict(payload.get("sandbox"), "create sandbox")
        sandbox_id = str(_required(sandbox, "id", "create sandbox"))
        if sandbox.get("state") != "ready":
            raise ShakedownFailure(
                f"create returned sandbox {sandbox_id} in {sandbox.get('state')!r}, not ready"
            )
        session = _session_info(payload, f"create sandbox {sandbox_id}")
        node = str(sandbox.get("node") or "[unknown]")
        marker_slug = f"{self.run_id}-{ordinal}"
        record = SandboxRecord(
            sandbox_id=sandbox_id,
            ordinal=ordinal,
            role=role,
            node=node,
            initial_node=node,
            timeout_seconds=timeout_seconds,
            deadline_at=_parse_api_datetime(
                sandbox.get("deadline_at") or sandbox.get("expires_at"),
                f"create sandbox {sandbox_id} deadline",
            ),
            marker_path=f"/tmp/watasu-shakedown-marker-{marker_slug}",
            marker_value=f"{marker_slug}:{uuid.uuid4().hex}",
            heartbeat_path=f"/tmp/watasu-shakedown-heartbeat-{marker_slug}",
            session=session,
            sandbox_payload=sandbox,
        )
        with self._created_lock:
            self._created[sandbox_id] = record
        return record

    def _prime_sandbox(self, record: SandboxRecord) -> None:
        self._write_file(record, record.marker_path, record.marker_value)
        self._assert_marker(record)
        sandbox = self._sdk_sandbox(record)
        quoted_path = shlex.quote(record.heartbeat_path)
        command = (
            f"i=0; while :; do i=$((i + 1)); "
            f"printf '%s\\n' \"$i\" >> {quoted_path}; sleep 0.2; done"
        )
        handle = sandbox.commands.run(
            command,
            background=True,
            timeout=self.args.process_timeout_seconds,
            request_timeout=self.args.data_plane_timeout_seconds,
        )
        record.heartbeat_pid = str(handle.pid)
        handle.disconnect()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if self._heartbeat_count(record) >= 2:
                    break
            except Exception:
                pass
            time.sleep(0.2)
        else:
            raise ShakedownFailure(
                f"heartbeat process {record.heartbeat_pid} did not write in sandbox "
                f"{record.sandbox_id}"
            )
        self._assert_process_running(record)
        self.recorder.note(
            "sandbox_primed",
            sandbox_id=record.sandbox_id,
            node=record.node,
            pid=record.heartbeat_pid,
            role=record.role,
        )

    def _sdk_sandbox(self, record: SandboxRecord) -> Sandbox:
        return Sandbox(
            record.sandbox_id,
            connection_config=self.sdk_config,
            session={
                "data_plane_url": record.session.data_plane_url,
                "token": record.session.token,
                "access_token": record.session.token,
                "runtime_generation": record.session.runtime_generation,
            },
            sandbox=record.sandbox_payload,
        )

    def _write_file(self, record: SandboxRecord, path: str, value: str) -> None:
        self.http.data_plane(
            "file_write",
            "PUT",
            record.session,
            "/runtime/v1/files",
            sandbox_id=record.sandbox_id,
            body=value.encode(),
            params={"path": path},
            allowed={200, 201},
            timeout_seconds=self.args.data_plane_timeout_seconds,
        )

    def _read_file(
        self,
        record: SandboxRecord,
        path: str,
        *,
        session: Optional[SessionInfo] = None,
        operation: str = "file_read",
        timeout_seconds: Optional[float] = None,
        echo: bool = True,
    ) -> str:
        _, content = self.http.data_plane(
            operation,
            "GET",
            session or record.session,
            "/runtime/v1/files",
            sandbox_id=record.sandbox_id,
            params={"path": path},
            allowed={200},
            timeout_seconds=timeout_seconds or self.args.data_plane_timeout_seconds,
            echo=echo,
        )
        return content.decode("utf-8")

    def _run_request_storm(self, records: List[SandboxRecord]) -> None:
        request_count = self.args.request_storm_count
        if request_count == 0:
            return

        worker_count = self.args.request_storm_workers
        self.recorder.note(
            "request_storm_start",
            requests=request_count,
            workers=worker_count,
            sandboxes=len(records),
        )
        started = time.monotonic()
        completion_lock = threading.Lock()
        completed = 0
        successful = 0
        next_progress = 1000
        failures: List[Dict[str, Any]] = []
        start_barrier = threading.Barrier(worker_count)
        threads_used: Set[str] = set()

        def perform(sequence: int) -> None:
            nonlocal completed, successful, next_progress
            record = records[sequence % len(records)]
            operation_index = (sequence // len(records)) % 3
            try:
                if operation_index == 0:
                    _, payload = self.http.control(
                        "request_storm_show",
                        "GET",
                        f"/sandboxes/{record.sandbox_id}",
                        sandbox_id=record.sandbox_id,
                        allowed={200},
                        timeout_seconds=self.args.request_timeout_seconds,
                        echo=False,
                    )
                    sandbox = _require_dict(payload.get("sandbox"), "request storm show")
                    response_id = str(_required(sandbox, "id", "request storm show"))
                    if response_id != record.sandbox_id:
                        raise ShakedownFailure(
                            f"request storm changed identity from {record.sandbox_id} "
                            f"to {response_id}"
                        )
                    state = sandbox.get("state")
                    expected_states = (
                        {"ready", "stopped"} if record.role == "storm-soak" else {"ready"}
                    )
                    if state not in expected_states:
                        raise ShakedownFailure(
                            f"request storm found sandbox {record.sandbox_id} in "
                            f"{state!r}, expected one of {sorted(expected_states)!r}"
                        )
                elif operation_index == 1:
                    actual = self._read_file(
                        record,
                        record.marker_path,
                        operation="request_storm_marker",
                        echo=False,
                    )
                    if actual != record.marker_value:
                        raise ShakedownFailure(
                            f"request storm marker changed for sandbox {record.sandbox_id}"
                        )
                else:
                    content = self._read_file(
                        record,
                        record.heartbeat_path,
                        operation="request_storm_heartbeat",
                        echo=False,
                    )
                    lines = [line for line in content.splitlines() if line.strip()]
                    if not lines or not lines[-1].isdigit():
                        raise ShakedownFailure(
                            f"request storm heartbeat was invalid for sandbox "
                            f"{record.sandbox_id}"
                        )
            except BaseException as error:
                with completion_lock:
                    if len(failures) < 100:
                        failures.append(
                            {
                                "sequence": sequence,
                                "sandbox_id": record.sandbox_id,
                                "error": f"{type(error).__name__}: {error}",
                            }
                        )
            else:
                with completion_lock:
                    successful += 1
            finally:
                progress = None
                with completion_lock:
                    completed += 1
                    if completed >= next_progress:
                        progress = min(completed, request_count)
                        next_progress += 1000
                if progress is not None:
                    self.recorder.note(
                        "request_storm_progress",
                        completed=progress,
                        total=request_count,
                    )

        def worker(worker_index: int) -> None:
            with completion_lock:
                threads_used.add(threading.current_thread().name)
            start_barrier.wait(timeout=30)
            for sequence in range(worker_index, request_count, worker_count):
                perform(sequence)

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=worker_count, thread_name_prefix="sandbox-request-storm"
        ) as executor:
            futures = [executor.submit(worker, index) for index in range(worker_count)]
            for future in futures:
                future.result()

        storm_events, _ = self.recorder.snapshot()
        storm_events = [
            event
            for event in storm_events
            if event.operation.startswith("request_storm_")
        ]
        statuses = collections.Counter(str(event.status) for event in storm_events)
        latencies: Dict[str, List[int]] = collections.defaultdict(list)
        for event in storm_events:
            latencies[event.operation].append(event.elapsed_ms)
        latency_summary = {
            operation: {
                "count": len(values),
                "min_ms": min(values),
                "p50_ms": round(statistics.median(values)),
                "p95_ms": _percentile(values, 0.95),
                "p99_ms": _percentile(values, 0.99),
                "max_ms": max(values),
            }
            for operation, values in sorted(latencies.items())
            if values
        }
        self._request_storm_result = {
            "requested": request_count,
            "completed": completed,
            "successful": successful,
            "failed": completed - successful,
            "workers": worker_count,
            "threads_used": len(threads_used),
            "sandboxes": len(records),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "http_status_counts": dict(sorted(statuses.items())),
            "latencies": latency_summary,
            "failures": failures,
        }
        self.recorder.note(
            "request_storm_complete",
            requests=request_count,
            completed=completed,
            successful=successful,
            failed=completed - successful,
            workers=worker_count,
            threads_used=len(threads_used),
            elapsed_seconds=self._request_storm_result["elapsed_seconds"],
        )
        if (
            completed != request_count
            or successful != request_count
            or len(threads_used) != worker_count
            or failures
        ):
            raise ShakedownFailure(
                f"request storm completed {completed}/{request_count} requests with "
                f"{completed - successful} failures across {len(threads_used)}/"
                f"{worker_count} threads"
            )

    def _assert_marker(self, record: SandboxRecord) -> None:
        actual = self._read_file(record, record.marker_path)
        if actual != record.marker_value:
            raise ShakedownFailure(
                f"sandbox {record.sandbox_id} marker changed: "
                f"expected {record.marker_value!r}, got {actual!r}"
            )

    def _heartbeat_count(self, record: SandboxRecord) -> int:
        content = self._read_file(record, record.heartbeat_path, operation="heartbeat_read")
        return len([line for line in content.splitlines() if line.strip()])

    def _assert_process_running(self, record: SandboxRecord) -> None:
        if not record.heartbeat_pid:
            raise ShakedownFailure(f"sandbox {record.sandbox_id} has no heartbeat pid")
        status = self._sdk_sandbox(record).commands.process(
            record.heartbeat_pid,
            request_timeout=self.args.data_plane_timeout_seconds,
        )
        if status.status != "running":
            raise ShakedownFailure(
                f"sandbox {record.sandbox_id} process {record.heartbeat_pid} is "
                f"{status.status!r}, not running"
            )

    def _assert_runtime_survived(
        self, record: SandboxRecord, heartbeat_before: int
    ) -> None:
        self._assert_marker(record)
        self._assert_process_running(record)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            heartbeat_after = self._heartbeat_count(record)
            if heartbeat_after > heartbeat_before:
                self.recorder.note(
                    "runtime_survived",
                    sandbox_id=record.sandbox_id,
                    pid=record.heartbeat_pid,
                    heartbeat_before=heartbeat_before,
                    heartbeat_after=heartbeat_after,
                    node=record.node,
                )
                return
            time.sleep(0.25)
        raise ShakedownFailure(
            f"sandbox {record.sandbox_id} process {record.heartbeat_pid} did not "
            "advance after resume"
        )

    def _pause(self, record: SandboxRecord, endpoint: str) -> None:
        _, payload = self.http.control(
            f"sandbox_{endpoint}",
            "POST",
            f"/sandboxes/{record.sandbox_id}/{endpoint}",
            sandbox_id=record.sandbox_id,
            body={},
            allowed={202},
            timeout_seconds=self.args.request_timeout_seconds,
        )
        self._assert_response_identity(record, payload, f"{endpoint} response")
        self._wait_for_state(record, {"stopped"}, self.args.state_timeout_seconds)

    def _connect(self, record: SandboxRecord, endpoint: str) -> None:
        _, payload = self.http.control(
            f"sandbox_{endpoint}",
            "POST",
            f"/sandboxes/{record.sandbox_id}/{endpoint}",
            sandbox_id=record.sandbox_id,
            body={"connect": {"timeout_seconds": record.timeout_seconds}},
            allowed={200},
            timeout_seconds=self.args.request_timeout_seconds,
        )
        self._assert_response_identity(record, payload, f"{endpoint} response")
        sandbox = _require_dict(payload.get("sandbox"), f"{endpoint} sandbox")
        if sandbox.get("state") != "ready":
            raise ShakedownFailure(
                f"{endpoint} returned sandbox {record.sandbox_id} in "
                f"{sandbox.get('state')!r}, not ready"
            )
        record.session = _session_info(payload, f"{endpoint} sandbox {record.sandbox_id}")
        self._update_record_node(record, sandbox)
        record.sandbox_payload = sandbox

    def _wait_for_state(
        self, record: SandboxRecord, target: Set[str], timeout_seconds: float
    ) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        last_state = None
        while time.monotonic() < deadline:
            _, payload = self.http.control(
                "sandbox_show",
                "GET",
                f"/sandboxes/{record.sandbox_id}",
                sandbox_id=record.sandbox_id,
                allowed={200},
                timeout_seconds=min(30, timeout_seconds),
            )
            sandbox = _require_dict(payload.get("sandbox"), "sandbox show")
            self._assert_response_identity(record, payload, "sandbox show")
            state = str(sandbox.get("state") or "")
            last_state = state
            self._update_record_node(record, sandbox)
            record.sandbox_payload = sandbox
            if state in target:
                return sandbox
            if state in TERMINAL_FAILURE_STATES:
                raise ShakedownFailure(
                    f"sandbox {record.sandbox_id} entered terminal state {state!r} "
                    f"while waiting for {sorted(target)}"
                )
            time.sleep(self.args.state_poll_seconds)
        raise ShakedownFailure(
            f"sandbox {record.sandbox_id} did not reach {sorted(target)} within "
            f"{timeout_seconds}s; last state={last_state!r}"
        )

    def _assert_response_identity(
        self, record: SandboxRecord, payload: Dict[str, Any], context: str
    ) -> None:
        sandbox = payload.get("sandbox")
        if not isinstance(sandbox, dict) or sandbox.get("id") is None:
            return
        response_id = str(sandbox["id"])
        if response_id != record.sandbox_id:
            raise ShakedownFailure(
                f"{context} changed identity from {record.sandbox_id} to {response_id}"
            )

    def _update_record_node(
        self, record: SandboxRecord, sandbox: Dict[str, Any]
    ) -> None:
        node = str(sandbox.get("node") or record.node or "[unknown]")
        record.node = node
        if not record.node_history or record.node_history[-1] != node:
            record.node_history.append(node)

    def _require_fleet_coverage(self, records: Iterable[SandboxRecord]) -> None:
        nodes = collections.Counter(record.node for record in records)
        self.recorder.note("fleet_placement", nodes=dict(nodes))
        if self.args.require_nodes:
            missing = sorted(set(self.args.require_nodes) - set(nodes))
            if missing:
                raise ShakedownFailure(
                    f"created sandboxes did not cover required nodes {missing}; "
                    f"observed {dict(nodes)}"
                )
        elif len(nodes) < self.args.minimum_nodes:
            raise ShakedownFailure(
                f"created sandboxes covered {len(nodes)} node(s), fewer than required "
                f"minimum {self.args.minimum_nodes}: {dict(nodes)}"
            )

    def _run_random_cycles(self, records: List[SandboxRecord]) -> None:
        self.recorder.note(
            "random_cycles_start", workers=len(records), cycles=self.args.cycles
        )
        failures: List[str] = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(records), thread_name_prefix="sandbox-cycle"
        ) as executor:
            futures = {
                executor.submit(self._random_worker, record): record
                for record in records
            }
            for future, record in futures.items():
                try:
                    future.result()
                except BaseException as error:
                    failures.append(f"sandbox {record.sandbox_id}: {error}")
        if failures:
            raise ShakedownFailure("random lifecycle cycles failed: " + " | ".join(failures))
        self.recorder.note("random_cycles_complete", workers=len(records))

    def _random_worker(self, record: SandboxRecord) -> None:
        rng = random.Random(self.seed ^ (record.ordinal * 0x9E3779B97F4A7C15))
        for cycle in range(self.args.cycles):
            time.sleep(rng.uniform(0, self.args.maximum_jitter_seconds))
            heartbeat_before = self._heartbeat_count(record)
            previous_session = record.session
            stop_endpoint = rng.choice(["pause", "stop"])
            self._pause(record, stop_endpoint)
            time.sleep(rng.uniform(0, self.args.maximum_jitter_seconds))

            if rng.random() < self.args.auto_resume_fraction:
                marker = self._read_file(
                    record,
                    record.marker_path,
                    session=previous_session,
                    operation="data_plane_auto_resume",
                    timeout_seconds=self.args.request_timeout_seconds,
                )
                if marker != record.marker_value:
                    raise ShakedownFailure(
                        f"auto-resume changed marker for sandbox {record.sandbox_id}"
                    )
                self._wait_for_state(
                    record, {"ready"}, self.args.state_timeout_seconds
                )
                self._connect(record, rng.choice(["connect", "resume"]))
            else:
                self._connect(record, rng.choice(["connect", "resume"]))

            self._assert_runtime_survived(record, heartbeat_before)
            if rng.random() < self.args.ready_refresh_fraction:
                self._connect(record, rng.choice(["connect", "resume"]))
                self._assert_marker(record)
            record.cycles_completed += 1
            self.recorder.note(
                "cycle_complete",
                sandbox_id=record.sandbox_id,
                cycle=cycle + 1,
                stop=stop_endpoint,
                node=record.node,
            )

    def _run_overlap_scenarios(self, records: List[SandboxRecord]) -> None:
        if not records:
            return
        self.recorder.note("overlap_scenarios_start")
        self._stop_storm(records[0])
        if len(records) > 1:
            self._stop_connect_overlap(records[1])
        self.recorder.note("overlap_scenarios_complete")

    def _stop_storm(self, record: SandboxRecord) -> None:
        heartbeat_before = self._heartbeat_count(record)
        calls = self.args.storm_concurrency
        barrier = threading.Barrier(calls)

        def stop_call(index: int) -> None:
            barrier.wait()
            endpoint = "pause" if index % 2 == 0 else "stop"
            _, payload = self.http.control(
                f"storm_{endpoint}",
                "POST",
                f"/sandboxes/{record.sandbox_id}/{endpoint}",
                sandbox_id=record.sandbox_id,
                body={},
                allowed={202},
                timeout_seconds=self.args.request_timeout_seconds,
            )
            self._assert_response_identity(record, payload, f"storm {endpoint}")

        self._parallel_calls(stop_call, range(calls), "stop storm")
        self._wait_for_state(record, {"stopped"}, self.args.state_timeout_seconds)

        resume_barrier = threading.Barrier(calls)

        def resume_call(index: int) -> Dict[str, Any]:
            resume_barrier.wait()
            endpoint = "connect" if index % 2 == 0 else "resume"
            _, payload = self.http.control(
                f"storm_{endpoint}",
                "POST",
                f"/sandboxes/{record.sandbox_id}/{endpoint}",
                sandbox_id=record.sandbox_id,
                body={"connect": {"timeout_seconds": record.timeout_seconds}},
                allowed={200},
                timeout_seconds=self.args.request_timeout_seconds,
            )
            self._assert_response_identity(record, payload, f"storm {endpoint}")
            if (payload.get("sandbox") or {}).get("state") != "ready":
                raise ShakedownFailure(f"storm {endpoint} returned without ready sandbox")
            _session_info(payload, f"storm {endpoint}")
            return payload

        payloads = self._parallel_calls(resume_call, range(calls), "resume storm")
        chosen = payloads[-1]
        record.session = _session_info(chosen, "resume storm result")
        record.sandbox_payload = _require_dict(chosen.get("sandbox"), "resume storm")
        self._update_record_node(record, record.sandbox_payload)
        self._assert_runtime_survived(record, heartbeat_before)

    def _stop_connect_overlap(self, record: SandboxRecord) -> None:
        heartbeat_before = self._heartbeat_count(record)

        def request_stop() -> None:
            _, payload = self.http.control(
                "overlap_stop",
                "POST",
                f"/sandboxes/{record.sandbox_id}/stop",
                sandbox_id=record.sandbox_id,
                body={},
                allowed={202},
                timeout_seconds=self.args.request_timeout_seconds,
            )
            self._assert_response_identity(record, payload, "overlap stop")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            stop_future = executor.submit(request_stop)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not stop_future.done():
                _, payload = self.http.control(
                    "overlap_state_probe",
                    "GET",
                    f"/sandboxes/{record.sandbox_id}",
                    sandbox_id=record.sandbox_id,
                    allowed={200},
                    timeout_seconds=30,
                )
                state = (payload.get("sandbox") or {}).get("state")
                if state in {"stopping", "stopped"}:
                    break
                time.sleep(0.02)
            self._connect(record, "connect")
            stop_future.result()
        self._wait_for_state(record, {"ready"}, self.args.state_timeout_seconds)
        self._connect(record, "resume")
        self._assert_runtime_survived(record, heartbeat_before)

    def _run_replica_freshness(self, record: SandboxRecord) -> None:
        heartbeat_before = self._heartbeat_count(record)
        self._pause(record, "pause")
        self._connect(record, "connect")

        calls = self.args.storm_concurrency
        barrier = threading.Barrier(calls)

        def run_shell(index: int) -> None:
            expected = f"replica-freshness-{index}"
            barrier.wait()
            result = self._sdk_sandbox(record).commands.run(
                f"printf '{expected}'",
                timeout=30,
                request_timeout=self.args.data_plane_timeout_seconds,
            )
            if result.stdout != expected:
                raise ShakedownFailure(
                    f"replica freshness shell {index} returned {result.stdout!r}, "
                    f"expected {expected!r}"
                )

        self._parallel_calls(run_shell, range(calls), "replica freshness shell storm")
        self._assert_runtime_survived(record, heartbeat_before)
        self.recorder.note(
            "replica_freshness_complete",
            sandbox_id=record.sandbox_id,
            requests=calls,
            node=record.node,
        )

    def _parallel_calls(
        self, function: Any, items: Iterable[Any], context: str
    ) -> List[Any]:
        item_list = list(items)
        failures: List[str] = []
        results: List[Any] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(item_list)) as executor:
            futures = [executor.submit(function, item) for item in item_list]
            for future in futures:
                try:
                    results.append(future.result())
                except BaseException as error:
                    failures.append(str(error))
        if failures:
            raise ShakedownFailure(f"{context} failed: " + " | ".join(failures))
        return results

    def _run_destroy_races(self, *, start_ordinal: int, create_interval: float) -> None:
        self.recorder.note(
            "destroy_races_start", count=self.args.destroy_race_sandboxes
        )
        for offset in range(self.args.destroy_race_sandboxes):
            self._sleep_create_interval(create_interval)
            record = self._create_sandbox(
                ordinal=start_ordinal + offset,
                role="destroy-race",
                timeout_seconds=self.args.sandbox_timeout_seconds,
            )
            self._prime_sandbox(record)
            barrier = threading.Barrier(self.args.storm_concurrency)

            def destructive_call(_index: int) -> None:
                barrier.wait()
                self.http.control(
                    "destroy_race_delete",
                    "DELETE",
                    f"/sandboxes/{record.sandbox_id}",
                    sandbox_id=record.sandbox_id,
                    allowed={202},
                    timeout_seconds=self.args.request_timeout_seconds,
                )

            self._parallel_calls(
                destructive_call,
                range(self.args.storm_concurrency),
                "duplicate destroy race",
            )
            self._wait_until_deleted(record.sandbox_id, self.args.state_timeout_seconds)
        self.recorder.note("destroy_races_complete")

    def _run_timeout_soak(self, record: SandboxRecord) -> None:
        self.recorder.note(
            "timeout_soak_wait",
            sandbox_id=record.sandbox_id,
            timeout_seconds=record.timeout_seconds,
        )
        old_session = record.session
        deadline = record.deadline_at + dt.timedelta(seconds=self.args.soak_grace_seconds)
        last_progress = 0.0
        while True:
            _, payload = self.http.control(
                "soak_state_probe",
                "GET",
                f"/sandboxes/{record.sandbox_id}",
                sandbox_id=record.sandbox_id,
                allowed={200},
                timeout_seconds=30,
            )
            sandbox = _require_dict(payload.get("sandbox"), "soak sandbox show")
            state = str(sandbox.get("state") or "")
            record.sandbox_payload = sandbox
            self._update_record_node(record, sandbox)
            if state == "stopped":
                break
            if state in TERMINAL_FAILURE_STATES:
                raise ShakedownFailure(
                    f"soak sandbox {record.sandbox_id} entered {state!r} instead of stopped"
                )
            remaining = (deadline - dt.datetime.now(dt.timezone.utc)).total_seconds()
            if remaining <= 0:
                raise ShakedownFailure(
                    f"soak sandbox {record.sandbox_id} did not auto-pause by "
                    f"{deadline.isoformat()}; last state={state!r}"
                )
            elapsed = time.monotonic() - self.started_at
            if elapsed - last_progress >= 60:
                self.recorder.note(
                    "timeout_soak_progress",
                    sandbox_id=record.sandbox_id,
                    elapsed_seconds=round(elapsed, 1),
                    state=state,
                )
                last_progress = elapsed
            time.sleep(min(self.args.soak_poll_seconds, remaining))

        marker = self._read_file(
            record,
            record.marker_path,
            session=old_session,
            operation="soak_data_plane_auto_resume",
            timeout_seconds=self.args.request_timeout_seconds,
        )
        if marker != record.marker_value:
            raise ShakedownFailure(
                f"soak sandbox {record.sandbox_id} marker changed after timeout auto-resume"
            )
        self._wait_for_state(record, {"ready"}, self.args.state_timeout_seconds)
        self._connect(record, "connect")
        self._assert_process_running(record)
        self._assert_marker(record)
        self.recorder.note(
            "timeout_soak_complete",
            sandbox_id=record.sandbox_id,
            elapsed_seconds=round(time.monotonic() - self.started_at, 1),
            node=record.node,
            pid=record.heartbeat_pid,
        )

    def _wait_until_deleted(self, sandbox_id: str, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        last_state = None
        while time.monotonic() < deadline:
            _, payload = self.http.control(
                "sandbox_delete_probe",
                "GET",
                f"/sandboxes/{sandbox_id}",
                sandbox_id=sandbox_id,
                allowed={200},
                timeout_seconds=30,
            )
            if not payload or payload.get("error") or payload.get("errors"):
                return
            sandbox = payload.get("sandbox") or {}
            last_state = sandbox.get("state")
            if last_state in {"destroyed", "expired"}:
                return
            time.sleep(self.args.state_poll_seconds)
        raise ShakedownFailure(
            f"sandbox {sandbox_id} was not deleted within {timeout_seconds}s; "
            f"last state={last_state!r}"
        )

    def _cleanup_owned_sandboxes(self) -> None:
        if self.args.keep_on_failure and self._failures:
            self.recorder.note("cleanup_skipped", reason="--keep-on-failure")
            return
        ids = set(self._created)
        listed_states: Dict[str, str] = {}
        try:
            listed_states = self._list_owned_sandbox_states()
            ids.update(listed_states)
        except BaseException as error:
            self._cleanup_failures.append(f"could not list owned sandboxes: {error}")
        delete_ids = {
            sandbox_id
            for sandbox_id in ids
            if listed_states.get(sandbox_id) not in TERMINAL_CLEANUP_STATES
        }
        self.recorder.note("cleanup_start", sandbox_count=len(delete_ids))

        def delete_one(sandbox_id: str) -> None:
            try:
                self.http.control(
                    "cleanup_delete",
                    "DELETE",
                    f"/sandboxes/{sandbox_id}",
                    sandbox_id=sandbox_id,
                    allowed={202},
                    timeout_seconds=self.args.request_timeout_seconds,
                )
            except BaseException as error:
                self._cleanup_failures.append(f"delete {sandbox_id}: {error}")

        if delete_ids:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(8, len(delete_ids))
            ) as executor:
                list(executor.map(delete_one, sorted(delete_ids)))

        deadline = time.monotonic() + self.args.cleanup_timeout_seconds
        remaining = delete_ids
        while remaining and time.monotonic() < deadline:
            time.sleep(self.args.state_poll_seconds)
            try:
                listed_states = self._list_owned_sandbox_states()
            except BaseException as error:
                self._cleanup_failures.append(f"cleanup verification list: {error}")
                break
            remaining = {
                sandbox_id
                for sandbox_id in remaining
                if sandbox_id in listed_states
                and listed_states[sandbox_id] not in TERMINAL_CLEANUP_STATES
            }
        if remaining:
            self._cleanup_failures.append(
                f"owned sandboxes still listed after cleanup: {sorted(remaining)}"
            )
        self.recorder.note(
            "cleanup_complete",
            remaining=len(remaining),
            failures=len(self._cleanup_failures),
        )

    def _list_owned_sandbox_states(self) -> Dict[str, str]:
        states: Dict[str, str] = {}
        next_token: Optional[str] = None
        while True:
            params: List[Tuple[str, str]] = [
                ("team", self.args.team),
                ("limit", "100"),
                ("query[metadata][watasu_shakedown]", self.run_id),
            ]
            if next_token:
                params.append(("next_token", next_token))
            _, payload = self.http.control(
                "owned_sandbox_list",
                "GET",
                "/sandboxes",
                params=params,
                allowed={200},
                timeout_seconds=30,
            )
            for sandbox in payload.get("sandboxes") or []:
                if isinstance(sandbox, dict) and sandbox.get("id") is not None:
                    states[str(sandbox["id"])] = str(sandbox.get("state") or "")
            token = payload.get("next_token")
            if token in [None, ""]:
                break
            next_token = str(token)
        return states

    def _record_failure(self, error: BaseException) -> None:
        message = f"{type(error).__name__}: {error}"
        with self._failure_lock:
            self._failures.append(message)
        self.recorder.note("shakedown_failure", error=message)

    def _report(self) -> Dict[str, Any]:
        events, notes = self.recorder.snapshot()
        latencies: Dict[str, List[int]] = collections.defaultdict(list)
        statuses: Dict[str, int] = collections.Counter()
        for event in events:
            latencies[event.operation].append(event.elapsed_ms)
            statuses[str(event.status)] += 1

        latency_summary = {
            operation: {
                "count": len(values),
                "min_ms": min(values),
                "p50_ms": round(statistics.median(values)),
                "p95_ms": _percentile(values, 0.95),
                "max_ms": max(values),
            }
            for operation, values in sorted(latencies.items())
            if values
        }
        records = list(self._created.values())
        nodes = collections.Counter(
            node for record in records for node in set(record.node_history)
        )
        failures = list(self._failures)
        failures.extend(self._cleanup_failures)
        return {
            "status": "passed" if not failures else "failed",
            "run_id": self.run_id,
            "seed": self.seed,
            "api_url": self.args.api_url,
            "team": self.args.team,
            "template": self.args.template,
            "started_at": self.started_wall.isoformat(),
            "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "elapsed_seconds": round(time.monotonic() - self.started_at, 3),
            "sandboxes_created": len(records),
            "cycles_completed": sum(record.cycles_completed for record in records),
            "nodes_exercised": dict(sorted(nodes.items())),
            "request_count": len(events),
            "http_status_counts": dict(sorted(statuses.items())),
            "latencies": latency_summary,
            "request_storm": self._request_storm_result,
            "sandbox_results": [
                {
                    "sandbox_id": record.sandbox_id,
                    "role": record.role,
                    "initial_node": record.initial_node,
                    "node_history": record.node_history,
                    "heartbeat_pid": record.heartbeat_pid,
                    "cycles_completed": record.cycles_completed,
                }
                for record in sorted(records, key=lambda item: item.ordinal)
            ],
            "failures": failures,
            "events": [dataclasses.asdict(event) for event in events],
            "notes": notes,
        }


def _required(mapping: Dict[str, Any], key: str, context: str) -> Any:
    value = mapping.get(key)
    if value in [None, ""]:
        raise ShakedownFailure(f"{context} did not include {key}")
    return value


def _require_dict(value: Any, context: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ShakedownFailure(f"{context} was not an object")
    return value


def _parse_api_datetime(value: Any, context: str) -> dt.datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ShakedownFailure(f"{context} did not include a timestamp")
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ShakedownFailure(f"{context} included invalid timestamp {raw!r}") from error
    if parsed.tzinfo is None:
        raise ShakedownFailure(f"{context} timestamp did not include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def _session_info(payload: Dict[str, Any], context: str) -> SessionInfo:
    session = _require_dict(payload.get("session"), f"{context} session")
    return SessionInfo(
        data_plane_url=str(_required(session, "data_plane_url", context)),
        token=str(session.get("access_token") or _required(session, "token", context)),
        runtime_generation=session.get("runtime_generation"),
    )


def _response_json(response: requests.Response) -> Dict[str, Any]:
    if not response.content:
        return {}
    try:
        value = response.json()
    except ValueError:
        return {"body": response.text[:2000]}
    return value if isinstance(value, dict) else {"body": value}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("[redacted]" if key.lower() in REDACTED_KEYS else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _safe_json(value: Any) -> str:
    return json.dumps(_redact(value), sort_keys=True, default=str)[:4000]


def _percentile(values: Sequence[int], fraction: float) -> int:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return ordered[rank]


def _load_token(environment_name: str, token_command: str) -> str:
    token = os.environ.get(environment_name, "").strip()
    if token:
        return token
    command = shlex.split(token_command)
    if not command:
        raise ShakedownFailure("token command is empty")
    completed = subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    token = completed.stdout.strip()
    if not token:
        raise ShakedownFailure(f"token command {command[0]!r} returned an empty token")
    return token


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="https://api.watasu.io/v1")
    parser.add_argument("--team", default="watasu")
    parser.add_argument("--template", default="base")
    parser.add_argument("--sandboxes", type=int, default=8)
    parser.add_argument("--cycles", type=int, default=8)
    parser.add_argument("--destroy-race-sandboxes", type=int, default=2)
    parser.add_argument("--storm-concurrency", type=int, default=6)
    parser.add_argument("--request-storm-count", type=int, default=0)
    parser.add_argument("--request-storm-workers", type=int, default=32)
    parser.add_argument("--storm-jitter-seconds", type=float, default=0.25)
    parser.add_argument("--maximum-jitter-seconds", type=float, default=2.0)
    parser.add_argument("--auto-resume-fraction", type=float, default=0.30)
    parser.add_argument("--ready-refresh-fraction", type=float, default=0.25)
    parser.add_argument("--sandbox-timeout-seconds", type=int, default=3600)
    parser.add_argument("--soak-timeout-seconds", type=int, default=620)
    parser.add_argument("--soak-grace-seconds", type=int, default=180)
    parser.add_argument("--soak-poll-seconds", type=float, default=5.0)
    parser.add_argument("--process-timeout-seconds", type=float, default=7200)
    parser.add_argument("--request-timeout-seconds", type=float, default=1800)
    parser.add_argument("--data-plane-timeout-seconds", type=float, default=60)
    parser.add_argument("--state-timeout-seconds", type=float, default=300)
    parser.add_argument("--state-poll-seconds", type=float, default=0.25)
    parser.add_argument("--cleanup-timeout-seconds", type=float, default=300)
    parser.add_argument("--create-interval-seconds", type=float, default=0.0)
    parser.add_argument("--minimum-nodes", type=int, default=2)
    parser.add_argument("--require-node", dest="require_nodes", action="append", default=[])
    parser.add_argument("--run-id")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--report")
    parser.add_argument("--token-environment", default="WATASU_API_KEY")
    parser.add_argument("--token-command", default="watasu token")
    parser.add_argument("--keep-on-failure", action="store_true")
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    positive_ints = {
        "sandboxes": args.sandboxes,
        "cycles": args.cycles,
        "storm_concurrency": args.storm_concurrency,
        "request_storm_workers": args.request_storm_workers,
        "sandbox_timeout_seconds": args.sandbox_timeout_seconds,
        "soak_timeout_seconds": args.soak_timeout_seconds,
        "minimum_nodes": args.minimum_nodes,
    }
    for name, value in positive_ints.items():
        if value <= 0:
            raise ShakedownFailure(f"--{name.replace('_', '-')} must be positive")
    if args.destroy_race_sandboxes < 0:
        raise ShakedownFailure("--destroy-race-sandboxes cannot be negative")
    if args.request_storm_count < 0:
        raise ShakedownFailure("--request-storm-count cannot be negative")
    if args.request_storm_count and args.request_storm_workers > args.request_storm_count:
        raise ShakedownFailure(
            "--request-storm-workers cannot exceed --request-storm-count"
        )
    for name in ["auto_resume_fraction", "ready_refresh_fraction"]:
        value = getattr(args, name)
        if not 0 <= value <= 1:
            raise ShakedownFailure(f"--{name.replace('_', '-')} must be between 0 and 1")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _validate_args(args)
        token = _load_token(args.token_environment, args.token_command)
        report = LifecycleShakedown(args, token).run()
    except KeyboardInterrupt:
        print("shakedown interrupted", file=sys.stderr)
        return 130
    except BaseException as error:
        print(f"shakedown failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                key: report[key]
                for key in [
                    "status",
                    "run_id",
                    "seed",
                    "elapsed_seconds",
                    "sandboxes_created",
                    "cycles_completed",
                    "nodes_exercised",
                    "request_count",
                    "http_status_counts",
                    "latencies",
                    "request_storm",
                ]
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
