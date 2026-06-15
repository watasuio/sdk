from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from watasu._transport.data_plane import DataPlaneClient


@dataclass
class GitCommandResult:
    """Result returned by a sandbox Git command."""

    stdout: str
    stderr: str
    path: Optional[str] = None
    url: Optional[str] = None
    ref: Optional[str] = None
    branch: Optional[str] = None
    remote: Optional[str] = None
    name: Optional[str] = None
    value: Optional[str] = None
    branches: List[str] = field(default_factory=list)
    current_branch: Optional[str] = None
    command: Optional[Dict] = None
    raw: Dict = field(default_factory=dict)


@dataclass
class GitFileStatus:
    """Parsed status entry for a repository file."""

    name: str
    status: str
    index_status: str
    working_tree_status: str
    staged: bool
    renamed_from: Optional[str] = None


@dataclass
class GitStatus:
    """Parsed repository status."""

    current_branch: Optional[str]
    upstream: Optional[str]
    ahead: int
    behind: int
    detached: bool
    file_status: List[GitFileStatus]
    result: GitCommandResult

    @property
    def is_clean(self) -> bool:
        return len(self.file_status) == 0

    @property
    def has_changes(self) -> bool:
        return len(self.file_status) > 0

    @property
    def has_staged(self) -> bool:
        return any(item.staged for item in self.file_status)

    @property
    def has_untracked(self) -> bool:
        return any(item.status == "untracked" for item in self.file_status)

    @property
    def has_conflicts(self) -> bool:
        return any(item.status == "conflict" for item in self.file_status)

    @property
    def total_count(self) -> int:
        return len(self.file_status)

    @property
    def staged_count(self) -> int:
        return sum(1 for item in self.file_status if item.staged)

    @property
    def unstaged_count(self) -> int:
        return self.total_count - self.staged_count

    @property
    def untracked_count(self) -> int:
        return sum(1 for item in self.file_status if item.status == "untracked")

    @property
    def conflict_count(self) -> int:
        return sum(1 for item in self.file_status if item.status == "conflict")


@dataclass
class GitBranches:
    """Git branch list."""

    branches: List[str]
    current_branch: Optional[str]
    result: GitCommandResult
    path: Optional[str] = None


