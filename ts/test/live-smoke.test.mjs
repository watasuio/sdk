import assert from 'node:assert/strict'
import test from 'node:test'

import {
  CommandExitError,
  ConnectionConfig,
  InvalidArgumentError,
  Sandbox,
  Template,
  Volume,
  base64DecodeBytes,
  base64DecodeText,
  base64Encode,
  waitForFile,
  waitForPort,
  waitForProcess,
  waitForTimeout,
  waitForURL,
} from '../dist/index.js'
import { Sandbox as CodeInterpreterSandbox } from '../dist/codeInterpreter.js'

const live = process.env.WATASU_LIVE_API_TESTS === '1'
const team = process.env.WATASU_SMOKE_TEAM ?? 'watasu'
const prefix = `sdk-ts-${Date.now()}-${process.pid}`
const requestTimeoutMs = 240_000
const sandboxTimeoutMs = 900_000

test('live broad TypeScript SDK smoke', { skip: live ? false : 'set WATASU_LIVE_API_TESTS=1 to run live API smoke tests', timeout: 1_200_000 }, async () => {
  assert.ok(process.env.WATASU_API_KEY, 'WATASU_API_KEY is required')
  assert.equal(new ConnectionConfig().apiKey, process.env.WATASU_API_KEY)
  assert.equal(base64DecodeText(base64Encode(new TextEncoder().encode('codec-ok'))), 'codec-ok')
  assert.deepEqual(Array.from(base64DecodeBytes(base64Encode(new Uint8Array([1, 2, 3])))), [1, 2, 3])

  const ready = [
    waitForPort(8080),
    waitForURL('http://127.0.0.1:8080/health'),
    waitForProcess('bash'),
    waitForFile('/tmp/ready'),
    waitForTimeout(1200),
  ]
  assert.deepEqual(ready.map((cmd) => typeof cmd.getCmd()), ['string', 'string', 'string', 'string', 'string'])

  assert.equal(await Template.exists('base'), true)
  assert.equal(await Template.aliasExists('base'), true)
  assert.equal(await Template.exists(`${prefix}-missing-template`), false)
  assert.ok(Array.isArray(await Template.getTags('base')))

  const volumeName = `${prefix}-volume`
  let volume
  let sbx
  let codeSbx
  let restoredSbx
  const snapshots = []

  try {
    volume = await Volume.create(volumeName, { team, requestTimeoutMs })
    assert.equal((await Volume.getInfo(volume.id, { requestTimeoutMs })).name, volumeName)
    assert.ok((await Volume.list({ team, requestTimeoutMs })).some((item) => item.id === volume.id))
    const connectedVolume = await Volume.connect(volume.id, { requestTimeoutMs })
    await connectedVolume.makeDir('/workspace', { requestTimeoutMs })
    await connectedVolume.writeFile('/workspace/hello.txt', 'volume-ok', { requestTimeoutMs, force: true })
    assert.equal(await connectedVolume.readFile('/workspace/hello.txt', { requestTimeoutMs }), 'volume-ok')
    assert.deepEqual(Array.from(await connectedVolume.readFile('/workspace/hello.txt', { requestTimeoutMs, format: 'bytes' })), Array.from(new TextEncoder().encode('volume-ok')))
    assert.ok(await connectedVolume.readFile('/workspace/hello.txt', { requestTimeoutMs, format: 'blob' }))
    const volumeStream = await connectedVolume.readFile('/workspace/hello.txt', { requestTimeoutMs, format: 'stream' })
    assert.ok(volumeStream.getReader)
    assert.equal(await connectedVolume.exists('/workspace/hello.txt', { requestTimeoutMs }), true)
    assert.equal(await connectedVolume.exists('/workspace/missing.txt', { requestTimeoutMs }), false)
    assert.equal((await connectedVolume.getInfo('/workspace/hello.txt', { requestTimeoutMs })).type, 'file')
    assert.ok((await connectedVolume.list('/workspace', { requestTimeoutMs })).some((entry) => entry.name === 'hello.txt'))
    await connectedVolume.updateMetadata('/workspace/hello.txt', { requestTimeoutMs, mode: '0644' })
    assert.equal(await connectedVolume.remove('/workspace/hello.txt', { requestTimeoutMs }), true)

    sbx = await Sandbox.create('base', {
      team,
      timeoutMs: sandboxTimeoutMs,
      requestTimeoutMs,
      metadata: { smoke: prefix, sdk: 'typescript' },
      envs: { WATASU_SMOKE_VALUE: 'env-ok' },
      volumeMounts: { '/mnt/smoke-volume': volume },
    })

    assert.equal(typeof sbx.id, 'string')
    assert.equal(await sbx.isRunning({ requestTimeoutMs }), true)
    assert.equal((await Sandbox.getInfo(sbx.id, { requestTimeoutMs })).sandboxId, sbx.id)
    assert.equal((await Sandbox.getFullInfo(sbx.id, { requestTimeoutMs })).sandboxId, sbx.id)
    assert.equal((await sbx.getInfo({ requestTimeoutMs })).sandboxId, sbx.id)
    await Sandbox.setTimeout(sbx.id, sandboxTimeoutMs, { requestTimeoutMs })
    await sbx.setTimeout(sandboxTimeoutMs, { requestTimeoutMs })
    assert.ok((await Sandbox.list({ team, limit: 10, query: { metadata: { smoke: prefix } } }).nextItems()).some((item) => item.sandboxId === sbx.id))
    assert.equal(typeof sbx.getHost(8080), 'string')
    assert.equal(typeof sbx.getMcpUrl(), 'string')
    assert.equal(await sbx.getMcpToken(), undefined)
    assert.ok(Array.isArray(await sbx.getMetrics({ requestTimeoutMs })))
    assert.ok(Array.isArray(await Sandbox.getMetrics(sbx.id, { requestTimeoutMs })))
    await sbx.updateNetwork({ allowInternetAccess: true }, { requestTimeoutMs })
    await Sandbox.updateNetwork(sbx.id, { allowInternetAccess: true }, { requestTimeoutMs })

    await exerciseFiles(sbx)
    await exerciseSignedFileUrls(sbx)
    await exerciseCommands(sbx)
    await exerciseProcessManager(sbx)
    await exercisePty(sbx)
    await exerciseTerminal(sbx)
    await exerciseGit(sbx)

    const snapshot = await sbx.createSnapshot({ name: `${prefix}-snapshot`, requestTimeoutMs })
    snapshots.push(snapshot.snapshotId)
    assert.equal(typeof snapshot.snapshotId, 'string')
    assert.ok((await sbx.listSnapshots({ limit: 10 }).nextItems({ requestTimeoutMs })).some((item) => item.snapshotId === snapshot.snapshotId))
    assert.ok((await Sandbox.listSnapshots({ sandboxId: sbx.id, limit: 10 }).nextItems({ requestTimeoutMs })).some((item) => item.snapshotId === snapshot.snapshotId))
    restoredSbx = await Sandbox.create(snapshot.snapshotId, {
      timeoutMs: sandboxTimeoutMs,
      requestTimeoutMs,
      team,
      metadata: { smoke: prefix, sdk: 'typescript-restored' },
    })
    assert.equal(await restoredSbx.files.read(`/tmp/${prefix}-files/batch-a.txt`, { requestTimeoutMs }), 'a')
    await restoredSbx.kill({ requestTimeoutMs }).catch(() => {})
    restoredSbx = undefined
    assert.equal(await sbx.deleteSnapshot(snapshot.snapshotId, { requestTimeoutMs }), true)
    snapshots.pop()
    assert.equal(await Sandbox.deleteSnapshot(`${prefix}-missing-snapshot`, { requestTimeoutMs }), false)

    const connected = await Sandbox.connect(sbx.id, { requestTimeoutMs, timeoutMs: sandboxTimeoutMs })
    assert.equal(connected.id, sbx.id)
    assert.equal(await connected.commands.run('printf connected-ok', { requestTimeoutMs }).then((r) => r.stdout), 'connected-ok')
    assert.equal(await connected.resume({ requestTimeoutMs, timeoutMs: sandboxTimeoutMs }), true)

    codeSbx = await CodeInterpreterSandbox.create({
      team,
      timeoutMs: sandboxTimeoutMs,
      requestTimeoutMs,
      metadata: { smoke: prefix, sdk: 'typescript-code' },
    })
    const execution = await codeSbx.runCode("print('code-ok')", { requestTimeoutMs, timeoutMs: 30_000 })
    assert.equal(execution.error, undefined)
    assert.match(execution.logs.stdout.map(String).join(''), /code-ok/)
    const context = await codeSbx.createCodeContext({ requestTimeoutMs, language: 'python', cwd: '/tmp' })
    assert.ok((await codeSbx.listCodeContexts({ requestTimeoutMs })).some((item) => item.id === context.id))
    await codeSbx.restartCodeContext(context, { requestTimeoutMs })
    await codeSbx.removeCodeContext(context, { requestTimeoutMs })
  } finally {
    for (const snapshotId of snapshots.splice(0)) {
      await Sandbox.deleteSnapshot(snapshotId, { requestTimeoutMs }).catch(() => {})
    }
    if (restoredSbx) await restoredSbx.kill({ requestTimeoutMs }).catch(() => {})
    if (codeSbx) await codeSbx.kill({ requestTimeoutMs }).catch(() => {})
    if (sbx) await sbx.kill({ requestTimeoutMs }).catch(() => {})
    if (volume) await retryCleanup(() => volume.destroy({ requestTimeoutMs }))
  }

  assert.equal(await Volume.destroy(`${prefix}-missing-volume`, { requestTimeoutMs }), false)
})

