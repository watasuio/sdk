from __future__ import annotations

import base64
import json
import queue
import threading
import time
from typing import Any, Dict, Iterable, Iterator, Optional, Union

from watasu.connection_config import KEEPALIVE_PING_INTERVAL_SEC
from watasu.exceptions import SandboxException, format_request_timeout_error


class ProcessSocket:
    def __init__(
        self,
        base_url: str,
        token: str,
        path: str,
        *,
        keepalive_interval: float = KEEPALIVE_PING_INTERVAL_SEC,
        request_timeout: Optional[float] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.path = path
        self.keepalive_interval = keepalive_interval
        self.request_timeout = request_timeout
        self._ws = None
        self._closed = False

    def connect(self) -> "ProcessSocket":
        try:
            import websocket
        except ImportError as error:
            raise SandboxException(
                "The 'websocket-client' package is required for command streaming."
            ) from error

        ws_url = _ws_url(self.base_url, self.path)
        self._ws = websocket.create_connection(
            ws_url,
            header=[f"Authorization: Bearer {self.token}"],
            timeout=self.request_timeout,
        )
        return self

    def send_json(self, payload: Dict[str, Any]) -> None:
        self._require_open().send(json.dumps(payload))

    def send_stdin(self, data: Union[str, bytes]) -> None:
        if isinstance(data, bytes):
            raw = data
        else:
            raw = data.encode("utf-8")
        self.send_json({"type": "stdin", "data": base64.b64encode(raw).decode("ascii")})

    def send_signal(self, signal: str = "SIGKILL") -> None:
        self.send_json({"type": "signal", "signal": signal})

    def close(self) -> None:
        self._closed = True
        if self._ws is not None:
            self._ws.close()

    def frames(self, timeout: Optional[float] = None) -> Iterator[Dict[str, Any]]:
        ws = self._require_open()
        deadline = None if timeout in (None, 0) else time.monotonic() + float(timeout)
        next_ping = time.monotonic() + self.keepalive_interval

        while not self._closed:
            now = time.monotonic()
            if now >= next_ping:
                try:
                    ws.ping("watasu-sdk")
                except Exception as error:
                    raise SandboxException(
                        f"process websocket ping failed: {error}"
                    ) from error
                next_ping = now + self.keepalive_interval

            socket_timeout = min(1.0, max(0.1, next_ping - now))
            if deadline is not None:
                remaining = deadline - now
                if remaining <= 0:
                    raise format_request_timeout_error()
                socket_timeout = min(socket_timeout, remaining)

            ws.settimeout(socket_timeout)
            try:
                message = ws.recv()
            except TimeoutError:
                continue
            except Exception as error:
                if _is_timeout_error(error):
                    continue
                if self._closed:
                    return
                raise SandboxException(f"process websocket failed: {error}") from error

            if message is None:
                return
            if isinstance(message, bytes):
                raise SandboxException("process websocket returned binary frame")
            try:
                frame = json.loads(message)
            except json.JSONDecodeError as error:
                raise SandboxException(
                    f"process websocket returned invalid JSON: {message}"
                ) from error
            if frame.get("type") in {"ready", "pong"}:
                continue
            if frame.get("type") == "error":
                raise SandboxException(
                    frame.get("message") or frame.get("code") or "process error"
                )
            yield frame

    def _require_open(self):
        if self._ws is None:
            raise SandboxException("process websocket is not connected")
        return self._ws


class ProcessEventStream:
    def __init__(self, socket: ProcessSocket, frames: Iterable[Dict[str, Any]]) -> None:
        self.socket = socket
        self._frames = iter(frames)

    def __iter__(self) -> "ProcessEventStream":
        return self

    def __next__(self) -> Dict[str, Any]:
        return next(self._frames)

    def close(self) -> None:
        self.socket.close()


class QueuedProcessEventStream:
    def __init__(
        self,
        socket: ProcessSocket,
        first_frame: Dict[str, Any],
        frames: Iterable[Dict[str, Any]],
    ) -> None:
        self.socket = socket
        self._queue: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        self._queue.put(first_frame)
        self._closed = False

        def pump() -> None:
            try:
                for frame in frames:
                    self._queue.put(frame)
            except Exception as error:
                if not self._closed:
                    self._queue.put(error)
            finally:
                self._queue.put(None)

        self._thread = threading.Thread(target=pump, daemon=True)
        self._thread.start()

    def __iter__(self) -> "QueuedProcessEventStream":
        return self

    def __next__(self) -> Dict[str, Any]:
        item = self._queue.get()
        if item is None:
            raise StopIteration
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self._closed = True
        self.socket.close()


def _ws_url(base_url: str, path: str) -> str:
    if base_url.startswith("https://"):
        prefix = "wss://"
        rest = base_url[len("https://") :]
    elif base_url.startswith("http://"):
        prefix = "ws://"
        rest = base_url[len("http://") :]
    else:
        prefix = "wss://"
        rest = base_url
    return f"{prefix}{rest.rstrip('/')}/{path.lstrip('/')}"


def _is_timeout_error(error: Exception) -> bool:
    name = error.__class__.__name__.lower()
    return "timeout" in name
