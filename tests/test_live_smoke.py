from __future__ import annotations

import asyncio
import os
import time
import urllib.request

import pytest

from watasu import (
    AsyncSandbox,
    AsyncTemplate,
    AsyncVolume,
    CommandExitException,
    ConnectionConfig,
    Sandbox,
    Template,
    Volume,
    wait_for_file,
    wait_for_port,
    wait_for_process,
    wait_for_timeout,
    wait_for_url,
)
from watasu.sandbox.commands.command_handle import PtySize
from watasu_code_interpreter import AsyncSandbox as AsyncCodeSandbox
from watasu_code_interpreter import Sandbox as CodeSandbox


pytestmark = pytest.mark.skipif(
    os.environ.get("WATASU_LIVE_API_TESTS") != "1",
    reason="set WATASU_LIVE_API_TESTS=1 to run live SDK smoke tests",
)

TEAM = os.environ.get("WATASU_SMOKE_TEAM", "watasu")
PREFIX = f"sdk-py-{int(time.time() * 1000)}-{os.getpid()}"
REQUEST_TIMEOUT = 240
SANDBOX_TIMEOUT = 300


def test_live_python_sync_sdk_smoke():
    assert os.environ.get("WATASU_API_KEY")
    assert ConnectionConfig().api_key == os.environ["WATASU_API_KEY"]
    assert [type(cmd.get_cmd()).__name__ for cmd in _ready_cmds()] == [
        "str",
        "str",
        "str",
        "str",
        "str",
    ]

    assert Template.exists("base") is True
    assert Template.alias_exists("base") is True
    assert Template.exists(f"{PREFIX}-missing-template") is False
    assert isinstance(Template.get_tags("base"), list)

    volume_name = f"{PREFIX}-volume"
    volume = None
    sbx = None
    code_sbx = None
    snapshots = []

    try:
        volume = Volume.create(volume_name, team=TEAM, request_timeout=REQUEST_TIMEOUT)
        assert Volume.get_info(volume.id, request_timeout=REQUEST_TIMEOUT).name == volume_name
        assert any(
            item.id == volume.id
            for item in Volume.list(team=TEAM, request_timeout=REQUEST_TIMEOUT)
        )
        connected_volume = Volume.connect(volume.id, request_timeout=REQUEST_TIMEOUT)
        connected_volume.make_dir("/workspace", force=True, request_timeout=REQUEST_TIMEOUT)
        connected_volume.write_file(
            "/workspace/hello.txt",
            "volume-ok",
            force=True,
            request_timeout=REQUEST_TIMEOUT,
        )
        assert connected_volume.read_file("/workspace/hello.txt", request_timeout=REQUEST_TIMEOUT) == "volume-ok"
        assert connected_volume.read_file(
            "/workspace/hello.txt",
            format="bytes",
            request_timeout=REQUEST_TIMEOUT,
        ) == b"volume-ok"
        assert (
            connected_volume.read_file(
                "/workspace/hello.txt",
                format="stream",
                request_timeout=REQUEST_TIMEOUT,
            ).read()
            == b"volume-ok"
        )
        assert connected_volume.exists("/workspace/hello.txt", request_timeout=REQUEST_TIMEOUT) is True
        assert connected_volume.exists("/workspace/missing.txt", request_timeout=REQUEST_TIMEOUT) is False
        assert connected_volume.get_info("/workspace/hello.txt", request_timeout=REQUEST_TIMEOUT).type == "file"
        assert any(
            entry.name == "hello.txt"
            for entry in connected_volume.list("/workspace", depth=1, request_timeout=REQUEST_TIMEOUT)
        )
        connected_volume.update_metadata(
            "/workspace/hello.txt",
            mode="0644",
            request_timeout=REQUEST_TIMEOUT,
        )
        assert connected_volume.remove("/workspace/hello.txt", request_timeout=REQUEST_TIMEOUT) is True

        sbx = Sandbox.create(
            "base",
            team=TEAM,
            timeout=SANDBOX_TIMEOUT,
            request_timeout=REQUEST_TIMEOUT,
            metadata={"smoke": PREFIX, "sdk": "python-sync"},
            envs={"WATASU_SMOKE_VALUE": "env-ok"},
            volume_mounts={"/mnt/smoke-volume": volume},
        )
        assert isinstance(sbx.id, str)
        assert sbx.is_running(request_timeout=REQUEST_TIMEOUT) is True
        assert Sandbox.get_info(sbx.id, request_timeout=REQUEST_TIMEOUT).sandbox_id == sbx.id
        assert sbx.get_info(request_timeout=REQUEST_TIMEOUT).sandbox_id == sbx.id
        Sandbox.set_timeout(sbx.id, SANDBOX_TIMEOUT, request_timeout=REQUEST_TIMEOUT)
        sbx.set_timeout(SANDBOX_TIMEOUT, request_timeout=REQUEST_TIMEOUT)
        assert any(
            item.sandbox_id == sbx.id
            for item in Sandbox.list(
                team=TEAM,
                limit=10,
                query={"metadata": {"smoke": PREFIX}},
                request_timeout=REQUEST_TIMEOUT,
            ).next_items()
        )
        assert isinstance(sbx.get_host(8080), str)
        assert isinstance(sbx.get_mcp_url(), str)
        assert sbx.get_mcp_token(request_timeout=REQUEST_TIMEOUT) is None
        assert isinstance(sbx.get_metrics(request_timeout=REQUEST_TIMEOUT), list)
        assert isinstance(Sandbox.get_metrics(sbx.id, request_timeout=REQUEST_TIMEOUT), list)
        sbx.update_network({"allow_internet_access": True}, request_timeout=REQUEST_TIMEOUT)
        Sandbox.update_network(
            sbx.id,
            {"allow_internet_access": True},
            request_timeout=REQUEST_TIMEOUT,
        )

        _exercise_files(sbx)
        _exercise_signed_file_urls(sbx)
        _exercise_commands(sbx)
        _exercise_pty(sbx)
        _exercise_git(sbx)

        snapshot = sbx.create_snapshot(
            name=f"{PREFIX}-snapshot",
            request_timeout=REQUEST_TIMEOUT,
        )
        snapshots.append(snapshot.snapshot_id)
        assert snapshot.snapshot_id
        assert any(
            item.snapshot_id == snapshot.snapshot_id
            for item in sbx.list_snapshots(limit=10, request_timeout=REQUEST_TIMEOUT).next_items()
        )
        assert any(
            item.snapshot_id == snapshot.snapshot_id
            for item in Sandbox.list_snapshots(
                sandbox_id=sbx.id,
                limit=10,
                request_timeout=REQUEST_TIMEOUT,
            ).next_items()
        )
        assert sbx.delete_snapshot(snapshot.snapshot_id, request_timeout=REQUEST_TIMEOUT) is True
        snapshots.pop()
        assert Sandbox.delete_snapshot(f"{PREFIX}-missing-snapshot", request_timeout=REQUEST_TIMEOUT) is False

        connected = Sandbox.connect(
            sbx.id,
            timeout=SANDBOX_TIMEOUT,
            request_timeout=REQUEST_TIMEOUT,
        )
        assert connected.id == sbx.id
        assert connected.commands.run("printf connected-ok", request_timeout=REQUEST_TIMEOUT).stdout == "connected-ok"
        assert connected.resume(timeout=SANDBOX_TIMEOUT, request_timeout=REQUEST_TIMEOUT) is True

        code_sbx = CodeSandbox.create(
            team=TEAM,
            timeout=SANDBOX_TIMEOUT,
            request_timeout=REQUEST_TIMEOUT,
            metadata={"smoke": PREFIX, "sdk": "python-code"},
        )
        execution = code_sbx.run_code(
            "print('code-ok')",
            timeout=30,
            request_timeout=REQUEST_TIMEOUT,
        )
        assert execution.error is None
        assert "code-ok" in "".join(str(item) for item in execution.logs.stdout)
        context = code_sbx.create_code_context(
            cwd="/tmp",
            language="python",
            request_timeout=REQUEST_TIMEOUT,
        )
        assert any(
            item.id == context.id
            for item in code_sbx.list_code_contexts(request_timeout=REQUEST_TIMEOUT)
        )
        code_sbx.restart_code_context(context, request_timeout=REQUEST_TIMEOUT)
        code_sbx.remove_code_context(context, request_timeout=REQUEST_TIMEOUT)
    finally:
        for snapshot_id in snapshots:
            try:
                Sandbox.delete_snapshot(snapshot_id, request_timeout=REQUEST_TIMEOUT)
            except Exception:
                pass
        if code_sbx is not None:
            _ignore_errors(lambda: code_sbx.kill(request_timeout=REQUEST_TIMEOUT))
        if sbx is not None:
            _ignore_errors(lambda: sbx.kill(request_timeout=REQUEST_TIMEOUT))
        if volume is not None:
            _ignore_errors(lambda: volume.destroy(request_timeout=REQUEST_TIMEOUT))

    assert Volume.destroy(f"{PREFIX}-missing-volume", request_timeout=REQUEST_TIMEOUT) is False