async function exerciseFiles(sbx) {
  const dir = `/tmp/${prefix}-files`
  await sbx.files.makeDir(dir, { requestTimeoutMs })
  const events = []
  const watcher = await sbx.files.watchDir(dir, (event) => events.push(event), { requestTimeoutMs, includeEntry: true })
  try {
    assert.equal(await sbx.files.exists(`${dir}/missing.txt`, { requestTimeoutMs }), false)
    await sbx.files.write(`${dir}/hello.txt`, 'file-ok', { requestTimeoutMs })
    await sbx.files.writeBytes(`${dir}/bytes.bin`, new Uint8Array([4, 5, 6]), { requestTimeoutMs })
    await sbx.files.write([
      { path: `${dir}/batch-a.txt`, data: 'a' },
      { path: `${dir}/batch-b.txt`, data: new TextEncoder().encode('b') },
    ], { requestTimeoutMs })
    await waitEventually(
      async () => await sbx.files.read(`${dir}/hello.txt`, { requestTimeoutMs }) === 'file-ok',
      10_000,
      'text file read',
    )
    await waitEventually(
      async () => {
        const bytes = await sbx.files.readBytes(`${dir}/bytes.bin`, { requestTimeoutMs })
        return assert.deepEqual(Array.from(bytes), [4, 5, 6]) === undefined
      },
      10_000,
      'bytes file read',
    )
    assert.ok(await sbx.files.read(`${dir}/hello.txt`, { requestTimeoutMs, format: 'blob' }))
    const stream = await sbx.files.read(`${dir}/hello.txt`, { requestTimeoutMs, format: 'stream' })
    assert.ok(stream.getReader)
    await waitEventually(
      async () => (await sbx.files.getInfo(`${dir}/hello.txt`, { requestTimeoutMs })).type === 'file',
      10_000,
      'file info',
    )
    await waitEventually(
      async () => (await sbx.files.list(dir, { requestTimeoutMs, depth: 1 })).some((entry) => entry.name === 'hello.txt'),
      10_000,
      'directory listing',
    )
    await waitEventually(
      async () => await sbx.files.exists(`${dir}/hello.txt`, { requestTimeoutMs }) === true,
      10_000,
      'file exists',
    )
    const renamed = await sbx.files.rename(`${dir}/hello.txt`, `${dir}/renamed.txt`, { requestTimeoutMs })
    assert.equal(renamed.name, 'renamed.txt')
    await waitUntil(() => events.length > 0, 10_000, 'filesystem watch event')
  } finally {
    await watcher.stop()
    await watcher.wait().catch(() => {})
  }
  await sbx.files.remove(`${dir}/renamed.txt`, { requestTimeoutMs })
}

