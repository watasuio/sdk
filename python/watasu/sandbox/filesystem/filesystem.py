from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, IO, List, Optional, TypedDict, Union


class FileType(Enum):
    FILE = "file"
    DIR = "dir"


@dataclass
class WriteInfo:
    name: str
    type: Optional[FileType]
    path: str
    metadata: Optional[Dict[str, str]] = None


class WriteEntry(TypedDict):
    """File path and data used by ``Filesystem.write_files``."""

    path: str
    data: Union[str, bytes, IO]


@dataclass
class EntryInfo(WriteInfo):
    size: int = 0
    mode: int = 0
    permissions: str = ""
    owner: str = ""
    group: str = ""
    modified_time: datetime = field(
        default_factory=lambda: datetime.fromtimestamp(0, timezone.utc)
    )
    symlink_target: Optional[str] = None


class FilesystemEventType(Enum):
    CREATE = "create"
    WRITE = "write"
    REMOVE = "remove"
    RENAME = "rename"


@dataclass
class FilesystemEvent:
    type: FilesystemEventType
    path: str
    name: str = ""
    entry: Optional[EntryInfo] = None


@dataclass
class ApplyDiffFailedHunk:
    index: int
    old_start: int


@dataclass
class ApplyDiffFailure:
    path: str
    error: str
    failed_hunk: Optional[ApplyDiffFailedHunk] = None


@dataclass
class ApplyDiffFileSummary:
    path: str
    kind: str
    added: int
    removed: int
    source_path: Optional[str] = None


@dataclass
class ApplyDiffSummary:
    requested: int
    applied: int
    failed: int


@dataclass
class ApplyDiffReport:
    status: str
    parsed_diff_blocks: int
    patches: int
    files: List[ApplyDiffFileSummary]
    summary: ApplyDiffSummary
    applied: List[str]
    failed: List[ApplyDiffFailure]
    touched: List[str]
    raw: Dict


def file_type_from_api(value) -> Optional[FileType]:
    if value in ("file", "regular"):
        return FileType.FILE
    if value in ("directory", "dir"):
        return FileType.DIR
    return None


def entry_from_api(payload: Dict) -> EntryInfo:
    path = payload.get("path") or ""
    name = payload.get("name") or path.rstrip("/").split("/")[-1]
    modified = (
        payload.get("modified_time")
        or payload.get("mtime")
        or payload.get("updated_at")
    )
    modified_time = _parse_time(modified)
    return EntryInfo(
        name=name,
        type=file_type_from_api(payload.get("type")),
        path=path,
        size=int(payload.get("bytes") or payload.get("size") or 0),
        mode=int(payload.get("mode") or 0),
        permissions=str(payload.get("permissions") or ""),
        owner=str(payload.get("owner") or ""),
        group=str(payload.get("group") or ""),
        modified_time=modified_time,
        symlink_target=payload.get("symlink_target"),
        metadata=payload.get("metadata") or None,
    )


def write_info_from_api(payload: Dict) -> WriteInfo:
    entry = entry_from_api(payload)
    return WriteInfo(
        name=entry.name, type=entry.type, path=entry.path, metadata=entry.metadata
    )


def apply_diff_report_from_api(payload: Dict) -> ApplyDiffReport:
    return ApplyDiffReport(
        status=str(payload.get("status") or ""),
        parsed_diff_blocks=int(payload.get("parsed_diff_blocks") or 0),
        patches=int(payload.get("patches") or 0),
        files=[
            ApplyDiffFileSummary(
                path=str(item.get("path") or ""),
                source_path=item.get("source_path"),
                kind=str(item.get("kind") or ""),
                added=int(item.get("added") or 0),
                removed=int(item.get("removed") or 0),
            )
            for item in payload.get("files", [])
            if isinstance(item, dict)
        ],
        summary=_apply_diff_summary(payload.get("summary") or {}),
        applied=[item for item in payload.get("applied", []) if isinstance(item, str)],
        failed=[
            _apply_diff_failure(item)
            for item in payload.get("failed", [])
            if isinstance(item, dict)
        ],
        touched=[item for item in payload.get("touched", []) if isinstance(item, str)],
        raw=payload,
    )


def _apply_diff_summary(payload: Dict) -> ApplyDiffSummary:
    return ApplyDiffSummary(
        requested=int(payload.get("requested") or 0),
        applied=int(payload.get("applied") or 0),
        failed=int(payload.get("failed") or 0),
    )


def _apply_diff_failure(payload: Dict) -> ApplyDiffFailure:
    failed_hunk = payload.get("failed_hunk")
    return ApplyDiffFailure(
        path=str(payload.get("path") or ""),
        error=str(payload.get("error") or ""),
        failed_hunk=(
            ApplyDiffFailedHunk(
                index=int(failed_hunk.get("index") or 0),
                old_start=int(failed_hunk.get("old_start") or 0),
            )
            if isinstance(failed_hunk, dict)
            else None
        ),
    )


def _parse_time(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.fromtimestamp(0, timezone.utc)