def test_live_python_async_sdk_smoke():
    asyncio.run(_test_live_python_async_sdk_smoke())


async def _test_live_python_async_sdk_smoke():
    assert await AsyncTemplate.exists("base", request_timeout=REQUEST_TIMEOUT) is True
    assert await AsyncTemplate.alias_exists("base", request_timeout=REQUEST_TIMEOUT) is True
    assert isinstance(await AsyncTemplate.get_tags("base", request_timeout=REQUEST_TIMEOUT), list)

    volume_name = f"{PREFIX}-async-volume"
    volume = None
    sbx = None
    code_sbx = None
    snapshots = []

    try:
        volume = await AsyncVolume.create(volume_name, team=TEAM, request_timeout=REQUEST_TIMEOUT)
        assert (await AsyncVolume.get_info(volume.id, request_timeout=REQUEST_TIMEOUT)).name == volume_name
        assert any(
            item.id == volume.id
            for item in await AsyncVolume.list(team=TEAM, request_timeout=REQUEST_TIMEOUT)
        )
        connected_volume = await AsyncVolume.connect(volume.id, request_timeout=REQUEST_TIMEOUT)
        await connected_volume.make_dir("/workspace", force=True, request_timeout=REQUEST_TIMEOUT)
        await connected_volume.write_file(
            "/workspace/hello.txt",
            b"async-volume-ok",
            force=True,
            request_timeout=REQUEST_TIMEOUT,
        )
        assert await connected_volume.read_file(
            "/workspace/hello.txt",
            request_timeout=REQUEST_TIMEOUT,
        ) == "async-volume-ok"
        assert await connected_volume.exists("/workspace/hello.txt", request_timeout=REQUEST_TIMEOUT) is True
        await connected_volume.update_metadata(
            "/workspace/hello.txt",
            mode="0644",
            request_timeout=REQUEST_TIMEOUT,
        )
        assert await connected_volume.remove("/workspace/hello.txt", request_timeout=REQUEST_TIMEOUT) is True

        async with await AsyncSandbox.create(
            "base",
            team=TEAM,
            timeout=SANDBOX_TIMEOUT,
            request_timeout=REQUEST_TIMEOUT,
            metadata={"smoke": PREFIX, "sdk": "python-async"},
            envs={"WATASU_SMOKE_VALUE": "async-env-ok"},
            volume_mounts={"/mnt/smoke-volume": volume},
        ) as sbx:
            assert isinstance(sbx.id, str)
            assert await sbx.is_running(request_timeout=REQUEST_TIMEOUT) is True
            assert (await AsyncSandbox.get_info(sbx.id, request_timeout=REQUEST_TIMEOUT)).sandbox_id == sbx.id
            assert (await sbx.get_info(request_timeout=REQUEST_TIMEOUT)).sandbox_id == sbx.id
            await AsyncSandbox.set_timeout(sbx.id, SANDBOX_TIMEOUT, request_timeout=REQUEST_TIMEOUT)
            await sbx.set_timeout(SANDBOX_TIMEOUT, request_timeout=REQUEST_TIMEOUT)
            paginator = await AsyncSandbox.list(
                team=TEAM,
                limit=10,
                query={"metadata": {"smoke": PREFIX}},
                request_timeout=REQUEST_TIMEOUT,
            )
            assert any(item.sandbox_id == sbx.id for item in await paginator.next_items())
            assert isinstance(sbx.get_host(8080), str)
            assert isinstance(sbx.get_mcp_url(), str)
            assert await sbx.get_mcp_token(request_timeout=REQUEST_TIMEOUT) is None
            assert isinstance(await sbx.get_metrics(request_timeout=REQUEST_TIMEOUT), list)
            assert isinstance(await AsyncSandbox.get_metrics(sbx.id, request_timeout=REQUEST_TIMEOUT), list)
            await sbx.update_network({"allow_internet_access": True}, request_timeout=REQUEST_TIMEOUT)
            await AsyncSandbox.update_network(
                sbx.id,
                {"allow_internet_access": True},
                request_timeout=REQUEST_TIMEOUT,
            )

            await _exercise_async_files(sbx)
            await _exercise_async_signed_file_urls(sbx)
            await _exercise_async_commands(sbx)
            await _exercise_async_pty(sbx)
            await _exercise_async_git(sbx)

            snapshot = await sbx.create_snapshot(
                name=f"{PREFIX}-async-snapshot",
                request_timeout=REQUEST_TIMEOUT,
            )
            snapshots.append(snapshot.snapshot_id)
            assert snapshot.snapshot_id
            assert any(
                item.snapshot_id == snapshot.snapshot_id
                for item in await sbx.list_snapshots(
                    limit=10,
                    request_timeout=REQUEST_TIMEOUT,
                ).next_items()
            )
            assert any(
                item.snapshot_id == snapshot.snapshot_id
                for item in await AsyncSandbox.list_snapshots(
                    sandbox_id=sbx.id,
                    limit=10,
                    request_timeout=REQUEST_TIMEOUT,
                ).next_items()
            )
            assert await sbx.delete_snapshot(snapshot.snapshot_id, request_timeout=REQUEST_TIMEOUT) is True
            snapshots.pop()
            assert await AsyncSandbox.delete_snapshot(
                f"{PREFIX}-async-missing-snapshot",
                request_timeout=REQUEST_TIMEOUT,
            ) is False

            connected = await AsyncSandbox.connect(
                sbx.id,
                timeout=SANDBOX_TIMEOUT,
                request_timeout=REQUEST_TIMEOUT,
            )
            assert connected.id == sbx.id
            connected_run = await connected.commands.run(
                "printf async-connected-ok",
                request_timeout=REQUEST_TIMEOUT,
            )
            assert connected_run.stdout == "async-connected-ok"
            assert await connected.resume(timeout=SANDBOX_TIMEOUT, request_timeout=REQUEST_TIMEOUT) is True

        sbx = None
        code_sbx = await AsyncCodeSandbox.create(
            team=TEAM,
            timeout=SANDBOX_TIMEOUT,
            request_timeout=REQUEST_TIMEOUT,
            metadata={"smoke": PREFIX, "sdk": "python-async-code"},
        )
        execution = await code_sbx.run_code(
            "print('async-code-ok')",
            timeout=30,
            request_timeout=REQUEST_TIMEOUT,
        )
        assert execution.error is None
        assert "async-code-ok" in "".join(str(item) for item in execution.logs.stdout)
        context = await code_sbx.create_code_context(
            cwd="/tmp",
            language="python",
            request_timeout=REQUEST_TIMEOUT,
        )
        assert any(
            item.id == context.id
            for item in await code_sbx.list_code_contexts(request_timeout=REQUEST_TIMEOUT)
        )
        await code_sbx.restart_code_context(context, request_timeout=REQUEST_TIMEOUT)
        await code_sbx.remove_code_context(context, request_timeout=REQUEST_TIMEOUT)
    finally:
        for snapshot_id in snapshots:
            try:
                await AsyncSandbox.delete_snapshot(snapshot_id, request_timeout=REQUEST_TIMEOUT)
            except Exception:
                pass
        if code_sbx is not None:
            await _async_ignore_errors(code_sbx.kill(request_timeout=REQUEST_TIMEOUT))
        if sbx is not None:
            await _async_ignore_errors(sbx.kill(request_timeout=REQUEST_TIMEOUT))
        if volume is not None:
            await _async_ignore_errors(volume.destroy(request_timeout=REQUEST_TIMEOUT))

    assert await AsyncVolume.destroy(f"{PREFIX}-async-missing-volume", request_timeout=REQUEST_TIMEOUT) is False


