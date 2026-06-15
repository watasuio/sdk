from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Dict, List, Optional, Union

from watasu._transport.control import ControlClient
from watasu.connection_config import ApiParams, ConnectionConfig
from watasu.exceptions import InvalidArgumentException, NotFoundException

VolumeApiParams = Dict[str, Any]
VolumeConnectionConfig = ConnectionConfig
VolumeFileType = str
VolumeWriteData = Union[str, bytes, bytearray, memoryview]


@dataclass
class VolumeInfo:
    """Control-plane metadata for a persistent Watasu volume."""

    volume_id: str
    name: str
    state: Optional[str] = None
    token: Optional[str] = None
    size_mb: Optional[int] = None
    size_bytes: Optional[int] = None
    node: Optional[str] = None
    metadata: Optional[Dict[str, str]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None

    @property
    def id(self) -> str:
        """Volume id alias."""
        return self.volume_id


VolumeAndToken = VolumeInfo


@dataclass
class VolumeEntryStat:
    """File or directory metadata returned by volume content operations."""

    path: str
    name: str
    type: VolumeFileType
    size: Optional[int] = None
    mode: Optional[int] = None
    uid: Optional[int] = None
    gid: Optional[int] = None
    atime: Any = None
    mtime: Any = None
    ctime: Any = None
    raw: Optional[Dict[str, Any]] = None


class _DualMethod:
    def __init__(self, instance_func, class_func):
        self.instance_func = instance_func
        self.class_func = class_func

    def __get__(self, obj, cls):
        if obj is None:
            return self.class_func.__get__(cls, cls)
        return self.instance_func.__get__(obj, cls)


class Volume:
    """Persistent Watasu volume that can be mounted into sandboxes."""

    def __init__(
        self,
        volume_id: str,
        *,
        name: Optional[str] = None,
        token: Optional[str] = None,
        connection_config: Optional[ConnectionConfig] = None,
        control: Optional[ControlClient] = None,
    ) -> None:
        if connection_config is None:
            raise InvalidArgumentException("connection_config is required")

        self.volume_id = str(volume_id)
        self.id = self.volume_id
        self.name = name or self.volume_id
        self.token = token
        self.connection_config = connection_config
        self._control = control or ControlClient(connection_config)

    @classmethod
    def create(
        cls, name: str, team: Optional[str] = None, **opts: ApiParams
    ) -> "Volume":
        """Create a persistent volume and return a connected SDK object."""
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        payload = control.post(
            "/volumes",
            json=_compact({"name": name, "team": team}),
            resource="volume",
            request_timeout=opts.get("request_timeout"),
        )
        return _volume_from_payload(cls, payload, config, control)

    @classmethod
    def connect(cls, volume_id: str, **opts: ApiParams) -> "Volume":
        """Connect to an existing volume by id or name."""
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        payload = control.get(
            f"/volumes/{volume_id}",
            resource="volume",
            request_timeout=opts.get("request_timeout"),
        )
        return _volume_from_payload(cls, payload, config, control)

    def _get_info_instance(
        self, path: Optional[str] = None, **opts: ApiParams
    ) -> Union[VolumeInfo, VolumeEntryStat]:
        """Fetch this volume metadata or path metadata when ``path`` is provided."""
        if path is None:
            return self._get_info_class(
                self.volume_id, **self.connection_config.get_api_params(**opts)
            )

        payload = self._control.get(
            f"/volumes/{self.volume_id}/path",
            params={"path": path},
            resource="volume",
            request_timeout=opts.get("request_timeout"),
        )
        return volume_entry_from_api(payload.get("file") or payload)

    @classmethod
    def _get_info_class(cls, volume_id: str, **opts: ApiParams) -> VolumeInfo:
        """Fetch metadata for an existing volume by id or name."""
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        payload = control.get(
            f"/volumes/{volume_id}",
            resource="volume",
            request_timeout=opts.get("request_timeout"),
        )
        return volume_info_from_api(payload.get("volume") or payload)

    get_info = _DualMethod(_get_info_instance, _get_info_class)

    def _list_instance(
        self, path: str = "/", depth: Optional[int] = None, **opts: ApiParams
    ) -> List[VolumeEntryStat]:
        """List files and directories under ``path``."""
        payload = self._control.get(
            f"/volumes/{self.volume_id}/directories",
            params=_compact({"path": path, "depth": depth}),
            resource="volume",
            request_timeout=opts.get("request_timeout"),
        )
        return [volume_entry_from_api(item) for item in payload.get("entries", [])]

    @classmethod
    def _list_class(
        cls, team: Optional[str] = None, **opts: ApiParams
    ) -> List[VolumeInfo]:
        """List volumes visible to the configured API key."""
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        payload = control.get(
            "/volumes",
            params=_compact({"team": team}),
            resource="volume",
            request_timeout=opts.get("request_timeout"),
        )
        return [volume_info_from_api(item) for item in payload.get("volumes", [])]

    list = _DualMethod(_list_instance, _list_class)

    def make_dir(
        self,
        path: str,
        uid: Optional[int] = None,
        gid: Optional[int] = None,
        mode: Optional[Union[int, str]] = None,
        force: Optional[bool] = None,
        **opts: ApiParams,
    ) -> VolumeEntryStat:
        """Create a directory inside the detached volume."""
        payload = self._control.post(
            f"/volumes/{self.volume_id}/directories",
            json=_compact(
                {"path": path, "uid": uid, "gid": gid, "mode": mode, "force": force}
            ),
            resource="volume",
            request_timeout=opts.get("request_timeout"),
        )
        return volume_entry_from_api(payload.get("file") or payload)

    def exists(self, path: str, **opts: ApiParams) -> bool:
        """Return whether a path exists inside the detached volume."""
        try:
            self.get_info(path, **opts)
            return True
        except NotFoundException:
            return False

    def update_metadata(
        self,
        path: str,
        uid: Optional[int] = None,
        gid: Optional[int] = None,
        mode: Optional[Union[int, str]] = None,
        **opts: ApiParams,
    ) -> VolumeEntryStat:
        """Update ownership or mode metadata for a path."""
        payload = self._control.patch(
            f"/volumes/{self.volume_id}/path",
            json=_compact({"path": path, "uid": uid, "gid": gid, "mode": mode}),
            resource="volume",
            request_timeout=opts.get("request_timeout"),
        )
        return volume_entry_from_api(payload.get("file") or payload)

    def read_file(
        self, path: str, format: str = "text", **opts: ApiParams
    ) -> Union[str, bytes, BytesIO]:
        """Read a file from the detached volume as text, bytes, or a stream."""
        payload = self._control.get(
            f"/volumes/{self.volume_id}/files",
            params={"path": path},
            resource="volume",
            request_timeout=opts.get("request_timeout"),
        )
        file_payload = payload.get("file") or payload
        content = file_payload.get("content_b64") or file_payload.get("content") or ""
        raw = base64.b64decode(content) if file_payload.get("content_b64") else str(content).encode()

        if format == "text":
            return raw.decode()
        if format in {"bytes", "blob"}:
            return raw
        if format == "stream":
            return BytesIO(raw)
        raise InvalidArgumentException(f"unsupported volume read format: {format}")

    def write_file(
        self,
        path: str,
        data: VolumeWriteData,
        uid: Optional[int] = None,
        gid: Optional[int] = None,
        mode: Optional[Union[int, str]] = None,
        force: Optional[bool] = None,
        **opts: ApiParams,
    ) -> VolumeEntryStat:
        """Write a file into the detached volume."""
        payload = self._control.put(
            f"/volumes/{self.volume_id}/files",
            json=_compact(
                {
                    "path": path,
                    "content_b64": base64.b64encode(_bytes(data)).decode(),
                    "uid": uid,
                    "gid": gid,
                    "mode": mode,
                    "force": force,
                }
            ),
            resource="volume",
            request_timeout=opts.get("request_timeout"),
        )
        return volume_entry_from_api(payload.get("file") or payload)

    def remove(self, path: str, **opts: ApiParams) -> bool:
        """Remove a file or directory from the detached volume."""
        self._control.delete(
            f"/volumes/{self.volume_id}/path",
            params={"path": path},
            resource="volume",
            request_timeout=opts.get("request_timeout"),
        )
        return True

    def _destroy_instance(self, **opts: ApiParams) -> bool:
        """Destroy this volume."""
        return self._destroy_class(
            self.volume_id, **self.connection_config.get_api_params(**opts)
        )

    @classmethod
    def _destroy_class(cls, volume_id: str, **opts: ApiParams) -> bool:
        """Destroy a volume by id or name. Returns ``False`` when it does not exist."""
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        try:
            control.delete(
                f"/volumes/{volume_id}",
                resource="volume",
                request_timeout=opts.get("request_timeout"),
            )
            return True
        except NotFoundException:
            return False

    destroy = _DualMethod(_destroy_instance, _destroy_class)
    delete = destroy


class AsyncVolume:
    """Async wrapper for persistent Watasu volume operations."""

    def __init__(self, volume: Volume) -> None:
        self._volume = volume
        self.volume_id = volume.volume_id
        self.id = volume.id
        self.name = volume.name
        self.token = volume.token

    @classmethod
    async def create(
        cls, name: str, team: Optional[str] = None, **opts: ApiParams
    ) -> "AsyncVolume":
        """Create a persistent volume asynchronously."""
        volume = await asyncio.to_thread(Volume.create, name, team=team, **opts)
        return cls(volume)

    @classmethod
    async def connect(cls, volume_id: str, **opts: ApiParams) -> "AsyncVolume":
        """Connect to an existing volume asynchronously."""
        volume = await asyncio.to_thread(Volume.connect, volume_id, **opts)
        return cls(volume)

    async def _get_info_instance(
        self, path: Optional[str] = None, **opts: ApiParams
    ) -> Union[VolumeInfo, VolumeEntryStat]:
        """Fetch this volume metadata or path metadata when ``path`` is provided."""
        return await asyncio.to_thread(self._volume.get_info, path, **opts)

    @classmethod
    async def _get_info_class(cls, volume_id: str, **opts: ApiParams) -> VolumeInfo:
        """Fetch metadata for an existing volume by id or name."""
        return await asyncio.to_thread(Volume.get_info, volume_id, **opts)

    get_info = _DualMethod(_get_info_instance, _get_info_class)

    async def _list_instance(
        self, path: str = "/", depth: Optional[int] = None, **opts: ApiParams
    ) -> List[VolumeEntryStat]:
        """List files and directories under ``path``."""
        return await asyncio.to_thread(self._volume.list, path, depth=depth, **opts)

    @classmethod
    async def _list_class(
        cls, team: Optional[str] = None, **opts: ApiParams
    ) -> List[VolumeInfo]:
        """List volumes visible to the configured API key."""
        return await asyncio.to_thread(Volume.list, team=team, **opts)

    list = _DualMethod(_list_instance, _list_class)

    async def _destroy_instance(self, **opts: ApiParams) -> bool:
        """Destroy this volume."""
        return await asyncio.to_thread(self._volume.destroy, **opts)

    @classmethod
    async def _destroy_class(cls, volume_id: str, **opts: ApiParams) -> bool:
        """Destroy a volume by id or name."""
        return await asyncio.to_thread(Volume.destroy, volume_id, **opts)

    destroy = _DualMethod(_destroy_instance, _destroy_class)
    delete = destroy

    async def make_dir(self, *args: Any, **kwargs: Any) -> VolumeEntryStat:
        """Create a directory inside the detached volume."""
        return await asyncio.to_thread(self._volume.make_dir, *args, **kwargs)

    async def exists(self, *args: Any, **kwargs: Any) -> bool:
        """Return whether a path exists inside the detached volume."""
        return await asyncio.to_thread(self._volume.exists, *args, **kwargs)

    async def update_metadata(self, *args: Any, **kwargs: Any) -> VolumeEntryStat:
        """Update ownership or mode metadata for a path."""
        return await asyncio.to_thread(self._volume.update_metadata, *args, **kwargs)

    async def read_file(self, *args: Any, **kwargs: Any):
        """Read a file from the detached volume."""
        return await asyncio.to_thread(self._volume.read_file, *args, **kwargs)

    async def write_file(self, *args: Any, **kwargs: Any) -> VolumeEntryStat:
        """Write a file into the detached volume."""
        return await asyncio.to_thread(self._volume.write_file, *args, **kwargs)

    async def remove(self, *args: Any, **kwargs: Any) -> bool:
        """Remove a file or directory from the detached volume."""
        return await asyncio.to_thread(self._volume.remove, *args, **kwargs)


def _volume_from_payload(
    cls, payload: Dict[str, Any], config: ConnectionConfig, control: ControlClient
) -> Volume:
    info = volume_info_from_api(payload.get("volume") or payload)
    return cls(
        info.volume_id,
        name=info.name,
        token=info.token,
        connection_config=config,
        control=control,
    )


def volume_info_from_api(payload: Dict[str, Any]) -> VolumeInfo:
    """Convert API volume metadata into ``VolumeInfo``."""
    volume_id = payload.get("volume_id") or payload.get("id")
    if volume_id is None:
        raise InvalidArgumentException("volume response did not include id")
    return VolumeInfo(
        volume_id=str(volume_id),
        name=str(payload.get("name") or volume_id),
        state=payload.get("state"),
        token=payload.get("token"),
        size_mb=payload.get("size_mb"),
        size_bytes=payload.get("size_bytes"),
        node=payload.get("node") or payload.get("node_name"),
        metadata={key: str(value) for key, value in (payload.get("metadata") or {}).items()},
        created_at=payload.get("created_at"),
        updated_at=payload.get("updated_at"),
        raw=payload,
    )


def volume_entry_from_api(payload: Dict[str, Any]) -> VolumeEntryStat:
    """Convert API file metadata into ``VolumeEntryStat``."""
    return VolumeEntryStat(
        path=str(payload.get("path") or ""),
        name=str(payload.get("name") or ""),
        type=str(payload.get("type") or "file"),
        size=payload.get("size") or payload.get("bytes"),
        mode=payload.get("mode"),
        uid=payload.get("uid"),
        gid=payload.get("gid"),
        atime=payload.get("atime"),
        mtime=payload.get("mtime"),
        ctime=payload.get("ctime"),
        raw=payload,
    )


def _bytes(data: VolumeWriteData) -> bytes:
    if isinstance(data, str):
        return data.encode()
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    if isinstance(data, memoryview):
        return data.tobytes()
    raise InvalidArgumentException("unsupported volume write data")


def _compact(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}
