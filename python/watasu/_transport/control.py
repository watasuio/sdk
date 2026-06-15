from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from watasu.connection_config import ConnectionConfig
from watasu.exceptions import format_request_timeout_error
from watasu._transport.errors import map_http_error


class ControlClient:
    def __init__(self, config: ConnectionConfig):
        self.config = config
        self._session = requests.Session()
        self._session.trust_env = False

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        request_timeout: Optional[float] = None,
        resource: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            response = self._session.request(
                method,
                self.config.control_url(path),
                headers=self.config.auth_headers,
                json=json,
                params=params,
                timeout=self.config.get_request_timeout(request_timeout),
                proxies=_requests_proxies(self.config.proxy),
            )
        except requests.Timeout:
            raise format_request_timeout_error()

        if response.status_code < 400:
            if not response.content:
                return {}
            return response.json()

        payload: Any
        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        raise map_http_error(
            response.status_code, payload, response.text, resource=resource
        )

    def get(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        return self.request("POST", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        return self.request("DELETE", path, **kwargs)


def _requests_proxies(proxy: Any):
    if proxy is None:
        return None
    if isinstance(proxy, str):
        return {"http": proxy, "https": proxy}
    return proxy
