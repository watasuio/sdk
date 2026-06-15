from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, IO, Optional, TypedDict, Union


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


def _parse_time(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.fromtimestamp(0, timezone.utc)
