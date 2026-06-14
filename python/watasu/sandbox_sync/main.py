from __future__ import annotations

from typing import Dict, Iterable, Optional

from watasu._transport.control import ControlClient
from watasu._transport.data_plane import DataPlaneClient
from watasu.connection_config import (
    SESSION_OPERATION_REQUEST_TIMEOUT_SEC,
    ApiParams,
    ConnectionConfig,
)
from watasu.exceptions import InvalidArgumentException, SandboxException
from watasu.sandbox.sandbox_api import SandboxInfo, sandbox_info_from_api
from watasu.sandbox_sync.commands.command import Commands
from watasu.sandbox_sync.filesystem.filesystem import Filesystem
from watasu.sandbox_sync.paginator import SandboxPaginator
from watasu.stubs import unsupported


class _DualMethod:
    def __init__(self, instance_func, class_func):
        self.instance_func = instance_func
        self.class_func = class_func

    def __get__(self, obj, cls):
        if obj is None:
            return self.class_func.__get__(cls, cls)
        return self.instance_func.__get__(obj, cls)


class Sandbox:
    """A running Watasu sandbox.

    ``Sandbox.create`` and ``Sandbox.connect`` each perform one control-plane
    operation and require the API to return a usable data-plane session in the
    same response. The SDK does not poll sandbox status or wait for readiness on
    the caller's behalf; Phoenix encapsulates that lifecycle wait inside the API
    request.
    """

    default_template = "base"

    @property
    def sandbox_id(self):
        return self._sandbox_id

    @property
    def files(self) -> Filesystem:
        return self._filesystem

    @property
    def commands(self) -> Commands:
        return self._commands

    @property
    def pty(self):
        unsupported("sandbox.pty")

    @property
    def git(self):
        unsupported("sandbox.git")

    def __init__(
        self,
        sandbox_id: str,
        *,
        connection_config: ConnectionConfig,
        control: Optional[ControlClient] = None,
        session: Optional[Dict] = None,
        sandbox: Optional[Dict] = None,
        envs: Optional[Dict[str, str]] = None,
    ) -> None:
        self._sandbox_id = str(sandbox_id)
        self.connection_config = connection_config
        self._control = control or ControlClient(connection_config)
        self._session = session
        self._sandbox = sandbox or {}
        self._envs = dict(envs or {})
        self._data_plane = self._data_plane_from_session(session)
        self._filesystem = Filesystem(self._require_data_plane())
        self._commands = Commands(self._require_data_plane(), connection_config, self._envs)

    @classmethod
    def create(
        cls,
        template: Optional[str] = None,
        timeout: Optional[int] = None,
        metadata: Optional[Dict[str, str]] = None,
        envs: Optional[Dict[str, str]] = None,
        secure: bool = True,
        allow_internet_access: bool = True,
        network=None,
        lifecycle=None,
        **opts: ApiParams,
    ) -> "Sandbox":
        """Create a sandbox and return it with ``files`` and ``commands`` ready.

        Parameters configure Watasu sandbox creation.
        Watasu resolves ``template`` server-side, defaulting to ``"base"``.
        ``timeout`` is the sandbox lifetime in seconds, not the HTTP request
        timeout. The returned object always has an active data-plane session.
        """
        _reject_unsupported_opts(opts, ["mcp", "volume_mounts"])
        if network is not None:
            unsupported("network callable rules")
        if lifecycle is not None:
            unsupported("lifecycle pause/resume")

        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        sandbox_params = {
            "template": template or cls.default_template,
            "timeout_seconds": timeout or 300,
            "metadata": metadata or {},
            "allow_internet_access": allow_internet_access,
        }
        for key in (
            "template_version_id",
            "team",
            "cpu",
            "memory_mb",
            "disk_mb",
            "network_class",
            "allow_package_registry_access",
            "exposed_ports",
        ):
            if key in opts:
                sandbox_params[key] = opts[key]
        response = control.post(
            "/sandboxes",
            json={"sandbox": sandbox_params},
            resource="sandbox",
            request_timeout=_session_operation_request_timeout(config, opts),
        )
        sandbox = response.get("sandbox") or response
        session = response.get("session")
        sandbox_id = sandbox.get("id") or sandbox.get("sandbox_id")
        if not sandbox_id:
            raise SandboxException("create response did not include sandbox id")
        return cls(
            str(sandbox_id),
            connection_config=config,
            control=control,
            session=session,
            sandbox=sandbox,
            envs=envs,
        )

    def _connect_instance(self, timeout: Optional[int] = None, **opts: ApiParams) -> "Sandbox":
        """Reconnect this sandbox and refresh its data-plane session."""
        response = self._control.post(
            f"/sandboxes/{self.sandbox_id}/connect",
            json={"connect": {"timeout_seconds": timeout}} if timeout else {"connect": {}},
            resource="sandbox",
            request_timeout=_session_operation_request_timeout(self.connection_config, opts),
        )
        self._sandbox = response.get("sandbox") or self._sandbox
        self._session = response.get("session")
        self._data_plane = self._data_plane_from_session(self._session)
        self._filesystem = Filesystem(self._require_data_plane())
        self._commands = Commands(self._require_data_plane(), self.connection_config, self._envs)
        return self

    @classmethod
    def _connect_class(cls, sandbox_id: str, timeout: Optional[int] = None, **opts: ApiParams) -> "Sandbox":
        """Connect to an existing sandbox by id and return a ready ``Sandbox``."""
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        info_response = control.get(f"/sandboxes/{sandbox_id}", resource="sandbox")
        response = control.post(
            f"/sandboxes/{sandbox_id}/connect",
            json={"connect": {"timeout_seconds": timeout}} if timeout else {"connect": {}},
            resource="sandbox",
            request_timeout=_session_operation_request_timeout(config, opts),
        )
        return cls(
            str(sandbox_id),
            connection_config=config,
            control=control,
            session=response.get("session"),
            sandbox=response.get("sandbox") or info_response.get("sandbox") or {},
        )

    connect = _DualMethod(_connect_instance, _connect_class)

    def _kill_instance(self, **opts: ApiParams) -> bool:
        """Destroy this sandbox."""
        if self.connection_config.debug:
            return True
        return self._kill_class(self.sandbox_id, **self.connection_config.get_api_params(**opts))

    @classmethod
    def _kill_class(cls, sandbox_id: str, **opts: ApiParams) -> bool:
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        control.delete(f"/sandboxes/{sandbox_id}", resource="sandbox")
        return True

    kill = _DualMethod(_kill_instance, _kill_class)

    def _set_timeout_instance(self, timeout: int, **opts: ApiParams) -> None:
        """Set this sandbox's remaining lifetime in seconds."""
        self._set_timeout_class(self.sandbox_id, timeout, **self.connection_config.get_api_params(**opts))

    @classmethod
    def _set_timeout_class(cls, sandbox_id: str, timeout: int, **opts: ApiParams) -> None:
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        control.patch(
            f"/sandboxes/{sandbox_id}",
            json={"sandbox": {"timeout_seconds": timeout}},
            resource="sandbox",
        )

    set_timeout = _DualMethod(_set_timeout_instance, _set_timeout_class)

    def _get_info_instance(self, **opts: ApiParams) -> SandboxInfo:
        """Fetch the latest control-plane metadata for this sandbox."""
        return self._get_info_class(self.sandbox_id, **self.connection_config.get_api_params(**opts))

    @classmethod
    def _get_info_class(cls, sandbox_id: str, **opts: ApiParams) -> SandboxInfo:
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        payload = control.get(f"/sandboxes/{sandbox_id}", resource="sandbox")
        return sandbox_info_from_api(payload.get("sandbox") or payload)

    get_info = _DualMethod(_get_info_instance, _get_info_class)

    @staticmethod
    def list(**opts: ApiParams) -> SandboxPaginator[SandboxInfo]:
        """List sandboxes visible to the configured API token."""
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        payload = control.get("/sandboxes")
        return SandboxPaginator([sandbox_info_from_api(item) for item in payload.get("sandboxes", [])])

    def get_host(self, port: int) -> str:
        """Return the public hostname for an exposed sandbox port."""
        payload = self._control.get(f"/sandboxes/{self.sandbox_id}/ports/{int(port)}")
        port_info = payload.get("sandbox_port") or payload.get("port") or payload
        host_or_url = port_info.get("host") or port_info.get("url")
        if not host_or_url:
            route_token = self._sandbox.get("route_token")
            if not route_token:
                raise SandboxException("port response did not include host or url")
            return f"p{port}-{route_token}.sandbox.{self.connection_config.data_plane_domain}"
        return _host_only(host_or_url)

    def pause(self, *args, **kwargs) -> bool:
        unsupported("Sandbox.pause")

    beta_pause = pause

    def resume(self, *args, **kwargs) -> bool:
        unsupported("Sandbox.resume")

    def create_snapshot(self, *args, **kwargs):
        unsupported("Sandbox.create_snapshot")

    def checkpoint(self, *args, **kwargs):
        unsupported("Sandbox.checkpoint")

    def restore(self, *args, **kwargs):
        unsupported("Sandbox.restore")

    def __enter__(self):
        """Enter a context manager without changing sandbox state."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Destroy the sandbox when leaving a context manager."""
        self.kill()

    def _data_plane_from_session(self, session: Optional[Dict]) -> DataPlaneClient:
        if not session:
            raise SandboxException("sandbox session is required for data-plane operations")
        token = session.get("token") or session.get("access_token")
        url = session.get("data_plane_url")
        if not token or not url:
            raise SandboxException("sandbox session did not include data_plane_url and token")
        return DataPlaneClient(url, token, self.connection_config)

    def _require_data_plane(self) -> DataPlaneClient:
        if self._data_plane is None:
            raise SandboxException("sandbox data plane is not connected")
        return self._data_plane



def _reject_unsupported_opts(opts: Dict, keys: Iterable[str]) -> None:
    for key in keys:
        if key in opts and opts[key] is not None:
            unsupported(key)


def _session_operation_request_timeout(config: ConnectionConfig, opts: Dict) -> float:
    if opts.get("request_timeout") is not None:
        return float(opts["request_timeout"])
    return max(config.request_timeout, SESSION_OPERATION_REQUEST_TIMEOUT_SEC)


def _host_only(value: str) -> str:
    value = str(value)
    if "://" in value:
        from urllib.parse import urlparse

        return urlparse(value).netloc
    return value.split("/", 1)[0]