def _exercise_files(sbx: Sandbox) -> None:
    directory = f"/tmp/{PREFIX}-files"
    sbx.files.make_dir(directory, request_timeout=REQUEST_TIMEOUT)
    watcher = sbx.files.watch_dir(
        directory,
        request_timeout=REQUEST_TIMEOUT,
        include_entry=True,
    )
    try:
        assert sbx.files.exists(f"{directory}/missing.txt", request_timeout=REQUEST_TIMEOUT) is False
        sbx.files.write(f"{directory}/hello.txt", "file-ok", request_timeout=REQUEST_TIMEOUT)
        sbx.files.write(f"{directory}/bytes.bin", b"\x04\x05\x06", request_timeout=REQUEST_TIMEOUT)
        sbx.files.write_files(
            [
                {"path": f"{directory}/batch-a.txt", "data": "a"},
                {"path": f"{directory}/batch-b.txt", "data": b"b"},
            ],
            request_timeout=REQUEST_TIMEOUT,
        )
        assert sbx.files.read(f"{directory}/hello.txt", request_timeout=REQUEST_TIMEOUT) == "file-ok"
        assert sbx.files.read_bytes(f"{directory}/bytes.bin", request_timeout=REQUEST_TIMEOUT) == b"\x04\x05\x06"
        assert b"file-ok" == b"".join(
            sbx.files.read(
                f"{directory}/hello.txt",
                format="stream",
                request_timeout=REQUEST_TIMEOUT,
            )
        )
        assert sbx.files.get_info(f"{directory}/hello.txt", request_timeout=REQUEST_TIMEOUT).name == "hello.txt"
        assert any(
            entry.name == "hello.txt"
            for entry in sbx.files.list(directory, depth=1, request_timeout=REQUEST_TIMEOUT)
        )
        assert sbx.files.exists(f"{directory}/hello.txt", request_timeout=REQUEST_TIMEOUT) is True
        renamed = sbx.files.rename(
            f"{directory}/hello.txt",
            f"{directory}/renamed.txt",
            request_timeout=REQUEST_TIMEOUT,
        )
        assert renamed.name == "renamed.txt"
        _wait_until(
            lambda: len(watcher.get_new_events()) > 0,
            timeout=10,
            label="filesystem watch event",
        )
    finally:
        watcher.stop()
    sbx.files.remove(f"{directory}/renamed.txt", request_timeout=REQUEST_TIMEOUT)


