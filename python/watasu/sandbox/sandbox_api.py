from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

ALL_TRAFFIC = "0.0.0.0/0"

SandboxState = str
SandboxQuery = Dict[str, Any]
SandboxLifecycle = Dict[str, Any]
SandboxNetworkOpts = Dict[str, Any]
SandboxNetworkUpdate = Dict[str, Any]
SandboxNetworkRules = List[Dict[str, Any]]
SandboxNetworkRule = Dict[str, Any]
SandboxNetworkRuleInfo = Dict[str, Any]
SandboxNetworkSelector = Any
SandboxNetworkSelectorContext = Dict[str, Any]
SandboxNetworkTransform = Any
McpServer = Dict[str, Any]
GitHubMcpServer = Dict[str, Any]
GitHubMcpServerConfig = Dict[str, Any]
GitStatus = Dict[str, Any]
GitBranches = Dict[str, Any]
GitFileStatus = Dict[str, Any]
GitResetMode = str


@dataclass
class SandboxInfoLifecycle:
    on_timeout: Optional[str] = None
    auto_resume: bool = False


@dataclass
class SandboxNetworkInfo:
    host: Optional[str] = None


@dataclass
class SandboxMetrics:
    sandbox_id: Optional[str] = None
    state: Optional[str] = None
    node: Optional[str] = None
    backend: Optional[str] = None
    cpu_count: Optional[int] = None
    memory_mb: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SnapshotInfo:
    snapshot_id: str
    sandbox_id: Optional[str] = None
    name: Optional[str] = None
    status: Optional[str] = None
    size_bytes: Optional[int] = None
    created_at: Optional[str] = None
    expires_at: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FileUrlInfo:
    method: str
    path: str
    url: str
    expires_at: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SandboxInfo:
    sandbox_id: str
    template_id: Optional[str] = None
    name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    state: Optional[str] = None
    lifecycle: Optional[SandboxInfoLifecycle] = None
    volume_mounts: List[Dict[str, str]] = field(default_factory=list)
    started_at: Optional[str] = None
    end_at: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


def sandbox_info_from_api(payload: Dict[str, Any]) -> SandboxInfo:
    template = payload.get("template") or payload.get("sandbox_template") or {}
    return SandboxInfo(
        sandbox_id=str(payload.get("id") or payload.get("sandbox_id") or ""),
        template_id=(
            str(payload.get("template_id"))
            if payload.get("template_id") is not None
            else str(
                template.get("slug")
                or template.get("id")
                or payload.get("template_version_id")
                or ""
            )
        )
        or None,
        name=payload.get("name"),
        metadata=payload.get("metadata") or {},
        state=payload.get("state"),
        lifecycle=sandbox_lifecycle_from_api(payload.get("lifecycle")),
        volume_mounts=sandbox_volume_mounts_from_api(
            payload.get("volume_mounts", payload.get("volumeMounts"))
        ),
        started_at=payload.get("started_at")
        or payload.get("created_at")
        or payload.get("ready_at"),
        end_at=payload.get("end_at") or payload.get("deadline_at"),
        raw=payload,
    )


def sandbox_volume_mounts_from_api(payload: Any) -> List[Dict[str, str]]:
    if not isinstance(payload, list):
        return []

    mounts = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        path = item.get("path")
        if name and path:
            mounts.append({"name": str(name), "path": str(path)})
    return mounts


def sandbox_lifecycle_from_api(payload: Any) -> Optional[SandboxInfoLifecycle]:
    if not isinstance(payload, dict):
        return None
    on_timeout = payload.get("on_timeout") or payload.get("onTimeout")
    auto_resume = payload.get("auto_resume", payload.get("autoResume", False))
    if on_timeout is None and auto_resume is None:
        return None
    return SandboxInfoLifecycle(on_timeout=on_timeout, auto_resume=_bool(auto_resume))


def sandbox_metrics_from_api(payload: Dict[str, Any]) -> SandboxMetrics:
    return SandboxMetrics(
        sandbox_id=_string(_first(payload, "sandbox_id", "sandboxId")),
        state=_string(payload.get("state")),
        node=_string(payload.get("node")),
        backend=_string(payload.get("backend")),
        cpu_count=_int(_first(payload, "cpu_count", "cpuCount")),
        memory_mb=_int(_first(payload, "memory_mb", "memoryMb")),
        raw=payload,
    )


def snapshot_info_from_api(payload: Dict[str, Any]) -> SnapshotInfo:
    snapshot_id = (
        payload.get("snapshot_id")
        or payload.get("snapshotId")
        or payload.get("checkpoint_id")
        or payload.get("checkpointId")
        or payload.get("id")
    )
    return SnapshotInfo(
        snapshot_id=str(snapshot_id or ""),
        sandbox_id=_string(_first(payload, "sandbox_id", "sandboxId")),
        name=_string(payload.get("name")),
        status=_string(payload.get("status")),
        size_bytes=_int(_first(payload, "size_bytes", "sizeBytes")),
        created_at=_string(_first(payload, "created_at", "createdAt")),
        expires_at=_string(_first(payload, "expires_at", "expiresAt")),
        raw=payload,
    )


def file_url_info_from_api(payload: Dict[str, Any]) -> FileUrlInfo:
    return FileUrlInfo(
        method=str(payload.get("method") or ""),
        path=str(payload.get("path") or ""),
        url=str(payload.get("url") or ""),
        expires_at=_string(_first(payload, "expires_at", "expiresAt")),
        raw=payload,
    )


def _first(payload: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _string(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in ("true", "1", 1):
        return True
    return False


def get_signature(*_args: Any, **_kwargs: Any) -> str:
    raise NotImplementedError("get_signature is not supported by Watasu yet")
