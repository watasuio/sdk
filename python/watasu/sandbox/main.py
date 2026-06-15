from __future__ import annotations

from typing import Any, Optional, TypedDict

from watasu.connection_config import ConnectionConfig


class SandboxOpts(TypedDict, total=False):
    """Internal sandbox construction options used by compatibility imports."""

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
        """Data-plane API URL used by compatibility callers."""
        return self._sandbox_url or ""

    @property
    def _envd_access_token(self) -> Optional[str]:
        return self._envd_access_token_value

    @property
    def _envd_version(self) -> Any:
        return self._envd_version_value

    def get_host(self, port: int) -> str:
        """Return the public host for ``port``.

        Concrete sandbox classes override this method because Watasu resolves
        exposed-port hosts through the control plane.
        """
        raise NotImplementedError("get_host must be implemented by Sandbox")

    def get_mcp_url(self) -> str:
        """Return the conventional MCP URL for this sandbox."""
        return f"https://{self.get_host(self.mcp_port)}/mcp"