async def _exercise_async_files(sbx: AsyncSandbox) -> None:
    directory = f"/tmp/{PREFIX}-async-files"
    await sbx.files.make_dir(directory, request_timeout=REQUEST_TIMEOUT)
    watcher = await sbx.files.watch_dir(
        directory,
        request_timeout=REQUEST_TIMEOUT,
        include_entry=True,
    )
    try:
        assert await sbx.files.exists(f"{directory}/missing.txt", request_timeout=REQUEST_TIMEOUT) is False
        await sbx.files.write(f"{directory}/hello.txt", "async-file-ok", request_timeout=REQUEST_TIMEOUT)
        await sbx.files.write(f"{directory}/bytes.bin", b"\x07\x08\x09", request_timeout=REQUEST_TIMEOUT)
        await sbx.files.write_files(
            [
                {"path": f"{directory}/batch-a.txt", "data": "a"},
                {"path": f"{directory}/batch-b.txt", "data": b"b"},
            ],
            request_timeout=REQUEST_TIMEOUT,
        )
        assert await sbx.files.read(f"{directory}/hello.txt", request_timeout=REQUEST_TIMEOUT) == "async-file-ok"
        assert await sbx.files.read_bytes(f"{directory}/bytes.bin", request_timeout=REQUEST_TIMEOUT) == b"\x07\x08\x09"
        stream = await sbx.files.read(
            f"{directory}/hello.txt",
            format="stream",
            request_timeout=REQUEST_TIMEOUT,
        )
        assert b"async-file-ok" == b"".join(stream)
        assert (await sbx.files.get_info(f"{directory}/hello.txt", request_timeout=REQUEST_TIMEOUT)).name == "hello.txt"
        assert any(
            entry.name == "hello.txt"
            for entry in await sbx.files.list(directory, depth=1, request_timeout=REQUEST_TIMEOUT)
        )
        assert await sbx.files.exists(f"{directory}/hello.txt", request_timeout=REQUEST_TIMEOUT) is True
        renamed = await sbx.files.rename(
            f"{directory}/hello.txt",
            f"{directory}/renamed.txt",
            request_timeout=REQUEST_TIMEOUT,
        )
        assert renamed.name == "renamed.txt"
        await _async_wait_until(
            lambda: watcher.get_new_events(),
            timeout=10,
            label="async filesystem watch event",
        )
    finally:
        await watcher.stop()
    await sbx.files.remove(f"{directory}/renamed.txt", request_timeout=REQUEST_TIMEOUT)