async function exerciseSignedFileUrls(sbx) {
  const path = `/tmp/${prefix}-signed.txt`
  const uploadInfo = await sbx.uploadUrlInfo(path, { requestTimeoutMs, expiresInSeconds: 120 })
  assert.equal(uploadInfo.method, 'POST')
  assert.equal(uploadInfo.path, path)
  assert.ok(uploadInfo.url.includes('/runtime/v1/files'))
  const uploadResponse = await fetch(uploadInfo.url, { method: uploadInfo.method, body: 'signed-ok' })
  assert.ok(uploadResponse.ok, `signed upload failed: ${uploadResponse.status}`)
  const downloadInfo = await sbx.downloadUrlInfo(path, { requestTimeoutMs, expiresInSeconds: 120 })
  assert.equal(downloadInfo.method, 'GET')
  assert.ok((await sbx.downloadUrl(path, { requestTimeoutMs })).includes('/runtime/v1/files'))
  const downloadResponse = await fetch(downloadInfo.url)
  assert.ok(downloadResponse.ok, `signed download failed: ${downloadResponse.status}`)
  assert.equal(await downloadResponse.text(), 'signed-ok')
  assert.ok((await sbx.uploadUrl(path, { requestTimeoutMs })).includes('/runtime/v1/files'))
}

