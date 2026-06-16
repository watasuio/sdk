from __future__ import annotations

from typing import Any, Dict, Iterator, Optional

import requests

from watasu.connection_config import ConnectionConfig
from watasu.exceptions import format_request_timeout_error
from watasu._transport.errors import map_http_error


class DataPlaneClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        config: ConnectionConfig,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.config = config

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Connection": "close",
            **self.config.sandbox_headers,
            "Authorization": f"Bearer {self.token}",
        }

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[bytes] = None,
        request_timeout: Optional[float] = None,
        resource: Optional[str] = None,
        stream: bool = False,
    ) -> requests.Response:
        session = requests.Session()
        session.trust_env = False
        try:
            response = session.request(
                method,
                self.url(path),
                headers=self.headers,
                params=params,
                json=json,
                data=data,
                timeout=self.config.get_request_timeout(request_timeout),
                proxies=_requests_proxies(self.config.proxy),
                stream=stream,
            )
        except requests.Timeout:
            session.close()
            raise format_request_timeout_error()
        except Exception:
            session.close()
            raise

        if response.status_code < 400:
            if stream:
                setattr(response, "_watasu_session", session)
            else:
                _ = response.content
                session.close()
            return response

        try:
            payload: Any = response.json()
            fallback = response.text
        except ValueError:
            payload = response.text
            fallback = response.text
        finally:
            response.close()
            session.close()
        raise map_http_error(
            response.status_code, payload, fallback, resource=resource
        )

    def get_json(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        return self.request("GET", path, **kwargs).json()

    def post_json(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        return _json_or_empty(self.request("POST", path, **kwargs))

    def put_json(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        return _json_or_empty(self.request("PUT", path, **kwargs))

    def delete_json(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        return _json_or_empty(self.request("DELETE", path, **kwargs))

    def get_bytes(self, path: str, **kwargs: Any) -> bytes:
        return self.request("GET", path, **kwargs).content

    def iter_bytes(
        self, path: str, chunk_size: int = 65_536, **kwargs: Any
    ) -> Iterator[bytes]:
        response = self.request("GET", path, stream=True, **kwargs)
        return _iter_content_and_close(response, chunk_size)


def _requests_proxies(proxy: Any):
    if proxy is None:
        return None
    if isinstance(proxy, str):
        return {"http": proxy, "https": proxy}
    return proxy


def _iter_content_and_close(
    response: requests.Response, chunk_size: int
) -> Iterator[bytes]:
    try:
        yield from response.iter_content(chunk_size=chunk_size)
    finally:
        response.close()
        session = getattr(response, "_watasu_session", None)
        if session is not None:
            session.close()


def _json_or_empty(response: requests.Response) -> Dict[str, Any]:
    if not response.content:
        return {}
    return response.json()