def _exercise_signed_file_urls(sbx: Sandbox) -> None:
    path = f"/tmp/{PREFIX}-signed.txt"
    upload = sbx.upload_url_info(
        path,
        expires_in_seconds=120,
        request_timeout=REQUEST_TIMEOUT,
    )
    assert upload.method == "POST"
    assert upload.path == path
    _request(upload.url, method=upload.method, data=b"signed-ok")
    download = sbx.download_url_info(
        path,
        expires_in_seconds=120,
        request_timeout=REQUEST_TIMEOUT,
    )
    assert download.method == "GET"
    assert sbx.upload_url(path, request_timeout=REQUEST_TIMEOUT).startswith("http")
    assert sbx.download_url(path, request_timeout=REQUEST_TIMEOUT).startswith("http")
    assert _request(download.url, method=download.method) == b"signed-ok"


async def _exercise_async_signed_file_urls(sbx: AsyncSandbox) -> None:
    path = f"/tmp/{PREFIX}-async-signed.txt"
    upload = await sbx.upload_url_info(
        path,
        expires_in_seconds=120,
        request_timeout=REQUEST_TIMEOUT,
    )
    assert upload.method == "POST"
    await asyncio.to_thread(_request, upload.url, method=upload.method, data=b"async-signed-ok")
    download = await sbx.download_url_info(
        path,
        expires_in_seconds=120,
        request_timeout=REQUEST_TIMEOUT,
    )
    assert download.method == "GET"
    assert (await sbx.upload_url(path, request_timeout=REQUEST_TIMEOUT)).startswith("http")
    assert (await sbx.download_url(path, request_timeout=REQUEST_TIMEOUT)).startswith("http")
    assert await asyncio.to_thread(_request, download.url, method=download.method) == b"async-signed-ok"