async function exerciseCommands(sbx) {
  assert.equal(sbx.commands.supportsStdinClose, true)
  const run = await sbx.commands.run('printf command-ok', { requestTimeoutMs })
  assert.equal(run.stdout, 'command-ok')
  await assert.rejects(() => sbx.commands.run('echo fail >&2; exit 7', { requestTimeoutMs }), CommandExitError)

  const marker = `/tmp/${prefix}-commands-wrapper.txt`
  const wrapper = await sbx.commands.start(`cat > ${marker} && printf done > ${marker}.done`, { requestTimeoutMs, stdin: true, timeoutMs: 30_000 })
  try {
    await sbx.commands.sendStdin(wrapper.pid, 'wrapper-ok\n', { requestTimeoutMs })
    await sbx.commands.closeStdin(wrapper.pid, { requestTimeoutMs })
    await waitUntil(() => sbx.files.exists(`${marker}.done`, { requestTimeoutMs }), 10_000, 'commands static stdin close')
    assert.equal(await sbx.files.read(marker, { requestTimeoutMs }), 'wrapper-ok\n')
    const result = await waitCommand(wrapper, 30_000, 'commands static stdin wrapper')
    assert.equal(result.exitCode, 0)
    assert.equal(result.stdout, '')
  } finally {
    await cleanupHandle(wrapper)
  }

  const cat = await sbx.commands.start('cat', { requestTimeoutMs, stdin: true })
  await cat.sendStdin('stdin-ok\n')
  await cat.closeStdin()
  assert.equal((await waitCommand(cat, 30_000, 'commands direct stdin')).stdout, 'stdin-ok\n')

  const sleeper = await sbx.commands.run('sleep 60', { requestTimeoutMs, background: true, processID: `${prefix}-sleep` })
  assert.ok((await sbx.commands.list({ requestTimeoutMs })).some((item) => String(item.pid) === String(sleeper.pid)))
  const attached = await sbx.commands.connect(sleeper.pid, { requestTimeoutMs })
  await attached.disconnect()
  assert.equal(await sleeper.kill(), true)
  await cleanupHandle(sleeper)
}

