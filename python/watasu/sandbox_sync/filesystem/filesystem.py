from __future__ import annotations

import gzip as gzip_module
import base64
from io import IOBase, TextIOBase
from typing import Dict, IO, Iterator, List, Literal, Optional, Union
from urllib.parse import urlencode

from watasu._transport.data_plane import DataPlaneClient
from watasu._transport.process_ws import ProcessSocket
from watasu.exceptions import FileNotFoundException, InvalidArgumentException
from watasu.sandbox.filesystem.filesystem import (
    EntryInfo,
    WriteEntry,
    WriteInfo,
    entry_from_api,
    write_info_from_api,
)
from watasu.sandbox_sync.filesystem.watch_handle import WatchHandle


class Filesystem:
    """Filesystem helper backed by Watasu data-plane file endpoints."""

    def __init__(self, data_plane: DataPlaneClient):
        self._data_plane = data_plane

    def read(
        self,
        path: str,
        format: Literal["text", "bytes", "stream"] = "text",
        user=None,
        request_timeout: Optional[float] = None,
        gzip: bool = False,
    ):
        """Read a file as text, bytes, or a byte stream iterator."""
        params = {"path": path}
        if format == "stream":
            return self._data_plane.iter_bytes(
                "/runtime/v1/files",
                params=params,
                request_timeout=request_timeout,
                resource="file",
            )
        data = self._data_plane.get_bytes(
            "/runtime/v1/files",
            params=params,
            request_timeout=request_timeout,
            resource="file",
        )
        if gzip:
            data = gzip_module.decompress(data)
        if format == "bytes":
            return bytearray(data)
        if format == "text":
            return data.decode("utf-8")
        raise InvalidArgumentException("format must be 'text', 'bytes', or 'stream'")

    def read_bytes(
        self,
        path: str,
        user=None,
        request_timeout: Optional[float] = None,
        gzip: bool = False,
    ) -> bytes:
        """Read a file as bytes."""
        return bytes(
            self.read(
                path,
                format="bytes",
                user=user,
                request_timeout=request_timeout,
                gzip=gzip,
            )
        )

    def write(
        self,
        path: str,
        data: Union[str, bytes, IO],
        user=None,
        request_timeout: Optional[float] = None,
        gzip: bool = False,
        use_octet_stream: bool = False,
        metadata=None,
    ) -> WriteInfo:
        """Write bytes, text, or a file-like object to ``path``."""
        raw = _to_bytes(data)
        if gzip:
            raw = gzip_module.compress(raw)
        payload = self._data_plane.put_json(
            "/runtime/v1/files",
            params={"path": path, "gzip": "true"} if gzip else {"path": path},
            data=raw,
            request_timeout=request_timeout,
            resource="file",
        )
        return write_info_from_api(payload["file"])

    def write_files(
        self,
        files: List[WriteEntry],
        user=None,
        request_timeout: Optional[float] = None,
        gzip: bool = False,
        use_octet_stream: bool = False,
        metadata: Optional[Dict[str, str]] = None,
    ) -> List[WriteInfo]:
        """Write several files in one runtime API call."""
        if len(files) == 0:
            return []

        payload = self._data_plane.post_json(
            "/runtime/v1/files/write_files",
            json={
                "files": [
                    {
                        "path": file["path"],
                        "data_base64": base64.b64encode(
                            _maybe_gzip(_to_bytes(file["data"]), gzip)
                        ).decode("ascii"),
                        **({"gzip": True} if gzip else {}),
                    }
                    for file in files
                ]
            },
            request_timeout=request_timeout,
            resource="file",
        )
        return [write_info_from_api(item) for item in payload.get("files", [])]

    def list(
        self,
        path: str,
        depth=None,
        user=None,
        request_timeout: Optional[float] = None,
    ) -> List[EntryInfo]:
        """List directory entries below ``path``."""
        payload = self._data_plane.get_json(
            "/runtime/v1/directories",
            params={"path": path},
            request_timeout=request_timeout,
            resource="directory",
        )
        return [entry_from_api(item) for item in payload.get("entries", [])]

    def exists(
        self, path: str, user=None, request_timeout: Optional[float] = None
    ) -> bool:
        """Return whether a file or directory exists at ``path``."""
        try:
            self.get_info(path, user=user, request_timeout=request_timeout)
            return True
        except FileNotFoundException:
            return False

    def get_info(
        self, path: str, user=None, request_timeout: Optional[float] = None
    ) -> EntryInfo:
        """Return stat metadata for ``path``."""
        payload = self._data_plane.get_json(
            "/runtime/v1/files/stat",
            params={"path": path},
            request_timeout=request_timeout,
            resource="file",
        )
        return entry_from_api(payload["file"])

    def remove(
        self, path: str, user=None, request_timeout: Optional[float] = None
    ) -> None:
        """Remove a file at ``path``."""
        self._data_plane.delete_json(
            "/runtime/v1/files",
            params={"path": path},
            request_timeout=request_timeout,
            resource="file",
        )

    def rename(
        self,
        old_path: str,
        new_path: str,
        user=None,
        request_timeout: Optional[float] = None,
    ) -> EntryInfo:
        """Move or rename a file from ``old_path`` to ``new_path``."""
        payload = self._data_plane.post_json(
            "/runtime/v1/files/move",
            json={"from_path": old_path, "to_path": new_path},
            request_timeout=request_timeout,
            resource="file",
        )
        return entry_from_api(payload["file"])

    def make_dir(
        self, path: str, user=None, request_timeout: Optional[float] = None
    ) -> bool:
        """Create a directory, including parent directories when supported by the runtime."""
        self._data_plane.post_json(
            "/runtime/v1/directories",
            params={"path": path},
            request_timeout=request_timeout,
            resource="directory",
        )
        return True

    def watch_dir(
        self,
        path: str,
        user=None,
        request_timeout: Optional[float] = None,
        recursive: bool = False,
        include_entry: bool = False,
        allow_network_mounts: bool = False,
    ) -> WatchHandle:
        """Watch a directory for filesystem events."""
        query = urlencode(
            {
                "path": path,
                "recursive": "true" if recursive else "false",
                "include_entry": "true" if include_entry else "false",
                "allow_network_mounts": "true" if allow_network_mounts else "false",
            }
        )
        socket = ProcessSocket(
            self._data_plane.base_url,
            self._data_plane.token,
            f"/runtime/v1/files/watch?{query}",
            request_timeout=request_timeout,
            headers=self._data_plane.config.sandbox_headers,
        ).connect()
        return WatchHandle(socket, socket.frames(timeout=0))


def _to_bytes(data: Union[str, bytes, IO]) -> bytes:
    if isinstance(data, str):
        return data.encode("utf-8")
    if isinstance(data, bytes):
        return data
    if isinstance(data, TextIOBase):
        return data.read().encode("utf-8")
    if isinstance(data, IOBase):
        return data.read()
    raise InvalidArgumentException(f"Unsupported data type: {type(data)}")


def _maybe_gzip(data: bytes, enabled: bool) -> bytes:
    return gzip_module.compress(data) if enabled else data