def _exercise_commands(sbx: Sandbox) -> None:
    run = sbx.commands.run("printf command-ok", request_timeout=REQUEST_TIMEOUT)
    assert run.stdout == "command-ok"
    with pytest.raises(CommandExitException):
        sbx.commands.run("echo fail >&2; exit 7", request_timeout=REQUEST_TIMEOUT)

    cat = sbx.commands.run("cat", background=True, stdin=True, request_timeout=REQUEST_TIMEOUT)
    cat.send_stdin("stdin-ok\n", request_timeout=REQUEST_TIMEOUT)
    cat.close_stdin(request_timeout=REQUEST_TIMEOUT)
    assert cat.wait().stdout == "stdin-ok\n"

    sleeper = sbx.commands.run(
        "sleep 60",
        background=True,
        request_timeout=REQUEST_TIMEOUT,
    )
    assert any(str(item.pid) == str(sleeper.pid) for item in sbx.commands.list(request_timeout=REQUEST_TIMEOUT))
    attached = sbx.commands.connect(sleeper.pid, request_timeout=REQUEST_TIMEOUT)
    attached.disconnect()
    assert sleeper.kill() is True
    with pytest.raises(Exception):
        sleeper.wait()


async def _exercise_async_commands(sbx: AsyncSandbox) -> None:
    run = await sbx.commands.run("printf async-command-ok", request_timeout=REQUEST_TIMEOUT)
    assert run.stdout == "async-command-ok"
    with pytest.raises(CommandExitException):
        await sbx.commands.run("echo fail >&2; exit 7", request_timeout=REQUEST_TIMEOUT)

    cat = await sbx.commands.run(
        "cat",
        background=True,
        stdin=True,
        request_timeout=REQUEST_TIMEOUT,
    )
    await cat.send_stdin("async-stdin-ok\n", request_timeout=REQUEST_TIMEOUT)
    await cat.close_stdin(request_timeout=REQUEST_TIMEOUT)
    assert (await cat.wait()).stdout == "async-stdin-ok\n"

    sleeper = await sbx.commands.run("sleep 60", background=True, request_timeout=REQUEST_TIMEOUT)
    assert any(str(item.pid) == str(sleeper.pid) for item in await sbx.commands.list(request_timeout=REQUEST_TIMEOUT))
    attached = await sbx.commands.connect(sleeper.pid, request_timeout=REQUEST_TIMEOUT)
    await attached.disconnect()
    assert await sleeper.kill() is True
    with pytest.raises(Exception):
        await sleeper.wait()


def _exercise_pty(sbx: Sandbox) -> None:
    output = []
    handle = sbx.pty.create(PtySize(rows=24, cols=80), timeout=30, request_timeout=REQUEST_TIMEOUT)
    handle.send_stdin("printf pty-ok; exit\n", request_timeout=REQUEST_TIMEOUT)
    result = handle.wait(on_pty=lambda chunk: output.append(chunk))
    assert result.exit_code == 0
    assert b"pty-ok" in b"".join(output)

    long = sbx.pty.create(PtySize(rows=24, cols=80), timeout=120, request_timeout=REQUEST_TIMEOUT)
    connected = sbx.pty.connect(long.pid, request_timeout=REQUEST_TIMEOUT)
    connected.disconnect()
    sbx.pty.resize(long.pid, PtySize(rows=30, cols=100), request_timeout=REQUEST_TIMEOUT)
    sbx.pty.send_input(long.pid, "echo ignored\n", request_timeout=REQUEST_TIMEOUT)
    assert sbx.pty.kill(long.pid, request_timeout=REQUEST_TIMEOUT) is True


async def _exercise_async_pty(sbx: AsyncSandbox) -> None:
    output = []
    handle = await sbx.pty.create(
        PtySize(rows=24, cols=80),
        on_data=lambda chunk: output.append(chunk),
        timeout=30,
        request_timeout=REQUEST_TIMEOUT,
    )
    await handle.send_stdin("printf async-pty-ok; exit\n", request_timeout=REQUEST_TIMEOUT)
    assert (await handle.wait()).exit_code == 0
    assert b"async-pty-ok" in b"".join(output)

    long = await sbx.pty.create(
        PtySize(rows=24, cols=80),
        on_data=lambda chunk: None,
        timeout=120,
        request_timeout=REQUEST_TIMEOUT,
    )
    connected = await sbx.pty.connect(
        long.pid,
        on_data=lambda chunk: None,
        request_timeout=REQUEST_TIMEOUT,
    )
    await connected.disconnect()
    await sbx.pty.resize(long.pid, PtySize(rows=30, cols=100), request_timeout=REQUEST_TIMEOUT)
    await sbx.pty.send_input(long.pid, "echo ignored\n", request_timeout=REQUEST_TIMEOUT)
    assert await sbx.pty.kill(long.pid, request_timeout=REQUEST_TIMEOUT) is True


