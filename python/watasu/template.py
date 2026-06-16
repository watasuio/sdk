from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import shlex
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TypedDict, Union

from watasu._transport.control import ControlClient
from watasu.connection_config import ApiParams, ConnectionConfig
from watasu.exceptions import BuildException, InvalidArgumentException, NotFoundException


class TemplateBuildStatus(str, Enum):
    """Status of a template build."""

    BUILDING = "building"
    WAITING = "waiting"
    READY = "ready"
    ERROR = "error"


@dataclass
class LogEntry:
    """Template build log entry."""

    timestamp: Optional[datetime]
    level: str
    message: str

    def __str__(self) -> str:
        timestamp = self.timestamp.isoformat() if self.timestamp else ""
        return f"[{timestamp}] [{self.level}] {self.message}"


class LogEntryStart(LogEntry):
    """Log entry emitted when a blocking template build starts."""

    def __init__(
        self,
        timestamp: Optional[datetime] = None,
        message: str = "Build started",
    ):
        super().__init__(timestamp or datetime.now(), "debug", message)


class LogEntryEnd(LogEntry):
    """Log entry emitted when a blocking template build finishes."""

    def __init__(
        self,
        timestamp: Optional[datetime] = None,
        message: str = "Build finished",
    ):
        super().__init__(timestamp or datetime.now(), "debug", message)


LogEntryLevel = str
OutputHandler = Callable[[Any], Any]


class CopyItem(TypedDict, total=False):
    """Copy item accepted by ``Template.copy_items``."""

    src: Union[str, List[str]]
    dest: str
    force_upload: bool
    forceUpload: bool
    user: str
    mode: int
    resolve_symlinks: bool
    resolveSymlinks: bool


@dataclass
class BuildStatusReason:
    """Reason for the current build status."""

    message: str
    step: Optional[str] = None
    log_entries: List[LogEntry] = field(default_factory=list)


@dataclass
class TemplateBuildStatusResponse:
    """Response from getting template build status."""

    build_id: str
    template_id: str
    status: TemplateBuildStatus
    log_entries: List[LogEntry]
    logs: List[str]
    reason: Optional[BuildStatusReason] = None


@dataclass
class TemplateTagInfo:
    """Information about assigned template tags."""

    build_id: str
    tags: List[str]


@dataclass
class TemplateTag:
    """Detailed information about a template tag."""

    tag: str
    build_id: str
    created_at: datetime


@dataclass
class BuildInfo:
    """Information about a template build."""

    template_id: str
    build_id: str
    name: str
    alias: str
    tags: List[str] = field(default_factory=list)


class ReadyCmd:
    """Ready-check command wrapper accepted by template builders."""

    def __init__(self, command: str):
        self._command = command

    def get_cmd(self) -> str:
        """Return the shell command used as the ready check."""
        return self._command


