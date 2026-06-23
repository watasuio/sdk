from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union


@dataclass
class ProcessInfo:
    pid: Union[int, str]
    tag: Optional[str] = None
    cmd: Optional[str] = None
    args: List[str] = field(default_factory=list)
    envs: Dict[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None


@dataclass
class ProcessStatus:
    pid: Union[int, str]
    status: str
    id: Optional[Union[int, str]] = None
    os_pid: Optional[int] = None
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    cwd: Optional[str] = None
    user: Optional[str] = None
    pty: Optional[bool] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    exit_code: Optional[int] = None


@dataclass
class ProcessOutputEvent:
    cursor: int
    type: str
    data: bytes


@dataclass
class ProcessOutputSnapshot:
    pid: Union[int, str]
    status: str
    next_cursor: int
    truncated_before_cursor: bool
    events: List[ProcessOutputEvent] = field(default_factory=list)
    exit_code: Optional[int] = None
    finished_at: Optional[str] = None