async function exerciseProcessManager(sbx) {
  const output = await sbx.process.startAndWait({ cmd: 'printf process-ok', requestTimeoutMs })
  assert.equal(output.stdout, 'process-ok')
  const proc = await sbx.process.start({ cmd: 'read line; echo process:$line', stdin: true, requestTimeoutMs, timeoutMs: 30_000 })
  await proc.sendStdin('process-stdin-ok\n')
  assert.equal((await waitPromise(proc.wait(30_000), 'process manager stdin')).stdout, 'process:process-stdin-ok\n')
  const long = await sbx.process.start({ cmd: 'sleep 60', requestTimeoutMs })
  await long.kill()
  await waitPromise(long.wait(30_000), 'process manager kill').catch(() => {})
}

async function exercisePty(sbx) {
  const handle = await sbx.pty.create({ cmd: 'printf pty-ok', requestTimeoutMs, timeoutMs: 30_000 })
  assert.match((await waitCommand(handle, 30_000, 'pty quick command')).stdout, /pty-ok/)

  const long = await sbx.pty.create({ cmd: 'sleep 60', requestTimeoutMs, timeoutMs: 120_000 })
  await long.disconnect()
  const connected = await sbx.pty.connect(long.pid, { requestTimeoutMs })
  await connected.resize({ cols: 100, rows: 30 })
  await sbx.pty.sendInput(long.pid, 'ignored\n', { requestTimeoutMs }).catch(() => {})
  assert.equal(await sbx.pty.kill(long.pid, { requestTimeoutMs }), true)
  await cleanupHandle(connected)
}

async function exerciseTerminal(sbx) {
  const terminal = await sbx.terminal.start({ cmd: 'printf terminal-ok', requestTimeoutMs, timeoutMs: 30_000 })
  assert.match((await terminal.wait()).data, /terminal-ok/)
  const interactive = await sbx.terminal.start({ cmd: 'read line; echo terminal:$line', requestTimeoutMs, timeoutMs: 30_000 })
  await interactive.resize({ cols: 90, rows: 25 })
  await interactive.sendData('input-ok\n')
  assert.match((await interactive.wait()).data, /terminal:input-ok/)
  const long = await sbx.terminal.start({ cmd: 'sleep 60', requestTimeoutMs, timeoutMs: 120_000 })
  await long.kill()
  await waitPromise(long.wait(), 'terminal kill').catch(() => {})
}

