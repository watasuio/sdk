from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from watasu._transport.control import ControlClient
from watasu._transport.data_plane import DataPlaneClient
from watasu.connection_config import (
    SESSION_OPERATION_REQUEST_TIMEOUT_SEC,
    ApiParams,
    ConnectionConfig,
)
from watasu.exceptions import (
    ConflictException,
    InvalidArgumentException,
    NotFoundException,
    SandboxException,
)
from watasu.sandbox.sandbox_api import (
    FileUrlInfo,
    SandboxInfo,
    file_url_info_from_api,
    sandbox_info_from_api,
    sandbox_metrics_from_api,
    snapshot_info_from_api,
)
from watasu.sandbox_sync.commands.command import Commands
from watasu.sandbox_sync.commands.pty import Pty
from watasu.sandbox_sync.filesystem.filesystem import Filesystem
from watasu.sandbox_sync.git import Git
from watasu.sandbox_sync.paginator import SandboxPaginator, SnapshotPaginator
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
    def id(self):
        """Sandbox id alias."""
        return self._sandbox_id

    @property
    def files(self) -> Filesystem:
        return self._filesystem

    @property
    def commands(self) -> Commands:
        return self._commands

    @property
    def pty(self):
        return self._pty

    @property
    def git(self):
        return self._git

    def __init__(
        self,
        sandbox_id: Optional[str] = None,
        *,
        connection_config: Optional[ConnectionConfig] = None,
        control: Optional[ControlClient] = None,
        session: Optional[Dict] = None,
        sandbox: Optional[Dict] = None,
        envs: Optional[Dict[str, str]] = None,
        template: Optional[str] = None,
        timeout: Optional[int] = None,
        metadata: Optional[Dict[str, str]] = None,
        secure: bool = True,
        allow_internet_access: bool = True,
        network=None,
        lifecycle=None,
        **opts: ApiParams,
    ) -> None:
        if (
            connection_config is None
            and control is None
            and session is None
            and sandbox is None
        ):
            if sandbox_id is not None and template is None:
                created = self.connect(sandbox_id, timeout=timeout, **opts)
            else:
                created = self.create(
                    template=template,
                    timeout=timeout,
                    metadata=metadata,
                    envs=envs,
                    secure=secure,
                    allow_internet_access=allow_internet_access,
                    network=network,
                    lifecycle=lifecycle,
                    **opts,
                )

            self.__dict__.update(created.__dict__)
            return

        if sandbox_id is None:
            raise InvalidArgumentException(
                "sandbox_id is required for internal sandbox construction"
            )
        if connection_config is None:
            raise InvalidArgumentException(
                "connection_config is required for internal sandbox construction"
            )

        self._sandbox_id = str(sandbox_id)
        self.connection_config = connection_config
        self._control = control or ControlClient(connection_config)
        self._session = session
        self._sandbox = sandbox or {}
        self._envs = dict(envs or {})
        self._data_plane = self._data_plane_from_session(session)
        self._filesystem = Filesystem(self._require_data_plane())
        self._commands = Commands(
            self._require_data_plane(), connection_config, self._envs
        )
        self._pty = Pty(self._require_data_plane(), connection_config)
        self._git = Git(self._require_data_plane())

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
        if lifecycle is not None:
            unsupported("lifecycle pause/resume")

        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        sandbox_params = {
            "template_id": template or cls.default_template,
            "timeout": timeout or 300,
            "metadata": metadata or {},
            "env_vars": envs or {},
            "secure": secure,
            "allow_internet_access": allow_internet_access,
        }
        sandbox_params.update(_network_payload(network))
        for key in ("team",):
            if key in opts:
                sandbox_params[key] = opts[key]
        response = control.post(
            "/sandboxes",
            json=sandbox_params,
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

    def _connect_instance(
        self, timeout: Optional[int] = None, **opts: ApiParams
    ) -> "Sandbox":
        """Reconnect this sandbox and refresh its data-plane session."""
        response = self._control.post(
            f"/sandboxes/{self.sandbox_id}/resume",
            json={"timeout": timeout} if timeout else {},
            resource="sandbox",
            request_timeout=_session_operation_request_timeout(
                self.connection_config, opts
            ),
        )
        self._sandbox = response.get("sandbox") or self._sandbox
        self._session = response.get("session")
        self._data_plane = self._data_plane_from_session(self._session)
        self._filesystem = Filesystem(self._require_data_plane())
        self._commands = Commands(
            self._require_data_plane(), self.connection_config, self._envs
        )
        self._pty = Pty(self._require_data_plane(), self.connection_config)
        self._git = Git(self._require_data_plane())
        return self

    @classmethod
    def _connect_class(
        cls, sandbox_id: str, timeout: Optional[int] = None, **opts: ApiParams
    ) -> "Sandbox":
        """Connect to an existing sandbox by id and return a ready ``Sandbox``."""
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        info_response = control.get(f"/sandboxes/{sandbox_id}", resource="sandbox")
        response = control.post(
            f"/sandboxes/{sandbox_id}/resume",
            json={"timeout": timeout} if timeout else {},
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
    reconnect = connect

    def is_running(self, request_timeout: Optional[float] = None) -> bool:
        """Return whether this sandbox is in a runtime-active lifecycle state."""
        try:
            payload = self._control.get(
                f"/sandboxes/{self.sandbox_id}",
                request_timeout=request_timeout,
                resource="sandbox",
            )
        except NotFoundException:
            return False

        sandbox = payload.get("sandbox") or payload
        return sandbox.get("state") in {
            "creating",
            "ready",
            "checkpointing",
            "restoring",
            "stopping",
        }

    def _kill_instance(self, **opts: ApiParams) -> bool:
        """Destroy this sandbox."""
        if self.connection_config.debug:
            return True
        return self._kill_class(
            self.sandbox_id, **self.connection_config.get_api_params(**opts)
        )

    @classmethod
    def _kill_class(cls, sandbox_id: str, **opts: ApiParams) -> bool:
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        control.delete(f"/sandboxes/{sandbox_id}", resource="sandbox")
        return True

    kill = _DualMethod(_kill_instance, _kill_class)

    def close(self) -> None:
        """Close the local SDK attachment without destroying the sandbox."""
        return None

    def _beta_pause_instance(self, **opts: ApiParams) -> bool:
        """Pause this sandbox. Returns ``False`` if it is already paused."""
        return self._beta_pause_class(
            self.sandbox_id, **self.connection_config.get_api_params(**opts)
        )

    @classmethod
    def _beta_pause_class(cls, sandbox_id: str, **opts: ApiParams) -> bool:
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        try:
            control.post(f"/sandboxes/{sandbox_id}/pause", resource="sandbox")
            return True
        except ConflictException:
            return False

    beta_pause = _DualMethod(_beta_pause_instance, _beta_pause_class)
    pause = beta_pause

    def _resume_instance(
        self, timeout: Optional[int] = None, **opts: ApiParams
    ) -> bool:
        """Resume this sandbox and refresh its data-plane session."""
        self._connect_instance(timeout=timeout, **opts)
        return True

    @classmethod
    def _resume_class(
        cls, sandbox_id: str, timeout: Optional[int] = None, **opts: ApiParams
    ) -> bool:
        cls._connect_class(sandbox_id, timeout=timeout, **opts)
        return True

    resume = _DualMethod(_resume_instance, _resume_class)

    def _set_timeout_instance(self, timeout: int, **opts: ApiParams) -> None:
        """Set this sandbox's remaining lifetime in seconds."""
        self._set_timeout_class(
            self.sandbox_id, timeout, **self.connection_config.get_api_params(**opts)
        )

    @classmethod
    def _set_timeout_class(
        cls, sandbox_id: str, timeout: int, **opts: ApiParams
    ) -> None:
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        control.post(
            f"/sandboxes/{sandbox_id}/timeout",
            json={"timeout": timeout},
            resource="sandbox",
        )

    set_timeout = _DualMethod(_set_timeout_instance, _set_timeout_class)

    def _get_info_instance(self, **opts: ApiParams) -> SandboxInfo:
        """Fetch the latest control-plane metadata for this sandbox."""
        return self._get_info_class(
            self.sandbox_id, **self.connection_config.get_api_params(**opts)
        )

    @classmethod
    def _get_info_class(cls, sandbox_id: str, **opts: ApiParams) -> SandboxInfo:
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        payload = control.get(f"/sandboxes/{sandbox_id}", resource="sandbox")
        return sandbox_info_from_api(payload.get("sandbox") or payload)

    get_info = _DualMethod(_get_info_instance, _get_info_class)

    def _get_metrics_instance(self, **opts: ApiParams):
        """Fetch latest sandbox metrics."""
        payload = self._control.get(
            f"/sandboxes/{self.sandbox_id}/metrics",
            resource="sandbox",
            request_timeout=opts.get("request_timeout"),
        )
        metrics = payload.get("metrics", payload)
        if isinstance(metrics, list):
            return [sandbox_metrics_from_api(item or {}) for item in metrics]
        return [sandbox_metrics_from_api(metrics or {})]

    @classmethod
    def _get_metrics_class(cls, sandbox_id: str, **opts: ApiParams):
        """Fetch sandbox metrics by id."""
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        payload = control.get(
            f"/sandboxes/{sandbox_id}/metrics",
            resource="sandbox",
            request_timeout=opts.get("request_timeout"),
        )
        metrics = payload.get("metrics", payload)
        if isinstance(metrics, list):
            return [sandbox_metrics_from_api(item or {}) for item in metrics]
        return [sandbox_metrics_from_api(metrics or {})]

    get_metrics = _DualMethod(_get_metrics_instance, _get_metrics_class)

    def _create_snapshot_instance(
        self,
        name: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        expires_at: Optional[str] = None,
        quiesce_mode: Optional[str] = None,
        **opts: ApiParams,
    ):
        """Create a Watasu checkpoint using snapshot naming."""
        body = _compact(
            {
                "name": name,
                "metadata": metadata,
                "expires_at": expires_at,
                "quiesce_mode": quiesce_mode,
            }
        )
        payload = self._control.post(
            f"/sandboxes/{self.sandbox_id}/snapshots",
            json=body,
            resource="sandbox",
            request_timeout=opts.get("request_timeout"),
        )
        return snapshot_info_from_api(
            payload.get("sandbox_checkpoint") or payload.get("snapshot") or payload
        )

    @classmethod
    def _create_snapshot_class(
        cls,
        sandbox_id: str,
        name: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        expires_at: Optional[str] = None,
        quiesce_mode: Optional[str] = None,
        **opts: ApiParams,
    ):
        """Create a Watasu checkpoint by sandbox id."""
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        body = _compact(
            {
                "name": name,
                "metadata": metadata,
                "expires_at": expires_at,
                "quiesce_mode": quiesce_mode,
            }
        )
        payload = control.post(
            f"/sandboxes/{sandbox_id}/snapshots",
            json=body,
            resource="sandbox",
            request_timeout=opts.get("request_timeout"),
        )
        return snapshot_info_from_api(
            payload.get("sandbox_checkpoint") or payload.get("snapshot") or payload
        )

    create_snapshot = _DualMethod(_create_snapshot_instance, _create_snapshot_class)

    def checkpoint(self, *args, **kwargs):
        """Watasu-native alias for ``create_snapshot``."""
        return self.create_snapshot(*args, **kwargs)

    def _list_snapshots_instance(self, **opts: ApiParams):
        """List checkpoints for this sandbox using snapshot naming."""
        payload = self._control.get(
            f"/sandboxes/{self.sandbox_id}/checkpoints",
            resource="sandbox",
            request_timeout=opts.get("request_timeout"),
        )
        checkpoints = payload.get("sandbox_checkpoints") or []
        return SnapshotPaginator([snapshot_info_from_api(item) for item in checkpoints])

    @classmethod
    def _list_snapshots_class(cls, sandbox_id: str, **opts: ApiParams):
        """List checkpoints for a sandbox by id."""
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        payload = control.get(
            f"/sandboxes/{sandbox_id}/checkpoints",
            resource="sandbox",
            request_timeout=opts.get("request_timeout"),
        )
        checkpoints = payload.get("sandbox_checkpoints") or []
        return SnapshotPaginator([snapshot_info_from_api(item) for item in checkpoints])

    list_snapshots = _DualMethod(_list_snapshots_instance, _list_snapshots_class)

    def _delete_snapshot_instance(self, snapshot_id: str, **opts: ApiParams) -> bool:
        """Delete a snapshot by id."""
        try:
            self._control.delete(
                f"/sandbox_snapshots/{snapshot_id}",
                resource="sandbox",
                request_timeout=opts.get("request_timeout"),
            )
            return True
        except NotFoundException:
            return False

    @classmethod
    def _delete_snapshot_class(cls, snapshot_id: str, **opts: ApiParams) -> bool:
        """Delete a snapshot by id. Returns ``False`` when not found."""
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        try:
            control.delete(
                f"/sandbox_snapshots/{snapshot_id}",
                resource="sandbox",
                request_timeout=opts.get("request_timeout"),
            )
            return True
        except NotFoundException:
            return False

    delete_snapshot = _DualMethod(_delete_snapshot_instance, _delete_snapshot_class)

    def restore(
        self,
        checkpoint_id=None,
        *,
        snapshot_id=None,
        timeout: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
        **opts: ApiParams,
    ) -> SandboxInfo:
        """Restore a checkpoint into a new sandbox and return its metadata."""
        selected_checkpoint_id = checkpoint_id if checkpoint_id is not None else snapshot_id
        if selected_checkpoint_id is None:
            raise InvalidArgumentException("checkpoint_id or snapshot_id is required")
        body = {"checkpoint_id": selected_checkpoint_id}
        if timeout_seconds is not None:
            body["timeout_seconds"] = timeout_seconds
        elif timeout is not None:
            body["timeout_seconds"] = timeout
        payload = self._control.post(
            f"/sandboxes/{self.sandbox_id}/restore",
            json=body,
            resource="sandbox",
            request_timeout=opts.get("request_timeout"),
        )
        return sandbox_info_from_api(payload.get("sandbox") or payload)

    @staticmethod
    def list(
        query: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
        next_token: Optional[str] = None,
        **opts: ApiParams,
    ) -> SandboxPaginator[SandboxInfo]:
        """Return a paginator for sandboxes visible to the configured API token.

        ``query`` supports ``metadata`` and ``state`` filters. The state values
        ``"running"`` and ``"paused"`` are resolved by the Watasu API.
        """
        team = opts.pop("team", None)

        def load_page(
            page_token: Optional[str], page_opts: Dict[str, Any]
        ) -> Tuple[List[SandboxInfo], Optional[str]]:
            config = ConnectionConfig(**{**opts, **page_opts})
            control = ControlClient(config)
            payload = control.get(
                "/sandboxes",
                params=_sandbox_list_params(query, limit, page_token, team),
                resource="sandbox",
                request_timeout=page_opts.get("request_timeout"),
            )
            return (
                [
                    sandbox_info_from_api(item)
                    for item in payload.get("sandboxes", [])
                    if isinstance(item, dict)
                ],
                payload.get("next_token"),
            )

        return SandboxPaginator(load_page=load_page, next_token=next_token)

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

    def upload_url(
        self,
        path: str,
        user: Optional[str] = None,
        use_signature_expiration: Optional[int] = None,
        expires_in_seconds: Optional[int] = None,
        request_timeout: Optional[float] = None,
    ) -> str:
        """Get a signed URL for uploading a file with a POST request."""
        return self.upload_url_info(
            path,
            user=user,
            use_signature_expiration=use_signature_expiration,
            expires_in_seconds=expires_in_seconds,
            request_timeout=request_timeout,
        ).url

    def download_url(
        self,
        path: str,
        user: Optional[str] = None,
        use_signature_expiration: Optional[int] = None,
        expires_in_seconds: Optional[int] = None,
        request_timeout: Optional[float] = None,
    ) -> str:
        """Get a signed URL for downloading a file with a GET request."""
        return self.download_url_info(
            path,
            user=user,
            use_signature_expiration=use_signature_expiration,
            expires_in_seconds=expires_in_seconds,
            request_timeout=request_timeout,
        ).url

    def upload_url_info(self, path: str, **opts) -> FileUrlInfo:
        """Get signed upload URL metadata for a sandbox file path."""
        return self._file_url_info("upload_url", path, **opts)

    def download_url_info(self, path: str, **opts) -> FileUrlInfo:
        """Get signed download URL metadata for a sandbox file path."""
        return self._file_url_info("download_url", path, **opts)

    def update_network(
        self,
        network: Optional[Dict[str, Any]] = None,
        *,
        request_timeout: Optional[float] = None,
        **opts: Any,
    ) -> None:
        """Atomically replace this sandbox's network egress policy."""
        response = self._control.put(
            f"/sandboxes/{self.sandbox_id}/network",
            json=_network_payload(network, opts),
            resource="sandbox",
            request_timeout=request_timeout,
        )
        self._sandbox = response.get("sandbox") or self._sandbox
        return None

    def __enter__(self):
        """Enter a context manager without changing sandbox state."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Destroy the sandbox when leaving a context manager."""
        self.kill()

    def _data_plane_from_session(self, session: Optional[Dict]) -> DataPlaneClient:
        if not session:
            raise SandboxException(
                "sandbox session is required for data-plane operations"
            )
        token = session.get("token") or session.get("access_token")
        url = session.get("data_plane_url")
        if not token or not url:
            raise SandboxException(
                "sandbox session did not include data_plane_url and token"
            )
        return DataPlaneClient(url, token, self.connection_config)

    def _require_data_plane(self) -> DataPlaneClient:
        if self._data_plane is None:
            raise SandboxException("sandbox data plane is not connected")
        return self._data_plane

    def _file_url_info(self, route: str, path: str, **opts) -> FileUrlInfo:
        body = _compact(
            {
                "path": path,
                "user": opts.get("user"),
                "use_signature_expiration": opts.get("use_signature_expiration"),
                "expires_in_seconds": opts.get("expires_in_seconds"),
            }
        )
        payload = self._control.post(
            f"/sandboxes/{self.sandbox_id}/files/{route}",
            json=body,
            resource="sandbox",
            request_timeout=opts.get("request_timeout"),
        )
        return file_url_info_from_api(payload.get("file_url") or payload)


def _reject_unsupported_opts(opts: Dict, keys: Iterable[str]) -> None:
    for key in keys:
        if key in opts and opts[key] is not None:
            unsupported(key)


def _sandbox_list_params(
    query: Optional[Dict[str, Any]],
    limit: Optional[int],
    next_token: Optional[str],
    team: Optional[str],
):
    params = []
    if team:
        params.append(("team", str(team)))
    if limit is not None:
        params.append(("limit", str(limit)))
    if next_token:
        params.append(("next_token", str(next_token)))

    if isinstance(query, dict):
        metadata = query.get("metadata")
        if isinstance(metadata, dict):
            for key, value in metadata.items():
                params.append((f"query[metadata][{key}]", str(value)))

        for state in _list_query_values(query.get("state")):
            params.append(("query[state][]", state))

    return params


def _list_query_values(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        values = []
        for item in value:
            values.extend(_list_query_values(item))
        return values
    return [str(value)]


def _compact(payload: Dict) -> Dict:
    return {key: value for key, value in payload.items() if value is not None}


def _session_operation_request_timeout(config: ConnectionConfig, opts: Dict) -> float:
    if opts.get("request_timeout") is not None:
        return float(opts["request_timeout"])
    return max(config.request_timeout, SESSION_OPERATION_REQUEST_TIMEOUT_SEC)


_NETWORK_KEY_ALIASES = {
    "allowOut": "allow_out",
    "denyOut": "deny_out",
    "allowInternetAccess": "allow_internet_access",
    "allowPackageRegistryAccess": "allow_package_registry_access",
    "allowPublicTraffic": "allow_public_traffic",
    "egressProfile": "egress_profile",
    "egressProfiles": "egress_profiles",
    "networkClass": "network_class",
}


def _network_payload(
    network: Optional[Dict[str, Any]] = None,
    opts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if network is not None:
        if not isinstance(network, dict):
            unsupported("network callable rules")
        payload.update(_normalize_network_keys(network))
    if opts:
        payload.update(_normalize_network_keys(opts))
    _reject_unsupported_network(payload)
    return payload


def _normalize_network_keys(network: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for key, value in network.items():
        if value is not None:
            normalized[_NETWORK_KEY_ALIASES.get(str(key), str(key))] = value
    return normalized


def _reject_unsupported_network(network: Dict[str, Any]) -> None:
    if "rules" in network:
        unsupported("network rules")
    if "mask_request_host" in network or "maskRequestHost" in network:
        unsupported("network request host masking")
    for key in ("allow_out", "deny_out"):
        if callable(network.get(key)):
            unsupported("network callable selectors")


def _host_only(value: str) -> str:
    value = str(value)
    if "://" in value:
        from urllib.parse import urlparse

        return urlparse(value).netloc
    return value.split("/", 1)[0]