def _exercise_git(sbx: Sandbox) -> None:
    repo = f"/tmp/{PREFIX}-repo"
    remote = f"/tmp/{PREFIX}-remote.git"
    clone = f"/tmp/{PREFIX}-clone"
    sbx.commands.run(f"rm -rf {repo} {remote} {clone}", request_timeout=REQUEST_TIMEOUT)
    sbx.commands.run(f"git init --bare {remote}", request_timeout=REQUEST_TIMEOUT)
    sbx.git.dangerously_authenticate(
        "user",
        "token",
        host="example.test",
        protocol="https",
        request_timeout=REQUEST_TIMEOUT,
    )
    sbx.git.init(repo, initial_branch="main", request_timeout=REQUEST_TIMEOUT)
    sbx.git.configure_user(
        "Watasu Smoke",
        "smoke@watasu.io",
        path=repo,
        scope="local",
        request_timeout=REQUEST_TIMEOUT,
    )
    sbx.git.set_config(
        "smoke.key",
        "smoke-value",
        path=repo,
        scope="local",
        request_timeout=REQUEST_TIMEOUT,
    )
    assert sbx.git.get_config("smoke.key", path=repo, scope="local", request_timeout=REQUEST_TIMEOUT) == "smoke-value"
    sbx.commands.run(f"printf one > {repo}/file.txt", request_timeout=REQUEST_TIMEOUT)
    assert sbx.git.status(repo, request_timeout=REQUEST_TIMEOUT).has_untracked is True
    sbx.git.add(repo, files=["file.txt"], request_timeout=REQUEST_TIMEOUT)
    sbx.git.commit(repo, "initial", request_timeout=REQUEST_TIMEOUT)
    assert sbx.git.status(repo, request_timeout=REQUEST_TIMEOUT).is_clean is True
    sbx.git.create_branch(repo, "feature", request_timeout=REQUEST_TIMEOUT)
    assert sbx.git.branches(repo, request_timeout=REQUEST_TIMEOUT).current_branch == "feature"
    sbx.git.checkout_branch(repo, "main", request_timeout=REQUEST_TIMEOUT)
    sbx.git.delete_branch(repo, "feature", force=True, request_timeout=REQUEST_TIMEOUT)
    sbx.git.remote_add(repo, "origin", remote, overwrite=True, request_timeout=REQUEST_TIMEOUT)
    assert sbx.git.remote_get(repo, "origin", request_timeout=REQUEST_TIMEOUT) == remote
    sbx.git.push(repo, remote="origin", branch="main", request_timeout=REQUEST_TIMEOUT)
    sbx.git.clone(remote, path=clone, branch="main", request_timeout=REQUEST_TIMEOUT)
    sbx.commands.run(f"printf two > {repo}/file.txt", request_timeout=REQUEST_TIMEOUT)
    sbx.git.add(repo, all=True, request_timeout=REQUEST_TIMEOUT)
    sbx.git.commit(repo, "second", request_timeout=REQUEST_TIMEOUT)
    sbx.git.push(repo, remote="origin", branch="main", request_timeout=REQUEST_TIMEOUT)
    sbx.git.pull(clone, remote="origin", branch="main", request_timeout=REQUEST_TIMEOUT)
    assert sbx.files.read(f"{clone}/file.txt", request_timeout=REQUEST_TIMEOUT) == "two"
    sbx.commands.run(f"printf dirty > {repo}/file.txt", request_timeout=REQUEST_TIMEOUT)
    sbx.git.restore(repo, paths=["file.txt"], worktree=True, request_timeout=REQUEST_TIMEOUT)
    assert sbx.files.read(f"{repo}/file.txt", request_timeout=REQUEST_TIMEOUT) == "two"
    sbx.commands.run(f"printf staged > {repo}/staged.txt", request_timeout=REQUEST_TIMEOUT)
    sbx.git.add(repo, files=["staged.txt"], request_timeout=REQUEST_TIMEOUT)
    sbx.git.reset(repo, paths=["staged.txt"], request_timeout=REQUEST_TIMEOUT)
    assert sbx.git.status(repo, request_timeout=REQUEST_TIMEOUT).has_untracked is True
    sbx.git.checkout(repo, "main", request_timeout=REQUEST_TIMEOUT)