class Git:
    """Git helper backed by sandbox data-plane routes."""

    def __init__(self, data_plane: DataPlaneClient) -> None:
        self._data_plane = data_plane

    def clone(
        self,
        url: str,
        path: Optional[str] = None,
        branch: Optional[str] = None,
        depth: Optional[int] = None,
        recursive: bool = False,
        submodules: bool = False,
        username: Optional[str] = None,
        password: Optional[str] = None,
        dangerously_store_credentials: bool = False,
        envs: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> GitCommandResult:
        """Clone a Git repository into the sandbox."""
        return self._run(
            "/runtime/v1/git/clone",
            {
                "url": url,
                "path": path,
                "branch": branch,
                "depth": depth,
                "recursive": recursive if recursive else None,
                "submodules": submodules if submodules else None,
                "username": username,
                "password": password,
                "dangerously_store_credentials": dangerously_store_credentials
                if dangerously_store_credentials
                else None,
                "env_vars": envs,
                "timeout_seconds": _timeout_seconds(timeout),
            },
            request_timeout,
        )

    def dangerously_authenticate(
        self,
        username: str,
        password: str,
        host: Optional[str] = None,
        protocol: Optional[str] = None,
        envs: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> GitCommandResult:
        """Store Git credentials in the sandbox credential helper."""
        return self._run(
            "/runtime/v1/git/dangerously_authenticate",
            {
                "username": username,
                "password": password,
                "host": host,
                "protocol": protocol,
                "env_vars": envs,
                "timeout_seconds": _timeout_seconds(timeout),
            },
            request_timeout,
        )

    def configure_user(
        self,
        name: str,
        email: str,
        scope: Optional[str] = None,
        path: Optional[str] = None,
        envs: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> GitCommandResult:
        """Configure Git author identity globally or for one repository."""
        return self._run(
            "/runtime/v1/git/configure_user",
            {
                "name": name,
                "email": email,
                "scope": scope,
                "path": path,
                "env_vars": envs,
                "timeout_seconds": _timeout_seconds(timeout),
            },
            request_timeout,
        )

    def init(
        self,
        path: str,
        bare: bool = False,
        initial_branch: Optional[str] = None,
        envs: Optional[Dict[str, str]] = None,
        user: Optional[str] = None,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> GitCommandResult:
        """Initialize a Git repository."""
        return self._run(
            "/runtime/v1/git/init",
            {
                "path": path,
                "bare": bare if bare else None,
                "initial_branch": initial_branch,
                "env_vars": envs,
                "user": user,
                "cwd": cwd,
                "timeout_seconds": _timeout_seconds(timeout),
            },
            request_timeout,
        )

    def status(
        self,
        path: str,
        envs: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> GitStatus:
        """Return parsed repository status for ``path``."""
        result = self._run(
            "/runtime/v1/git/status",
            {"path": path, "env_vars": envs, "timeout_seconds": _timeout_seconds(timeout)},
            request_timeout,
        )
        return _parse_status(result)

    def branches(
        self,
        path: str,
        envs: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> GitBranches:
        """Return branches and the current branch for ``path``."""
        result = self._run(
            "/runtime/v1/git/branches",
            {"path": path, "env_vars": envs, "timeout_seconds": _timeout_seconds(timeout)},
            request_timeout,
        )
        return GitBranches(
            branches=list(result.branches),
            current_branch=result.current_branch,
            path=result.path,
            result=result,
        )

    def create_branch(
        self,
        path: str,
        branch: str,
        envs: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> GitCommandResult:
        """Create and check out a new branch."""
        return self._run(
            "/runtime/v1/git/create_branch",
            {"path": path, "branch": branch, "env_vars": envs, "timeout_seconds": _timeout_seconds(timeout)},
            request_timeout,
        )

    def delete_branch(
        self,
        path: str,
        branch: str,
        force: bool = False,
        envs: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> GitCommandResult:
        """Delete a branch."""
        return self._run(
            "/runtime/v1/git/delete_branch",
            {
                "path": path,
                "branch": branch,
                "force": force if force else None,
                "env_vars": envs,
                "timeout_seconds": _timeout_seconds(timeout),
            },
            request_timeout,
        )

    def add(
        self,
        path: str,
        files: Optional[List[str]] = None,
        envs: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> GitCommandResult:
        """Stage files. Defaults to all files."""
        return self._run(
            "/runtime/v1/git/add",
            {"path": path, "files": files, "env_vars": envs, "timeout_seconds": _timeout_seconds(timeout)},
            request_timeout,
        )

    def commit(
        self,
        path: str,
        message: str,
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
        allow_empty: bool = False,
        envs: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> GitCommandResult:
        """Commit staged files."""
        return self._run(
            "/runtime/v1/git/commit",
            {
                "path": path,
                "message": message,
                "author_name": author_name,
                "author_email": author_email,
                "allow_empty": allow_empty if allow_empty else None,
                "env_vars": envs,
                "timeout_seconds": _timeout_seconds(timeout),
            },
            request_timeout,
        )

    def reset(
        self,
        path: str,
        mode: Optional[Literal["soft", "mixed", "hard", "merge", "keep"]] = None,
        target: Optional[str] = None,
        paths: Optional[List[str]] = None,
        envs: Optional[Dict[str, str]] = None,
        user: Optional[str] = None,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> GitCommandResult:
        """Reset the current HEAD to a specified state."""
        return self._run(
            "/runtime/v1/git/reset",
            {
                "path": path,
                "mode": mode,
                "target": target,
                "paths": paths,
                "env_vars": envs,
                "user": user,
                "cwd": cwd,
                "timeout_seconds": _timeout_seconds(timeout),
            },
            request_timeout,
        )

    def restore(
        self,
        path: str,
        paths: List[str],
        staged: Optional[bool] = None,
        worktree: Optional[bool] = None,
        source: Optional[str] = None,
        envs: Optional[Dict[str, str]] = None,
        user: Optional[str] = None,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> GitCommandResult:
        """Restore working tree files or unstage changes."""
        return self._run(
            "/runtime/v1/git/restore",
            {
                "path": path,
                "paths": paths,
                "staged": staged,
                "worktree": worktree,
                "source": source,
                "env_vars": envs,
                "user": user,
                "cwd": cwd,
                "timeout_seconds": _timeout_seconds(timeout),
            },
            request_timeout,
        )

    def pull(
        self,
        path: str,
        branch: Optional[str] = None,
        remote: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        envs: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> GitCommandResult:
        """Pull the current branch with a fast-forward-only merge."""
        return self._run(
            "/runtime/v1/git/pull",
            {
                "path": path,
                "branch": branch,
                "remote": remote,
                "username": username,
                "password": password,
                "env_vars": envs,
                "timeout_seconds": _timeout_seconds(timeout),
            },
            request_timeout,
        )

    def push(
        self,
        path: str,
        branch: Optional[str] = None,
        remote: Optional[str] = None,
        set_upstream: bool = False,
        username: Optional[str] = None,
        password: Optional[str] = None,
        envs: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> GitCommandResult:
        """Push the current branch or a selected branch."""
        return self._run(
            "/runtime/v1/git/push",
            {
                "path": path,
                "branch": branch,
                "remote": remote,
                "set_upstream": set_upstream if set_upstream else None,
                "username": username,
                "password": password,
                "env_vars": envs,
                "timeout_seconds": _timeout_seconds(timeout),
            },
            request_timeout,
        )

    def checkout(
        self,
        path: str,
        ref: str,
        envs: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> GitCommandResult:
        """Check out an arbitrary ref in a repository."""
        return self._run(
            "/runtime/v1/git/checkout",
            {"path": path, "ref": ref, "env_vars": envs, "timeout_seconds": _timeout_seconds(timeout)},
            request_timeout,
        )

    def checkout_branch(self, path: str, branch: str, **kwargs) -> GitCommandResult:
        """Check out an existing branch in a repository."""
        return self.checkout(path, branch, **kwargs)

    def remote_add(
        self,
        path: str,
        name: str,
        url: str,
        fetch: bool = False,
        overwrite: bool = False,
        envs: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> GitCommandResult:
        """Add a remote."""
        return self._run(
            "/runtime/v1/git/remote_add",
            {
                "path": path,
                "name": name,
                "url": url,
                "fetch": fetch if fetch else None,
                "overwrite": overwrite if overwrite else None,
                "env_vars": envs,
                "timeout_seconds": _timeout_seconds(timeout),
            },
            request_timeout,
        )

    def remote_get(
        self,
        path: str,
        name: str,
        envs: Optional[Dict[str, str]] = None,
        user: Optional[str] = None,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> Optional[str]:
        """Return a remote URL, or ``None`` when the remote does not exist."""
        result = self._run(
            "/runtime/v1/git/remote_get",
            {
                "path": path,
                "name": name,
                "env_vars": envs,
                "user": user,
                "cwd": cwd,
                "timeout_seconds": _timeout_seconds(timeout),
            },
            request_timeout,
        )
        return result.value or result.url

    def set_config(
        self,
        key: str,
        value: str,
        scope: Optional[str] = None,
        path: Optional[str] = None,
        envs: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> GitCommandResult:
        """Set a Git config value."""
        return self._run(
            "/runtime/v1/git/set_config",
            {
                "key": key,
                "value": value,
                "scope": scope,
                "path": path,
                "env_vars": envs,
                "timeout_seconds": _timeout_seconds(timeout),
            },
            request_timeout,
        )

    def get_config(
        self,
        key: str,
        scope: Optional[str] = None,
        path: Optional[str] = None,
        envs: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        request_timeout: Optional[float] = None,
    ) -> str:
        """Read a Git config value."""
        result = self._run(
            "/runtime/v1/git/get_config",
            {
                "key": key,
                "scope": scope,
                "path": path,
                "env_vars": envs,
                "timeout_seconds": _timeout_seconds(timeout),
            },
            request_timeout,
        )
        return str(result.value or "")

    def _run(
        self, path: str, payload: Dict, request_timeout: Optional[float]
    ) -> GitCommandResult:
        response = self._data_plane.post_json(
            path,
            json={key: value for key, value in payload.items() if value is not None},
            request_timeout=request_timeout,
        )
        return _result_from_api(response.get("git") or response)


def _result_from_api(payload: Dict) -> GitCommandResult:
    return GitCommandResult(
        stdout=str(payload.get("stdout") or ""),
        stderr=str(payload.get("stderr") or ""),
        path=payload.get("path"),
        url=payload.get("url"),
        ref=payload.get("ref"),
        branch=payload.get("branch"),
        remote=payload.get("remote"),
        name=payload.get("name"),
        value=payload.get("value"),
        branches=[str(item) for item in payload.get("branches") or []],
        current_branch=payload.get("current_branch"),
        command=payload.get("command"),
        raw=payload,
    )


def _timeout_seconds(value: Optional[float]) -> Optional[int]:
    if value is None:
        return None
    return int(value)


def _parse_status(result: GitCommandResult) -> GitStatus:
    current_branch = None
    upstream = None
    ahead = 0
    behind = 0
    detached = False
    file_status: List[GitFileStatus] = []

    for line in [line for line in result.stdout.splitlines() if line]:
        if line.startswith("## "):
            branch_line = line[3:]
            detached = "HEAD" in branch_line and "no branch" in branch_line
            if "..." in branch_line:
                branch_part, tracking_part = branch_line.split("...", 1)
                current_branch = branch_part.split(" [", 1)[0] or None
                upstream = tracking_part.split(" ", 1)[0] or None
                ahead = _number_after(tracking_part, "ahead")
                behind = _number_after(tracking_part, "behind")
            else:
                current_branch = branch_line.split(" [", 1)[0] or None
            continue

        index_status = line[0] if len(line) > 0 else " "
        working_tree_status = line[1] if len(line) > 1 else " "
        name = line[3:]
        renamed_from = None
        if " -> " in name:
            renamed_from, name = name.split(" -> ", 1)
        status = _status_name(index_status, working_tree_status)
        file_status.append(
            GitFileStatus(
                name=name,
                status=status,
                index_status=index_status,
                working_tree_status=working_tree_status,
                staged=index_status not in {" ", "?"},
                renamed_from=renamed_from,
            )
        )

    return GitStatus(
        current_branch=current_branch,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        detached=detached,
        file_status=file_status,
        result=result,
    )


def _number_after(value: str, label: str) -> int:
    marker = f"{label} "
    if marker not in value:
        return 0
    try:
        return int(value.split(marker, 1)[1].split(",", 1)[0].split("]", 1)[0])
    except ValueError:
        return 0


def _status_name(index_status: str, working_tree_status: str) -> str:
    if index_status == "?" and working_tree_status == "?":
        return "untracked"
    if index_status == "U" or working_tree_status == "U" or (
        index_status == "A" and working_tree_status == "A"
    ):
        return "conflict"
    if index_status == "D" or working_tree_status == "D":
        return "deleted"
    if index_status == "R":
        return "renamed"
    if index_status == "A":
        return "added"
    if index_status == "M" or working_tree_status == "M":
        return "modified"
    return "changed"
