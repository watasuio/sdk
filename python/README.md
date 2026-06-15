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
from watasu import Sandbox

with Sandbox.create() as sbx:
    result = sbx.commands.run("echo hello")
    print(result.stdout)
```

Leaving the context manager calls `kill()`.

## Git, Watch, PTY, And Signed File URLs

```python
from watasu import PtySize, Sandbox

with Sandbox.create() as sbx:
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

    watcher = sbx.files.watch_dir("/workspace/project", recursive=True)

    terminal = sbx.pty.create(PtySize(rows=30, cols=100))
    terminal.send_stdin("echo hello\n")
    result = terminal.wait()

    upload_url = sbx.upload_url("/workspace/input.bin")
    download_url = sbx.download_url("/workspace/output.bin")

    events = watcher.get_new_events()
    watcher.stop()
```

## Metrics And Snapshots

```python
from watasu import Sandbox

with Sandbox.create() as sbx:
    metrics = sbx.get_metrics()
    snapshot = sbx.create_snapshot(name="ready")
    snapshots = sbx.list_snapshots().list_items()
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
        await sbx.delete_snapshot(snapshot.snapshot_id)
```

Unsupported surfaces raise explicit not-implemented errors instead of silently
falling back to client-side polling.
