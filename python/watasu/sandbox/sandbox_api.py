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
    status: Optional[str] = None


@dataclass
class SandboxNetworkInfo:
    host: Optional[str] = None


@dataclass
class SandboxMetrics:
    cpu_count: Optional[int] = None
    memory_mb: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SnapshotInfo:
    snapshot_id: str
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SandboxInfo:
    sandbox_id: str
    template_id: Optional[str] = None
    name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    state: Optional[str] = None
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
        started_at=payload.get("started_at")
        or payload.get("created_at")
        or payload.get("ready_at"),
        end_at=payload.get("end_at") or payload.get("deadline_at"),
        raw=payload,
    )


def get_signature(*_args: Any, **_kwargs: Any) -> str:
    raise NotImplementedError("get_signature is not supported by Watasu yet")