async function exerciseGit(sbx) {
  const repo = `/tmp/${prefix}-repo`
  const remote = `/tmp/${prefix}-remote.git`
  const clone = `/tmp/${prefix}-clone`
  await sbx.commands.run(`rm -rf ${repo} ${remote} ${clone}`, { requestTimeoutMs })
  await sbx.commands.run(`git init --bare ${remote}`, { requestTimeoutMs })
  await sbx.git.dangerouslyAuthenticate({ username: 'user', password: 'token', host: 'example.test', protocol: 'https', requestTimeoutMs })
  await sbx.git.init(repo, { initialBranch: 'main', requestTimeoutMs })
  await sbx.git.configureUser('Watasu Smoke', 'smoke@watasu.io', { path: repo, scope: 'local', requestTimeoutMs })
  await sbx.git.setConfig('smoke.key', 'smoke-value', { path: repo, scope: 'local', requestTimeoutMs })
  assert.equal(await sbx.git.getConfig('smoke.key', { path: repo, scope: 'local', requestTimeoutMs }), 'smoke-value')
  await sbx.commands.run(`printf one > ${repo}/file.txt`, { requestTimeoutMs })
  assert.equal((await sbx.git.status(repo, { requestTimeoutMs })).hasUntracked, true)
  await sbx.git.add(repo, { files: ['file.txt'], requestTimeoutMs })
  await sbx.git.commit(repo, 'initial', { requestTimeoutMs })
  assert.equal((await sbx.git.status(repo, { requestTimeoutMs })).isClean, true)
  await sbx.git.createBranch(repo, 'feature', { requestTimeoutMs })
  assert.equal((await sbx.git.branches(repo, { requestTimeoutMs })).currentBranch, 'feature')
  await sbx.git.checkoutBranch(repo, 'main', { requestTimeoutMs })
  await sbx.git.deleteBranch(repo, 'feature', { force: true, requestTimeoutMs })
  await sbx.git.remoteAdd(repo, 'origin', remote, { requestTimeoutMs, overwrite: true })
  assert.equal(await sbx.git.remoteGet(repo, 'origin', { requestTimeoutMs }), remote)
  await sbx.git.push(repo, { remote: 'origin', branch: 'main', requestTimeoutMs })
  await sbx.git.clone(remote, { path: clone, branch: 'main', requestTimeoutMs })
  await sbx.commands.run(`printf two > ${repo}/file.txt`, { requestTimeoutMs })
  await sbx.git.add(repo, { all: true, requestTimeoutMs })
  await sbx.git.commit(repo, 'second', { requestTimeoutMs })
  await sbx.git.push(repo, { remote: 'origin', branch: 'main', requestTimeoutMs })
  await sbx.git.pull(clone, { remote: 'origin', branch: 'main', requestTimeoutMs })
  assert.equal(await sbx.files.read(`${clone}/file.txt`, { requestTimeoutMs }), 'two')
  await sbx.commands.run(`printf dirty > ${repo}/file.txt`, { requestTimeoutMs })
  await sbx.git.restore(repo, { paths: ['file.txt'], worktree: true, requestTimeoutMs })
  assert.equal(await sbx.files.read(`${repo}/file.txt`, { requestTimeoutMs }), 'two')
  await sbx.commands.run(`printf staged > ${repo}/staged.txt`, { requestTimeoutMs })
  await sbx.git.add(repo, { files: ['staged.txt'], requestTimeoutMs })
  await sbx.git.reset(repo, { paths: ['staged.txt'], requestTimeoutMs })
  assert.equal((await sbx.git.status(repo, { requestTimeoutMs })).hasUntracked, true)
  await sbx.git.checkout(repo, 'main', { requestTimeoutMs })
}

async function waitUntil(check, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await check()) return
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  throw new Error(`timed out waiting for ${label}`)
}

async function waitEventually(check, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs
  let lastInvalidArgument
  while (Date.now() < deadline) {
    try {
      if (await check()) return
    } catch (error) {
      if (!(error instanceof InvalidArgumentError)) throw error
      lastInvalidArgument = error
    }
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  if (lastInvalidArgument) throw labelError(lastInvalidArgument, label)
  throw new Error(`timed out waiting for ${label}`)
}

async function waitCommand(handle, timeoutMs, label) {
  try {
    return await handle.wait(timeoutMs)
  } catch (error) {
    await cleanupHandle(handle)
    throw labelError(error, label)
  }
}

async function waitPromise(promise, label) {
  try {
    return await promise
  } catch (error) {
    throw labelError(error, label)
  }
}

async function cleanupHandle(handle) {
  try {
    if (typeof handle.kill === 'function') await handle.kill()
  } catch {}
  try {
    if (typeof handle.disconnect === 'function') await handle.disconnect()
  } catch {}
}

async function retryCleanup(action, attempts = 8, delayMs = 2_000) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      if (await action() !== false) return
    } catch {}
    if (attempt + 1 < attempts) await new Promise((resolve) => setTimeout(resolve, delayMs))
  }
}

function labelError(error, label) {
  if (error instanceof Error) {
    error.message = `${label}: ${error.message}`
    return error
  }
  return new Error(`${label}: ${String(error)}`)
}