class TemplateBase:
    """Chainable package-spec template builder for Watasu."""

    _logs_refresh_frequency = 0.2

    def __init__(
        self,
        file_context_path: Optional[str] = None,
        file_ignore_patterns: Optional[List[str]] = None,
    ):
        self._file_context_path = file_context_path
        self._file_ignore_patterns = file_ignore_patterns or []
        self._base: Optional[str] = None
        self._from_image: Optional[str] = None
        self._from_image_registry: Optional[Dict[str, Any]] = None
        self._packages: Dict[str, List[str]] = {}
        self._files: List[Dict[str, Any]] = []
        self._setup: List[str] = []
        self._env: Dict[str, str] = {}
        self._current_workdir: Optional[str] = None
        self._current_user: Optional[str] = None
        self._start_cmd: Optional[str] = None
        self._ready_cmd: Optional[str] = None
        self._force = False

    def from_debian_image(self, variant: str = "stable") -> "TemplateBase":
        """Request a Debian public base image.

        The Watasu API fails closed until OCI image import is enabled.
        """
        return self.from_image(f"debian:{variant}")

    def from_ubuntu_image(self, variant: str = "latest") -> "TemplateBase":
        """Request an Ubuntu public base image.

        The Watasu API fails closed until OCI image import is enabled.
        """
        return self.from_image(f"ubuntu:{variant}")

    def from_python_image(self, version: str = "3") -> "TemplateBase":
        """Request a Python public base image.

        The Watasu API fails closed until OCI image import is enabled.
        """
        return self.from_image(f"python:{version}")

    def from_node_image(self, variant: str = "lts") -> "TemplateBase":
        """Request a Node.js public base image.

        The Watasu API fails closed until OCI image import is enabled.
        """
        return self.from_image(f"node:{variant}")

    def from_bun_image(self, variant: str = "latest") -> "TemplateBase":
        """Request a Bun public base image.

        The Watasu API fails closed until OCI image import is enabled.
        """
        return self.from_image(f"oven/bun:{variant}")

    def from_base_image(self) -> "TemplateBase":
        """Start from the Watasu platform base template."""
        return self.from_template("base")

    def from_image(
        self,
        image: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> "TemplateBase":
        """Request a public container image base.

        The Watasu API fails closed until OCI image import is enabled.
        """
        if (username and not password) or (password and not username):
            raise InvalidArgumentException(
                "Both username and password are required when providing registry credentials"
            )
        self._from_image = image
        self._base = None
        self._from_image_registry = None
        if username and password:
            self._from_image_registry = {
                "type": "registry",
                "username": username,
                "password": password,
            }
        return self

    def from_aws_registry(
        self,
        image: str,
        access_key_id: str,
        secret_access_key: str,
        region: str,
    ) -> "TemplateBase":
        """Request an AWS registry image base.

        The Watasu API fails closed until registry image import is enabled.
        """
        self._from_image = image
        self._base = None
        self._from_image_registry = {
            "type": "aws",
            "aws_access_key_id": access_key_id,
            "aws_secret_access_key": secret_access_key,
            "aws_region": region,
        }
        return self

    def from_gcp_registry(
        self,
        image: str,
        service_account_json: Union[str, Dict[str, Any]],
    ) -> "TemplateBase":
        """Request a GCP registry image base.

        The Watasu API fails closed until registry image import is enabled.
        """
        self._from_image = image
        self._base = None
        self._from_image_registry = {
            "type": "gcp",
            "service_account_json": service_account_json,
        }
        return self

    def from_template(self, template: str) -> "TemplateBase":
        """Start this build from a ready Watasu template slug, tag, or version id."""
        self._base = template
        self._from_image = None
        self._from_image_registry = None
        return self

    def from_dockerfile(self, dockerfile_content_or_path: str) -> "TemplateBase":
        """Parse a Dockerfile or Dockerfile path into the Watasu template build spec."""
        _parse_dockerfile_into_template(dockerfile_content_or_path, self)
        return self

    def copy(self, *args: Any, **kwargs: Any) -> "TemplateBase":
        """Copy local files or directories into the built template."""
        if len(args) < 2:
            raise InvalidArgumentException("copy requires src and dest")
        src = args[0]
        dest = args[1]
        sources = src if isinstance(src, list) else [src]
        for source in sources:
            self._add_copy_source(str(source), str(dest), len(sources) > 1, kwargs)
        return self

    def copy_items(self, items: List[Dict[str, Any]]) -> "TemplateBase":
        """Copy multiple local files or directories into the built template."""
        for item in items:
            self.copy(
                item["src"],
                item["dest"],
                force_upload=item.get("force_upload") or item.get("forceUpload"),
                user=item.get("user"),
                mode=item.get("mode"),
                resolve_symlinks=item.get("resolve_symlinks")
                if "resolve_symlinks" in item
                else item.get("resolveSymlinks"),
            )
        return self

    def remove(
        self,
        path: Union[str, List[str]],
        force: bool = False,
        recursive: bool = False,
        user: Optional[str] = None,
    ) -> "TemplateBase":
        paths = [path] if isinstance(path, str) else path
        flags = f"{'-r ' if recursive else ''}{'-f ' if force else ''}"
        return self.run_cmd(f"rm {flags}{' '.join(paths)}", user=user)

    def rename(
        self,
        src: str,
        dest: str,
        force: bool = False,
        user: Optional[str] = None,
    ) -> "TemplateBase":
        return self.run_cmd(f"mv {src} {dest}{' -f' if force else ''}", user=user)

    def make_dir(
        self,
        path: Union[str, List[str]],
        mode: Optional[int] = None,
        user: Optional[str] = None,
    ) -> "TemplateBase":
        paths = [path] if isinstance(path, str) else path
        mode_arg = f"-m {oct(mode)[2:]} " if mode is not None else ""
        return self.run_cmd(f"mkdir -p {mode_arg}{' '.join(paths)}", user=user)

    def make_symlink(
        self,
        src: str,
        dest: str,
        user: Optional[str] = None,
        force: bool = False,
    ) -> "TemplateBase":
        return self.run_cmd(f"ln -s {'-f ' if force else ''}{src} {dest}", user=user)

    def run_cmd(
        self,
        command: Union[str, List[str]],
        user: Optional[str] = None,
    ) -> "TemplateBase":
        command_text = " && ".join(command) if isinstance(command, list) else command
        self._setup.append(self._command_with_context(command_text, user))
        return self

    def set_workdir(self, workdir: str) -> "TemplateBase":
        self._current_workdir = workdir
        return self

    def set_user(self, user: str) -> "TemplateBase":
        self._current_user = user
        return self

    def pip_install(
        self,
        packages: Optional[Union[str, List[str]]] = None,
        g: bool = True,
    ) -> "TemplateBase":
        package_list = _string_list(packages)
        if package_list and g:
            self._add_packages("pip", package_list)
        else:
            suffix = " ".join(package_list) if package_list else "."
            self.run_cmd(f"python3 -m pip install {'--user ' if not g else ''}{suffix}")
        return self

    def npm_install(
        self,
        packages: Optional[Union[str, List[str]]] = None,
        g: bool = False,
        dev: bool = False,
    ) -> "TemplateBase":
        package_list = _string_list(packages)
        if package_list and g:
            self._add_packages("npm", package_list)
        else:
            self.run_cmd(
                f"npm install {'-g ' if g else ''}{'--save-dev ' if dev else ''}{' '.join(package_list)}".strip()
            )
        return self

    def bun_install(
        self,
        packages: Optional[Union[str, List[str]]] = None,
        g: bool = False,
        dev: bool = False,
    ) -> "TemplateBase":
        package_list = _string_list(packages)
        self.run_cmd(
            f"bun install {'-g ' if g else ''}{'--dev ' if dev else ''}{' '.join(package_list)}".strip()
        )
        return self

    def apt_install(
        self,
        packages: Union[str, List[str]],
        no_install_recommends: bool = False,
        fix_missing: bool = False,
    ) -> "TemplateBase":
        self._add_packages("apt", _string_list(packages))
        return self

    def add_mcp_server(self, servers: Union[str, List[str]]) -> "TemplateBase":
        """Install MCP servers in a template that starts from ``mcp-gateway``."""
        if self._base != "mcp-gateway":
            raise BuildException("MCP servers can only be added to mcp-gateway template")
        return self.run_cmd(f"mcp-gateway pull {' '.join(_string_list(servers))}", user="root")

    def git_clone(
        self,
        url: str,
        path: Optional[str] = None,
        branch: Optional[str] = None,
        depth: Optional[int] = None,
        user: Optional[str] = None,
    ) -> "TemplateBase":
        args = ["git clone"]
        if branch:
            args.extend([f"--branch {branch}", "--single-branch"])
        if depth:
            args.append(f"--depth {depth}")
        args.append(url)
        if path:
            args.append(path)
        return self.run_cmd(" ".join(args), user=user)

    def set_start_cmd(
        self,
        start_cmd: str,
        ready_cmd: Union[str, ReadyCmd],
    ) -> "TemplateBase":
        self._start_cmd = start_cmd
        self._ready_cmd = ready_cmd.get_cmd() if isinstance(ready_cmd, ReadyCmd) else ready_cmd
        return self

    def set_ready_cmd(self, ready_cmd: Union[str, ReadyCmd]) -> "TemplateBase":
        self._ready_cmd = ready_cmd.get_cmd() if isinstance(ready_cmd, ReadyCmd) else ready_cmd
        return self

    def set_envs(self, envs: Dict[str, str]) -> "TemplateBase":
        self._env.update(envs)
        return self

    def skip_cache(self) -> "TemplateBase":
        self._force = True
        return self

    def to_build_spec(self) -> Dict[str, Any]:
        """Return the snake_case package-spec payload sent to the Watasu API."""
        spec: Dict[str, Any] = {}
        if self._base:
            spec["from_template"] = self._base
        if self._from_image:
            spec["from_image"] = self._from_image
        if self._from_image_registry:
            spec["from_image_registry"] = self._from_image_registry
        if self._packages:
            spec["packages"] = self._packages
        if self._files:
            spec["files"] = self._files
        if self._setup:
            spec["setup"] = self._setup
        if self._env:
            spec["env"] = self._env
        if self._start_cmd:
            spec["start_cmd"] = self._start_cmd
        if self._ready_cmd:
            spec["ready_cmd"] = self._ready_cmd
        return spec

    @staticmethod
    def to_json(template: "TemplateBase") -> str:
        """Return the template package spec as formatted JSON."""
        return json.dumps(template.to_build_spec(), indent=2)

    @staticmethod
    def to_dockerfile(template: "TemplateBase") -> str:
        """Return a Dockerfile-shaped preview of the supported package spec."""
        spec = template.to_build_spec()
        lines = [f"FROM {spec.get('from_image') or spec.get('from_template') or 'base'}"]
        packages = spec.get("packages") or {}
        for package in packages.get("apt", []):
            lines.append(f"RUN apt-get update && apt-get install -y {package}")
        for package in packages.get("pip", []):
            lines.append(f"RUN python3 -m pip install {package}")
        for package in packages.get("npm", []):
            lines.append(f"RUN npm install -g {package}")
        for file in spec.get("files", []):
            lines.append(f"COPY {file.get('source_path') or file.get('path')} {file.get('path')}")
        for command in spec.get("setup", []):
            lines.append(f"RUN {command}")
        return "\n".join(lines) + "\n"

    def _add_packages(self, manager: str, packages: List[str]) -> None:
        self._packages.setdefault(manager, []).extend(packages)

    def _add_copy_source(
        self,
        source: str,
        dest: str,
        multiple_sources: bool,
        options: Dict[str, Any],
    ) -> None:
        source_path = self._resolve_context_path(source)
        resolve_symlinks = options.get("resolve_symlinks")
        if resolve_symlinks is None:
            resolve_symlinks = options.get("resolveSymlinks", True)

        if source_path.is_dir():
            for file_path in _walk_files(source_path, bool(resolve_symlinks)):
                relative_path = _to_posix(file_path.relative_to(source_path))
                context_relative_path = _to_posix(file_path.relative_to(self._context_path()))
                if self._ignored(relative_path) or self._ignored(context_relative_path):
                    continue
                self._add_file_spec(
                    file_path,
                    _posix_join(dest, relative_path),
                    context_relative_path,
                    options,
                )
            return

        if not source_path.is_file():
            raise InvalidArgumentException(f"copy source is not a file or directory: {source}")

        dest_path = _posix_join(dest, source_path.name) if multiple_sources or dest.endswith("/") else dest
        self._add_file_spec(
            source_path,
            dest_path,
            _to_posix(source_path.relative_to(self._context_path())),
            options,
        )

    def _add_file_spec(
        self,
        source_path: Path,
        dest_path: str,
        context_relative_path: str,
        options: Dict[str, Any],
    ) -> None:
        file_spec: Dict[str, Any] = {
            "path": _normalize_sandbox_path(dest_path),
            "source_path": context_relative_path,
            "content_b64": base64.b64encode(source_path.read_bytes()).decode("ascii"),
        }
        mode = options.get("mode")
        if mode is not None:
            file_spec["mode"] = mode
        user = options.get("user")
        if user is not None:
            file_spec["user"] = user
        self._files.append(file_spec)

    def _context_path(self) -> Path:
        return Path(self._file_context_path or os.getcwd()).resolve()

    def _resolve_context_path(self, source: str) -> Path:
        source_path = Path(source)
        if source_path.is_absolute():
            raise InvalidArgumentException(
                "copy source must be relative to the template file context"
            )
        context_path = self._context_path()
        resolved = (context_path / source_path).resolve()
        try:
            resolved.relative_to(context_path)
        except ValueError:
            raise InvalidArgumentException(
                "copy source must stay inside the template file context"
            )
        return resolved

    def _ignored(self, relative_path: str) -> bool:
        return any(_matches_ignore_pattern(relative_path, pattern) for pattern in self._file_ignore_patterns)

    def _command_with_context(self, command: str, user: Optional[str]) -> str:
        command_text = f"cd {shlex.quote(self._current_workdir)} && {command}" if self._current_workdir else command
        command_user = user or self._current_user
        if command_user and command_user != "root":
            return f"su -s /bin/bash -c {shlex.quote(command_text)} {shlex.quote(command_user)}"
        return command_text


class Template(TemplateBase):
    """Synchronous template builder and build helper."""

    @staticmethod
    def build(
        template: "TemplateBase",
        name: Optional[str] = None,
        *,
        alias: Optional[str] = None,
        tags: Optional[List[str]] = None,
        cpu_count: int = 2,
        memory_mb: int = 1024,
        skip_cache: bool = False,
        on_build_logs: Optional[Callable[[LogEntry], None]] = None,
        **opts: ApiParams,
    ) -> BuildInfo:
        """Build a Watasu template and wait until the build finishes."""
        if on_build_logs:
            on_build_logs(LogEntryStart())
        try:
            build_info = Template.build_in_background(
                template,
                name,
                alias=alias,
                tags=tags,
                cpu_count=cpu_count,
                memory_mb=memory_mb,
                skip_cache=skip_cache,
                on_build_logs=on_build_logs,
                **opts,
            )
            _wait_for_build_finish(build_info, on_build_logs=on_build_logs, **opts)
            return build_info
        finally:
            if on_build_logs:
                on_build_logs(LogEntryEnd())

    @staticmethod
    def build_in_background(
        template: "TemplateBase",
        name: Optional[str] = None,
        *,
        alias: Optional[str] = None,
        tags: Optional[List[str]] = None,
        cpu_count: int = 2,
        memory_mb: int = 1024,
        skip_cache: bool = False,
        on_build_logs: Optional[Callable[[LogEntry], None]] = None,
        **opts: ApiParams,
    ) -> BuildInfo:
        """Start a Watasu template build and return its identifiers."""
        resolved_name = _normalize_template_build_name(name, alias)
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        payload: Dict[str, Any] = {
            "name": resolved_name,
            "cpu_count": cpu_count,
            "memory_mb": memory_mb,
            "skip_cache": skip_cache or template._force,
            "build_spec": template.to_build_spec(),
        }
        if tags is not None:
            payload["tags"] = tags
        if "team" in opts:
            payload["team"] = opts["team"]
        response = control.post("/templates", json=payload)
        return _build_info(response.get("template_build") or response)

    @staticmethod
    def get_build_status(
        build_info: BuildInfo,
        logs_offset: int = 0,
        **opts: ApiParams,
    ) -> TemplateBuildStatusResponse:
        """Return the current status and new logs for a template build."""
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        response = control.get(
            f"/templates/{build_info.template_id}/builds/{build_info.build_id}/status",
            params={"logs_offset": logs_offset},
        )
        return _template_build_status(response)

    @staticmethod
    def exists(name: str, **opts: ApiParams) -> bool:
        """Return whether a template name exists and is accessible."""
        return Template.alias_exists(name, **opts)

    @staticmethod
    def alias_exists(alias: str, **opts: ApiParams) -> bool:
        """Return whether a template alias exists and is accessible."""
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        try:
            control.get(f"/templates/aliases/{alias}")
            return True
        except NotFoundException:
            return False

    @staticmethod
    def assign_tags(
        target_name: str,
        tags: Union[str, List[str]],
        **opts: ApiParams,
    ) -> TemplateTagInfo:
        """Assign tags to an existing template build."""
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        response = control.post(
            "/templates/tags",
            json={"target": target_name, "tags": _string_list(tags)},
        )
        return TemplateTagInfo(
            build_id=str(response.get("build_id") or ""),
            tags=_string_list(response.get("tags")),
        )

    @staticmethod
    def remove_tags(
        name: str,
        tags: Union[str, List[str]],
        **opts: ApiParams,
    ) -> None:
        """Remove tags from a template."""
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        control.delete("/templates/tags", json={"name": name, "tags": _string_list(tags)})

    @staticmethod
    def get_tags(template_id: str, **opts: ApiParams) -> List[TemplateTag]:
        """Return all tags assigned to builds for a template."""
        config = ConnectionConfig(**opts)
        control = ControlClient(config)
        response = control.get(f"/templates/{template_id}/tags")
        return [_template_tag(item) for item in response if isinstance(item, dict)]


class AsyncTemplate(TemplateBase):
    """Async wrapper around the Watasu template builder helpers."""

    @staticmethod
    async def build(
        template: "TemplateBase",
        name: Optional[str] = None,
        *,
        alias: Optional[str] = None,
        tags: Optional[List[str]] = None,
        cpu_count: int = 2,
        memory_mb: int = 1024,
        skip_cache: bool = False,
        on_build_logs: Optional[Callable[[LogEntry], None]] = None,
        **opts: ApiParams,
    ) -> BuildInfo:
        return await asyncio.to_thread(
            Template.build,
            template,
            name,
            alias=alias,
            tags=tags,
            cpu_count=cpu_count,
            memory_mb=memory_mb,
            skip_cache=skip_cache,
            on_build_logs=on_build_logs,
            **opts,
        )

    @staticmethod
    async def build_in_background(
        template: "TemplateBase",
        name: Optional[str] = None,
        *,
        alias: Optional[str] = None,
        tags: Optional[List[str]] = None,
        cpu_count: int = 2,
        memory_mb: int = 1024,
        skip_cache: bool = False,
        on_build_logs: Optional[Callable[[LogEntry], None]] = None,
        **opts: ApiParams,
    ) -> BuildInfo:
        return await asyncio.to_thread(
            Template.build_in_background,
            template,
            name,
            alias=alias,
            tags=tags,
            cpu_count=cpu_count,
            memory_mb=memory_mb,
            skip_cache=skip_cache,
            on_build_logs=on_build_logs,
            **opts,
        )

    @staticmethod
    async def get_build_status(
        build_info: BuildInfo,
        logs_offset: int = 0,
        **opts: ApiParams,
    ) -> TemplateBuildStatusResponse:
        return await asyncio.to_thread(
            Template.get_build_status,
            build_info,
            logs_offset=logs_offset,
            **opts,
        )

    @staticmethod
    async def exists(name: str, **opts: ApiParams) -> bool:
        return await asyncio.to_thread(Template.exists, name, **opts)

    @staticmethod
    async def alias_exists(alias: str, **opts: ApiParams) -> bool:
        return await asyncio.to_thread(Template.alias_exists, alias, **opts)

    @staticmethod
    async def assign_tags(
        target_name: str,
        tags: Union[str, List[str]],
        **opts: ApiParams,
    ) -> TemplateTagInfo:
        return await asyncio.to_thread(Template.assign_tags, target_name, tags, **opts)

    @staticmethod
    async def remove_tags(
        name: str,
        tags: Union[str, List[str]],
        **opts: ApiParams,
    ) -> None:
        await asyncio.to_thread(Template.remove_tags, name, tags, **opts)

    @staticmethod
    async def get_tags(template_id: str, **opts: ApiParams) -> List[TemplateTag]:
        return await asyncio.to_thread(Template.get_tags, template_id, **opts)


TemplateClass = type
TemplateTagInfoType = TemplateTagInfo
TemplateTagType = TemplateTag


def wait_for_file(path: str) -> ReadyCmd:
    """Return a ready check that waits for a file to exist."""
    return ReadyCmd(f"test -e {shlex.quote(path)}")


def wait_for_port(port: int, host: str = "127.0.0.1") -> ReadyCmd:
    """Return a ready check that waits for a TCP port to accept connections."""
    return ReadyCmd(
        f"python3 - <<'PY'\nimport socket\ns=socket.create_connection(({host!r}, {int(port)}), 5)\ns.close()\nPY"
    )


def wait_for_process(process_name: str) -> ReadyCmd:
    """Return a ready check that waits for a process name."""
    return ReadyCmd(f"pgrep -f {shlex.quote(process_name)} >/dev/null")


def wait_for_timeout(timeout: Union[int, float]) -> ReadyCmd:
    """Return a ready check that waits for a fixed duration in milliseconds."""
    seconds = max(1, int(float(timeout) // 1000))
    return ReadyCmd(f"sleep {seconds}")


def wait_for_url(url: str, status_code: int = 200) -> ReadyCmd:
    """Return a ready check that waits for a URL to return a status code."""
    return ReadyCmd(
        f"python3 - <<'PY'\nimport urllib.request\nr=urllib.request.urlopen({url!r}, timeout=5)\nassert r.status == {int(status_code)}\nPY"
    )


def default_build_logger(
    min_level: Optional[LogEntryLevel] = None,
) -> Callable[[LogEntry], None]:
    """Return a logger function for template build log entries."""

    order = {"debug": 0, "info": 1, "warn": 2, "error": 3}
    min_level = min_level or "info"

    def logger(entry: LogEntry) -> None:
        if order.get(entry.level, order["info"]) < order.get(min_level, order["info"]):
            return
        print(str(entry))

    return logger


def _wait_for_build_finish(
    build_info: BuildInfo,
    on_build_logs: Optional[Callable[[LogEntry], None]] = None,
    **opts: ApiParams,
) -> None:
    logs_offset = 0
    status = TemplateBuildStatus.BUILDING
    while status in {TemplateBuildStatus.BUILDING, TemplateBuildStatus.WAITING}:
        build_status = Template.get_build_status(build_info, logs_offset=logs_offset, **opts)
        logs_offset += len(build_status.log_entries)
        for entry in build_status.log_entries:
            if on_build_logs:
                on_build_logs(entry)
        status = build_status.status
        if status == TemplateBuildStatus.READY:
            return
        if status == TemplateBuildStatus.ERROR:
            message = build_status.reason.message if build_status.reason else "Template build failed"
            raise BuildException(message)
        time.sleep(TemplateBase._logs_refresh_frequency)


def _normalize_template_build_name(name: Optional[str], alias: Optional[str]) -> str:
    selected = name if name is not None else alias
    if selected is None or not str(selected).strip():
        raise InvalidArgumentException("template build name is required")
    return str(selected)


def _build_info(payload: Dict[str, Any]) -> BuildInfo:
    template_id = payload.get("template_id") or payload.get("templateId")
    build_id = payload.get("build_id") or payload.get("buildId")
    if template_id is None or build_id is None:
        raise BuildException("template build response did not include identifiers")
    return BuildInfo(
        template_id=str(template_id),
        build_id=str(build_id),
        name=str(payload.get("name") or payload.get("alias") or ""),
        alias=str(payload.get("alias") or payload.get("name") or ""),
        tags=_string_list(payload.get("tags")),
    )


def _template_build_status(payload: Dict[str, Any]) -> TemplateBuildStatusResponse:
    return TemplateBuildStatusResponse(
        build_id=str(payload.get("build_id") or payload.get("buildID") or ""),
        template_id=str(payload.get("template_id") or payload.get("templateID") or ""),
        status=TemplateBuildStatus(str(payload.get("status") or "building")),
        log_entries=[_log_entry(item) for item in payload.get("log_entries", [])],
        logs=_string_list(payload.get("logs")),
        reason=_build_status_reason(payload.get("reason")),
    )


def _build_status_reason(payload: Any) -> Optional[BuildStatusReason]:
    if not isinstance(payload, dict):
        return None
    return BuildStatusReason(
        message=str(payload.get("message") or "Template build failed"),
        step=payload.get("step"),
        log_entries=[_log_entry(item) for item in payload.get("log_entries", [])],
    )


def _log_entry(payload: Dict[str, Any]) -> LogEntry:
    timestamp = payload.get("timestamp")
    return LogEntry(
        timestamp=_parse_datetime(timestamp),
        level=str(payload.get("level") or "info"),
        message=str(payload.get("message") or ""),
    )


def _template_tag(payload: Dict[str, Any]) -> TemplateTag:
    return TemplateTag(
        tag=str(payload.get("tag") or ""),
        build_id=str(payload.get("build_id") or payload.get("buildId") or ""),
        created_at=_parse_datetime(payload.get("created_at") or payload.get("createdAt"))
        or datetime.fromtimestamp(0),
    )


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _walk_files(root: Path, resolve_symlinks: bool) -> List[Path]:
    files: List[Path] = []
    for child in sorted(root.iterdir(), key=lambda item: str(item)):
        stat_path = child.resolve() if resolve_symlinks else child
        if stat_path.is_dir():
            files.extend(_walk_files(stat_path, resolve_symlinks))
        elif stat_path.is_file():
            files.append(stat_path)
    return files


def _normalize_sandbox_path(value: str) -> str:
    return "/".join(str(value).replace("\\", "/").split("/"))


def _posix_join(base: str, relative_path: str) -> str:
    return _normalize_sandbox_path(os.path.join(base.replace("\\", "/"), relative_path))


def _to_posix(path_value: Union[str, Path]) -> str:
    return str(path_value).replace(os.sep, "/")


def _matches_ignore_pattern(relative_path: str, pattern: str) -> bool:
    if not pattern:
        return False
    pattern = pattern.replace("\\", "/")
    if pattern.endswith("/"):
        return relative_path.startswith(pattern)
    if "*" not in pattern:
        return relative_path == pattern or relative_path.startswith(f"{pattern}/")

    import re

    regex = "^" + ".*".join(re.escape(part) for part in pattern.split("*")) + "$"
    return re.match(regex, relative_path) is not None


def _parse_dockerfile_into_template(
    dockerfile_content_or_path: str, template: TemplateBase
) -> None:
    raw_path = Path(dockerfile_content_or_path)
    path = raw_path if raw_path.is_absolute() else template._context_path() / raw_path
    content = path.read_text(encoding="utf-8") if path.is_file() else dockerfile_content_or_path

    for keyword, value in _dockerfile_instructions(content):
        upper = keyword.upper()
        if upper == "FROM":
            template.from_image(value.split()[0] if value.split() else "base")
        elif upper == "RUN":
            template.run_cmd(value)
        elif upper == "WORKDIR":
            template.set_workdir(value)
        elif upper == "USER":
            template.set_user(value)
        elif upper == "ENV":
            template.set_envs(_parse_env_instruction(value))
        elif upper in {"COPY", "ADD"}:
            words = [word for word in shlex.split(value) if not word.startswith("--")]
            if len(words) < 2:
                raise InvalidArgumentException(f"{upper} requires source and destination")
            template.copy(words[:-1], words[-1])
        elif upper in {"CMD", "ENTRYPOINT"}:
            template.set_start_cmd(value, wait_for_timeout(20_000))


def _dockerfile_instructions(content: str) -> List[tuple[str, str]]:
    logical_lines: List[str] = []
    current = ""
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            current += f"{line[:-1]} "
            continue
        logical_lines.append(f"{current}{line}")
        current = ""
    if current.strip():
        logical_lines.append(current.strip())

    instructions: List[tuple[str, str]] = []
    for line in logical_lines:
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            instructions.append((parts[0], parts[1].strip()))
    return instructions


def _parse_env_instruction(value: str) -> Dict[str, str]:
    words = shlex.split(value)
    if len(words) == 2 and "=" not in words[0]:
        return {words[0]: words[1]}
    env: Dict[str, str] = {}
    for word in words:
        if "=" in word:
            key, item_value = word.split("=", 1)
            if key:
                env[key] = item_value
    return env
