# Watasu Python SDK

Python SDK for Watasu.

## Install

```bash
pip install watasu
```

Set `WATASU_API_KEY` before using the SDK.

## Usage

```python
from watasu import Sandbox

sbx = Sandbox()
sbx.files.write("/home/user/a.py", "print(2 + 2)")
result = sbx.commands.run("python /home/user/a.py")
print(result.stdout)
sbx.kill()
```

`Sandbox()`, `Sandbox.create`, and `Sandbox.connect` return only after the Watasu
API supplies a usable data-plane session. The SDK does not poll sandbox
readiness.

```python
sbx.beta_pause()
sbx.resume(timeout=300)
```

```python
from watasu import Sandbox

with Sandbox.create() as sbx:
    result = sbx.commands.run("echo hello")
    print(result.stdout)
```

Leaving the context manager calls `kill()`.

## Code Interpreter

```python
from watasu_code_interpreter import Sandbox

with Sandbox.create() as sbx:
    execution = sbx.run_code(
        "print('hello')\n2 + 3",
        on_stdout=lambda message: print(message.line),
    )
    print(execution.text)
```

`watasu_code_interpreter.Sandbox` starts the `code-interpreter` template by
default and returns structured `results`, `logs`, and `error` fields for each
execution.

## MCP Gateway

```python
import os

from watasu import Sandbox

with Sandbox.create(
    mcp={
        "github": {
            "command": "github-mcp-server",
            "args": ["stdio"],
            "env": {"GITHUB_TOKEN": os.environ["GITHUB_TOKEN"]},
        }
    }
) as sbx:
    print(sbx.get_mcp_url())
    print(sbx.get_mcp_token())
```

## Listing Sandboxes

```python
from watasu import Sandbox

paginator = Sandbox.list(
    query={"metadata": {"purpose": "ci"}, "state": ["running"]},
    limit=20,
)

for sandbox in paginator.list_items():
    print(sandbox.sandbox_id, sandbox.state)
```

## Git, Watch, PTY, And Signed File URLs

```python
from watasu import PtySize, Sandbox

with Sandbox.create() as sbx:
    sbx.git.init("/workspace/new-project", initial_branch="main")
    sbx.git.clone(
        "https://github.com/acme/project.git",
        path="/workspace/project",
        branch="main",
        depth=1,
    )
    status = sbx.git.status("/workspace/project")
    sbx.git.configure_user(
        "Watasu Bot",
        "bot@watasu.local",
        scope="local",
        path="/workspace/project",
    )
    sbx.git.create_branch("/workspace/project", "feature/docs")
    sbx.git.add("/workspace/project", files=["README.md"])
    sbx.git.commit(
        "/workspace/project",
        "Update docs",
        author_name="Watasu Bot",
        author_email="bot@watasu.local",
    )
    sbx.git.push(
        "/workspace/project",
        remote="origin",
        branch="feature/docs",
        set_upstream=True,
    )
    remote_url = sbx.git.remote_get("/workspace/project", "origin")
    sbx.git.restore("/workspace/project", paths=["README.md"])
    sbx.git.reset("/workspace/project", mode="hard", target="HEAD")

    watcher = sbx.files.watch_dir("/workspace/project", recursive=True)
    sbx.files.write_files(
        [
            {"path": "/workspace/project/a.txt", "data": "alpha"},
            {"path": "/workspace/project/b.bin", "data": b"\x00\x01\x02"},
        ]
    )

    terminal = sbx.pty.create(PtySize(rows=30, cols=100))
    terminal.send_stdin("echo hello\n")
    result = terminal.wait()

    upload_url = sbx.upload_url("/workspace/input.bin")
    download_url = sbx.download_url("/workspace/output.bin")

    events = watcher.get_new_events()
    watcher.stop()
```

## Network Policy

```python
with Sandbox.create(
    network={
        "allow_out": ["pypi.org:443"],
        "deny_out": ["169.254.169.254"],
    }
) as sbx:
    sbx.update_network(
        allow_internet_access=False,
        allow_package_registry_access=True,
        allow_out=["pypi.org:443", "registry.npmjs.org:443"],
    )
```

## Template Builds

```python
from watasu import Template

template = (
    Template()
    .from_python_image("3.12")
    .copy("requirements.txt", "/workspace/requirements.txt")
    .apt_install(["git"])
    .pip_install(["pytest"])
    .set_envs({"PIP_DISABLE_PIP_VERSION_CHECK": "1"})
    .run_cmd("echo ready")
)

build = Template.build_in_background(
    template,
    "python-ci:stable",
    tags=["stable"],
    cpu_count=2,
    memory_mb=2048,
)
status = Template.get_build_status(build)

Template.assign_tags("python-ci:stable", ["prod"])
print(Template.exists("python-ci"))
```

The same builder classes are also available from the sync and async template
namespaces:

```python
from watasu.template_sync import Template
from watasu.template_async import AsyncTemplate
```

Template names resolve server-side. `python-ci` starts the latest ready build;
`python-ci:stable` starts the tagged build.

`Template(file_context_path=".").from_dockerfile("Dockerfile")` parses common
`FROM`, `WORKDIR`, `COPY`, `RUN`, `ENV`, `CMD`, and `ENTRYPOINT` instructions
into Watasu's package-spec builder.

## Metrics And Snapshots

```python
from watasu import Sandbox

with Sandbox.create() as sbx:
    metrics = sbx.get_metrics()
    snapshot = sbx.create_snapshot(name="ready")
    snapshots = sbx.list_snapshots().list_items()
    all_snapshots = Sandbox.list_snapshots(limit=100).next_items()
    restored = sbx.restore(snapshot_id=snapshot.snapshot_id)
    sbx.delete_snapshot(snapshot.snapshot_id)
```

Watasu snapshots are backed by sandbox checkpoints. Use the returned
`snapshot_id` when restoring from a checkpoint.

## Async API

```python
from watasu import AsyncSandbox


async def main() -> None:
    async with await AsyncSandbox.create() as sbx:
        result = await sbx.commands.run("echo hello")
        print(result.stdout)

        metrics = await sbx.get_metrics()
        snapshot = await sbx.create_snapshot(name="ready")
        snapshots = await sbx.list_snapshots().list_items()
        all_snapshots = await AsyncSandbox.list_snapshots(limit=100).list_items()
        await sbx.delete_snapshot(snapshot.snapshot_id)
```

Unsupported surfaces raise explicit not-implemented errors instead of silently
falling back to client-side polling.