async def _exercise_async_git(sbx: AsyncSandbox) -> None:
    repo = f"/tmp/{PREFIX}-async-repo"
    remote = f"/tmp/{PREFIX}-async-remote.git"
    clone = f"/tmp/{PREFIX}-async-clone"
    await sbx.commands.run(f"rm -rf {repo} {remote} {clone}", request_timeout=REQUEST_TIMEOUT)
    await sbx.commands.run(f"git init --bare {remote}", request_timeout=REQUEST_TIMEOUT)
    await sbx.git.dangerously_authenticate(
        "user",
        "token",
        host="example.test",
        protocol="https",
        request_timeout=REQUEST_TIMEOUT,
    )
    await sbx.git.init(repo, initial_branch="main", request_timeout=REQUEST_TIMEOUT)
    await sbx.git.configure_user(
        "Watasu Smoke",
        "smoke@watasu.io",
        path=repo,
        scope="local",
        request_timeout=REQUEST_TIMEOUT,
    )
    await sbx.git.set_config(
        "smoke.key",
        "async-smoke-value",
        path=repo,
        scope="local",
        request_timeout=REQUEST_TIMEOUT,
    )
    assert await sbx.git.get_config(
        "smoke.key",
        path=repo,
        scope="local",
        request_timeout=REQUEST_TIMEOUT,
    ) == "async-smoke-value"
    await sbx.commands.run(f"printf one > {repo}/file.txt", request_timeout=REQUEST_TIMEOUT)
    assert (await sbx.git.status(repo, request_timeout=REQUEST_TIMEOUT)).has_untracked is True
    await sbx.git.add(repo, files=["file.txt"], request_timeout=REQUEST_TIMEOUT)
    await sbx.git.commit(repo, "initial", request_timeout=REQUEST_TIMEOUT)
    assert (await sbx.git.status(repo, request_timeout=REQUEST_TIMEOUT)).is_clean is True
    await sbx.git.create_branch(repo, "feature", request_timeout=REQUEST_TIMEOUT)
    assert (await sbx.git.branches(repo, request_timeout=REQUEST_TIMEOUT)).current_branch == "feature"
    await sbx.git.checkout_branch(repo, "main", request_timeout=REQUEST_TIMEOUT)
    await sbx.git.delete_branch(repo, "feature", force=True, request_timeout=REQUEST_TIMEOUT)
    await sbx.git.remote_add(repo, "origin", remote, overwrite=True, request_timeout=REQUEST_TIMEOUT)
    assert await sbx.git.remote_get(repo, "origin", request_timeout=REQUEST_TIMEOUT) == remote
    await sbx.git.push(repo, remote="origin", branch="main", request_timeout=REQUEST_TIMEOUT)
    await sbx.git.clone(remote, path=clone, branch="main", request_timeout=REQUEST_TIMEOUT)
    await sbx.commands.run(f"printf two > {repo}/file.txt", request_timeout=REQUEST_TIMEOUT)
    await sbx.git.add(repo, all=True, request_timeout=REQUEST_TIMEOUT)
    await sbx.git.commit(repo, "second", request_timeout=REQUEST_TIMEOUT)
    await sbx.git.push(repo, remote="origin", branch="main", request_timeout=REQUEST_TIMEOUT)
    await sbx.git.pull(clone, remote="origin", branch="main", request_timeout=REQUEST_TIMEOUT)
    assert await sbx.files.read(f"{clone}/file.txt", request_timeout=REQUEST_TIMEOUT) == "two"
    await sbx.commands.run(f"printf dirty > {repo}/file.txt", request_timeout=REQUEST_TIMEOUT)
    await sbx.git.restore(repo, paths=["file.txt"], worktree=True, request_timeout=REQUEST_TIMEOUT)
    assert await sbx.files.read(f"{repo}/file.txt", request_timeout=REQUEST_TIMEOUT) == "two"
    await sbx.commands.run(f"printf staged > {repo}/staged.txt", request_timeout=REQUEST_TIMEOUT)
    await sbx.git.add(repo, files=["staged.txt"], request_timeout=REQUEST_TIMEOUT)
    await sbx.git.reset(repo, paths=["staged.txt"], request_timeout=REQUEST_TIMEOUT)
    assert (await sbx.git.status(repo, request_timeout=REQUEST_TIMEOUT)).has_untracked is True
    await sbx.git.checkout(repo, "main", request_timeout=REQUEST_TIMEOUT)


def _ready_cmds():
    return [
        wait_for_port(8080),
        wait_for_url("http://127.0.0.1:8080/health"),
        wait_for_process("bash"),
        wait_for_file("/tmp/ready"),
        wait_for_timeout(1200),
    ]


def _request(url: str, *, method: str, data: bytes | None = None) -> bytes:
    request = urllib.request.Request(url, method=method, data=data)
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        assert 200 <= response.status < 300
        return response.read()


def _wait_until(check, *, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {label}")


async def _async_wait_until(check, *, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await check():
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"timed out waiting for {label}")


def _ignore_errors(fn) -> None:
    try:
        fn()
    except Exception:
        pass


async def _async_ignore_errors(awaitable) -> None:
    try:
        await awaitable
    except Exception:
        pass
