from __future__ import annotations

import queue
import threading
from typing import Iterable, List, Optional

from watasu.exceptions import SandboxException
from watasu.sandbox.filesystem.filesystem import (
    FilesystemEvent,
    FilesystemEventType,
    entry_from_api,
)


class WatchHandle:
    """Handle for a running filesystem watcher.

    Use ``get_new_events()`` to drain events observed since the previous call.
    Use ``stop()`` to close the watcher stream.
    """

    def __init__(self, socket, frames: Iterable[dict]) -> None:
        self._socket = socket
        self._events: "queue.Queue[object]" = queue.Queue()
        self._closed = False
        self._thread = threading.Thread(
            target=self._pump, args=(frames,), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop watching the directory."""
        self._closed = True
        self._socket.close()

    close = stop

    def get_new_events(self) -> List[FilesystemEvent]:
        """Return filesystem events received since the previous drain."""
        if self._closed:
            raise SandboxException("The watcher is already stopped")

        events: List[FilesystemEvent] = []
        while True:
            try:
                item = self._events.get_nowait()
            except queue.Empty:
                return events
            if isinstance(item, Exception):
                raise item
            events.append(item)

    def _pump(self, frames: Iterable[dict]) -> None:
        try:
            for frame in frames:
                if self._closed:
                    return
                if frame.get("type") != "events":
                    continue
                for event in frame.get("events") or []:
                    self._events.put(_event_from_api(event or {}))
        except Exception as error:
            if not self._closed:
                self._events.put(error)


def _event_from_api(payload: dict) -> FilesystemEvent:
    path = str(payload.get("path") or "")
    event_type = _event_type(str(payload.get("type") or "modify"))
    entry = payload.get("file")
    return FilesystemEvent(
        type=event_type,
        path=path,
        name=_relative_name(path),
        entry=entry_from_api(entry) if isinstance(entry, dict) else None,
    )


def _event_type(value: str) -> FilesystemEventType:
    if value == "create":
        return FilesystemEventType.CREATE
    if value in {"delete", "remove"}:
        return FilesystemEventType.REMOVE
    if value == "rename":
        return FilesystemEventType.RENAME
    return FilesystemEventType.WRITE


def _relative_name(path: str) -> str:
    return path.rstrip("/").split("/")[-1]
