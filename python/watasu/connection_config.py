from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, TypedDict, Union

ProxyTypes = Union[str, Mapping[str, str]]
Username = str

KEEPALIVE_PING_INTERVAL_SEC = 50
SESSION_OPERATION_REQUEST_TIMEOUT_SEC = 150


class ApiParams(TypedDict, total=False):
    api_key: Optional[str]
    access_token: Optional[str]
    domain: Optional[str]
    request_timeout: Optional[float]
    headers: Optional[Dict[str, str]]
    extra_sandbox_headers: Optional[Dict[str, str]]
    proxy: Optional[ProxyTypes]
    api_url: Optional[str]
    sandbox_url: Optional[str]
    data_plane_domain: Optional[str]
    debug: Optional[bool]


@dataclass
class ConnectionConfig:
    """Connection settings used by the Watasu control-plane and data-plane clients.

    Most users only need to set ``WATASU_API_KEY``. ``domain`` defaults to
    ``watasu.io`` and produces ``https://api.watasu.io/v1`` for control-plane
    calls. Data-plane URLs and tokens are taken from the sandbox ``session``
    returned by ``Sandbox.create`` or ``Sandbox.connect`` unless
    ``sandbox_url`` is set to override the data-plane base URL, primarily for
    local runtimes.
    """

    api_key: Optional[str] = None
    domain: Optional[str] = None
    envd_port = 49983

    request_timeout: Optional[float] = 60
    headers: Dict[str, str] = field(default_factory=dict)
    extra_sandbox_headers: Dict[str, str] = field(default_factory=dict)
    proxy: Optional[ProxyTypes] = None
    api_url: Optional[str] = None
    sandbox_url: Optional[str] = None
    data_plane_domain: Optional[str] = None
    debug: bool = False

    def __init__(
        self,
        api_key: Optional[str] = None,
        access_token: Optional[str] = None,
        domain: Optional[str] = None,
        request_timeout: Optional[float] = None,
        headers: Optional[Dict[str, str]] = None,
        extra_sandbox_headers: Optional[Dict[str, str]] = None,
        proxy: Optional[ProxyTypes] = None,
        api_url: Optional[str] = None,
        sandbox_url: Optional[str] = None,
        data_plane_domain: Optional[str] = None,
        debug: Optional[bool] = None,
        **_: Any,
    ) -> None:
        self.api_key = api_key or access_token or os.environ.get("WATASU_API_KEY")
        self.domain = domain or os.environ.get("WATASU_DOMAIN") or "watasu.io"
        self.data_plane_domain = (
            data_plane_domain
            or os.environ.get("WATASU_DATA_PLANE_DOMAIN")
            or "watasuhost.com"
        )
        self.api_url = (
            api_url
            or os.environ.get("WATASU_API_URL")
            or f"https://api.{self.domain}/v1"
        ).rstrip("/")
        self.sandbox_url = (
            sandbox_url or os.environ.get("WATASU_SANDBOX_URL") or None
        )
        if request_timeout == 0:
            self.request_timeout = None
        elif request_timeout is not None:
            self.request_timeout = float(request_timeout)
        else:
            self.request_timeout = float(os.environ.get("WATASU_REQUEST_TIMEOUT", "60"))
        self.headers = dict(headers or {})
        self.extra_sandbox_headers = dict(extra_sandbox_headers or {})
        self.proxy = proxy
        self.debug = (
            bool(debug)
            if debug is not None
            else os.environ.get("WATASU_DEBUG", "").lower() in {"1", "true", "yes"}
        )

    def get_request_timeout(
        self, request_timeout: Optional[float] = None
    ) -> Optional[float]:
        """Return the effective timeout in seconds for one HTTP request."""
        if request_timeout == 0:
            return None
        return self.request_timeout if request_timeout is None else float(request_timeout)

    def get_api_params(self, **overrides: Any) -> Dict[str, Any]:
        """Return constructor kwargs that preserve this config with optional overrides."""
        params = {
            "api_key": self.api_key,
            "access_token": self.api_key,
            "domain": self.domain,
            "request_timeout": self.request_timeout,
            "headers": dict(self.headers),
            "extra_sandbox_headers": dict(self.extra_sandbox_headers),
            "proxy": self.proxy,
            "api_url": self.api_url,
            "sandbox_url": self.sandbox_url,
            "data_plane_domain": self.data_plane_domain,
            "debug": self.debug,
        }
        params.update(
            {key: value for key, value in overrides.items() if value is not None}
        )
        return params

    @property
    def auth_headers(self) -> Dict[str, str]:
        """HTTP headers including the bearer token when one is configured."""
        headers = dict(self.headers)
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @property
    def sandbox_headers(self) -> Dict[str, str]:
        """HTTP headers to send to sandbox data-plane requests."""
        return {**self.headers, **self.extra_sandbox_headers}

    def get_sandbox_url(self, sandbox_id: str, sandbox_domain: Optional[str]) -> str:
        """Return the sandbox data-plane API URL for a Watasu route token."""
        if self.sandbox_url:
            return self.sandbox_url
        if self.debug:
            return "http://localhost:49983"
        domain = sandbox_domain or self.data_plane_domain
        return f"https://{sandbox_id}.sandbox.{domain}"

    def get_host(
        self, sandbox_id: str, sandbox_domain: Optional[str], port: int
    ) -> str:
        """Return the public hostname for a Watasu sandbox route token and port."""
        if self.debug:
            return f"localhost:{port}"
        domain = sandbox_domain or self.data_plane_domain
        return f"p{port}-{sandbox_id}.sandbox.{domain}"

    def control_url(self, path: str) -> str:
        """Build an absolute control-plane API URL for a ``/v1`` path."""
        return f"{self.api_url}/{path.lstrip('/')}"
