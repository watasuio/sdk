from __future__ import annotations

from typing import Any, Optional, TypedDict
from urllib.parse import SplitResult, urlsplit

from watasu.connection_config import ConnectionConfig
from watasu.exceptions import SandboxException


class SandboxOpts(TypedDict, total=False):
    """Internal sandbox construction options."""

    sandbox_id: str
    sandbox_domain: Optional[str]
    envd_version: Any
    envd_access_token: Optional[str]
    sandbox_url: Optional[str]
    traffic_access_token: Optional[str]
    connection_config: ConnectionConfig


class SandboxBase:
    """Shared sandbox properties for code that imports ``watasu.sandbox.main``."""

    mcp_port = 50005
    default_sandbox_timeout = 300
    default_template = "base"
    default_mcp_template = "mcp-gateway"

    def __init__(
        self,
        sandbox_id: str,
        connection_config: ConnectionConfig,
        sandbox_domain: Optional[str] = None,
        traffic_access_token: Optional[str] = None,
        sandbox_url: Optional[str] = None,
        envd_access_token: Optional[str] = None,
        envd_version: Any = None,
    ) -> None:
        self._sandbox_id = str(sandbox_id)
        self.connection_config = connection_config
        self._sandbox_domain = sandbox_domain
        self._traffic_access_token = traffic_access_token
        self._sandbox_url = sandbox_url
        self._envd_access_token_value = envd_access_token
        self._envd_version_value = envd_version
        self._mcp_token: Optional[str] = None

    @property
    def connection_config(self) -> ConnectionConfig:
        return self._connection_config

    @connection_config.setter
    def connection_config(self, value: ConnectionConfig) -> None:
        self._connection_config = value

    @property
    def sandbox_id(self) -> str:
        """Unique sandbox identifier."""
        return self._sandbox_id

    @property
    def sandbox_domain(self) -> Optional[str]:
        """Sandbox data-plane domain, when known."""
        return self._sandbox_domain

    @property
    def traffic_access_token(self) -> Optional[str]:
        """Traffic access token for restricted sandbox services, when present."""
        return self._traffic_access_token

    @property
    def envd_api_url(self) -> str:
        """Data-plane API URL."""
        return self.connection_config.sandbox_url or self._sandbox_url or ""

    @property
    def envd_direct_url(self) -> str:
        """Direct data-plane API URL."""
        return self.envd_api_url

    @property
    def _envd_access_token(self) -> Optional[str]:
        return self._envd_access_token_value

    @property
    def _envd_version(self) -> Any:
        return self._envd_version_value

    def get_host(self, port: int) -> str:
        """Return the public hostname advertised by this installation."""
        return self._public_port_url(port).netloc

    def get_mcp_url(self) -> str:
        """Return the conventional MCP URL for this sandbox."""
        return self._public_port_url(self.mcp_port)._replace(
            path="/mcp", query="", fragment=""
        ).geturl()

    def _public_port_url(self, port: int) -> SplitResult:
        if type(port) is not int or not 1 <= port <= 65_535:
            raise SandboxException("port must be an integer between 1 and 65535")
        template = getattr(self, "_sandbox", {}).get("public_port_url_template")
        if not isinstance(template, str) or "{port}" not in template:
            raise SandboxException(
                "sandbox response did not include a public port URL template"
            )
        try:
            url = urlsplit(template.replace("{port}", str(port)))
            if (
                url.scheme not in ("http", "https")
                or not url.hostname
                or url.username is not None
                or url.password is not None
            ):
                raise ValueError("invalid public port URL")
            host = url.hostname
            if ":" in host:
                host = f"[{host}]"
            if url.port is not None and url.port != (443 if url.scheme == "https" else 80):
                host = f"{host}:{url.port}"
            return url._replace(netloc=host)
        except ValueError:
            raise SandboxException(
                "sandbox response included an invalid public port URL template"
            ) from None
