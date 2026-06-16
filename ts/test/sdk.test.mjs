import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { gunzipSync } from 'node:zlib'

import {
  ALL_TRAFFIC,
  ApiClient,
  BuildError,
  Commands,
  CommandExitError,
  CommandHandle,
  ConnectionConfig,
  FileNotFoundError,
  FileUploadError,
  Filesystem,
  FilesystemEventType,
  FilesystemWatcher,
  GitAuthError,
  GitUpstreamError,
  LogEntry,
  LogEntryEnd,
  LogEntryStart,
  ProcessManager,
  ProcessOutput,
  ProcessSocket,
  Pty,
  Sandbox,
  SandboxError,
  SandboxNotFoundError,
  SandboxPaginator,
  SnapshotPaginator,
  WatchHandle,
  ReadyCmd,
  Template,
  TerminalManager,
  TemplateError,
  Volume,
  VolumeConnectionConfig,
  VolumeError,
  VolumeFileType,
  base64DecodeText,
  base64Encode,
  defaultBuildLogger,
  getSignature,
  waitForFile,
  waitForPort,
  waitForProcess,
  waitForTimeout,
  waitForURL,
} from '../dist/index.js'
import DefaultSandbox from '../dist/index.js'
import { errorFromResponse } from '../dist/errors.js'
import {
  ConnectionConfig as CodeInterpreterConnectionConfig,
  ChartType,
  Context as CodeInterpreterContext,
  Pty as CodeInterpreterPty,
  Result as CodeInterpreterResult,
  Sandbox as CodeInterpreterSandbox,
  ScaleType,
  Template as CodeInterpreterTemplate,
} from '../dist/codeInterpreter.js'
import DefaultCodeInterpreterSandbox from '../dist/codeInterpreter.js'

test('connection config defaults to Watasu hosts', () => {
  const config = new ConnectionConfig({ apiKey: 'key' })
  assert.equal(config.apiUrl, 'https://api.watasu.io/v1')
  assert.equal(config.dataPlaneDomain, 'watasuhost.com')
  assert.equal(config.authHeaders.Authorization, 'Bearer key')
  assert.equal(config.accessToken, 'key')
  assert.equal(ConnectionConfig.envdPort, 49983)
})

test('connection config exposes access token logger and proxy options', () => {
  const logger = { debug() {}, info() {}, warn() {}, error() {} }
  const config = new ConnectionConfig({
    accessToken: 'token',
    logger,
    proxy: 'http://127.0.0.1:8080',
  })

  assert.equal(config.apiKey, 'token')
  assert.equal(config.accessToken, 'token')
  assert.equal(config.logger, logger)
  assert.equal(config.proxy, 'http://127.0.0.1:8080')
  assert.equal(config.authHeaders.Authorization, 'Bearer token')
})

test('core package exposes reference-compatible runtime symbols', async () => {
  assert.equal(ALL_TRAFFIC, '0.0.0.0/0')
  assert.equal(DefaultSandbox, Sandbox)
  assert.equal(FilesystemEventType.WRITE, 'write')
  assert.equal(VolumeFileType.FILE, 'file')
  assert.notEqual(VolumeConnectionConfig, ConnectionConfig)
  assert.equal(typeof ApiClient, 'function')
  assert.ok(new BuildError('x') instanceof Error)
  assert.ok(new FileUploadError('x') instanceof BuildError)
  assert.ok(new GitAuthError('x') instanceof Error)
  assert.ok(new GitUpstreamError('x') instanceof SandboxError)
  assert.ok(new SandboxNotFoundError('x') instanceof Error)
  assert.ok(new TemplateError('x') instanceof SandboxError)
  assert.ok(new VolumeError('x') instanceof Error)

  const volume = new Volume('vol-1', 'cache', 'volume-token', 'watasu.io', false, 'http://127.0.0.1:8080')
  const volumeConfig = new VolumeConnectionConfig(volume, { apiKey: 'key' })
  assert.equal(volume.id, 'vol-1')
  assert.equal(volume.name, 'cache')
  assert.equal(volume.token, 'volume-token')
  assert.equal(volume.domain, 'watasu.io')
  assert.equal(volume.proxy, 'http://127.0.0.1:8080')
  assert.equal(volumeConfig.token, 'volume-token')
  assert.equal(volumeConfig.apiKey, 'key')

  const entry = new LogEntry(new Date('2026-06-16T00:00:00Z'), 'info', 'ready')
  assert.equal(entry.toString(), '[2026-06-16T00:00:00.000Z] info: ready')
  assert.ok(new LogEntryStart() instanceof LogEntry)
  assert.ok(new LogEntryEnd() instanceof LogEntry)
  assert.equal(typeof defaultBuildLogger({ minLevel: 'error' }), 'function')

  const signature = await getSignature({
    path: '/workspace/a.txt',
    operation: 'read',
    user: 'user',
    envdAccessToken: 'token',
  })
  assert.equal(signature.expiration, null)
  assert.match(signature.signature, /^v1_[A-Za-z0-9+/]+$/)
})

test('code interpreter models expose chart enums and raw result payloads', () => {
  assert.equal(ChartType.BAR, 'bar')
  assert.equal(ScaleType.LINEAR, 'linear')

  const raw = {
    text: 'summary',
    chart: {
      type: ChartType.BAR,
      title: 'Builds',
      elements: [{ label: 'Mon', value: '4', group: 'week' }],
    },
    extra: { 'application/vnd.custom': { ok: true } },
    is_main_result: true,
  }
  const result = new CodeInterpreterResult(raw)

  assert.equal(result.raw, raw)
  assert.equal(result.text, 'summary')
  assert.equal(result.chart.type, ChartType.BAR)
  assert.equal(result.isMainResult, true)
  assert.deepEqual(result.formats(), ['text', 'chart', 'application/vnd.custom'])
})

test('connection config accepts access token alias', () => {
  const config = new ConnectionConfig({ accessToken: 'alias-key' })
  assert.equal(config.authHeaders.Authorization, 'Bearer alias-key')
})

test('connection config keeps API headers and caller signal', () => {
  const controller = new AbortController()
  const config = new ConnectionConfig({
    apiKey: 'key',
    sandboxUrl: 'http://localhost:49983',
    headers: { 'x-shared': 'shared' },
    apiHeaders: { 'x-api': 'api' },
    debug: true,
    signal: controller.signal,
  })

  assert.equal(config.authHeaders.Authorization, 'Bearer key')
  assert.equal(config.authHeaders['x-shared'], 'shared')
  assert.equal(config.authHeaders['x-api'], 'api')
  assert.equal(config.sandboxUrl, 'http://localhost:49983')
  assert.equal(config.debug, true)
  assert.equal(config.signal, controller.signal)
})

test('connection config exposes sandbox URL helpers and abort signals', async () => {
  const config = new ConnectionConfig({ apiKey: 'key', requestTimeoutMs: 100 })
  assert.equal(config.getHost('route-token', 3000), 'p3000-route-token.sandbox.watasuhost.com')
  assert.equal(
    config.getSandboxUrl('route-token', { sandboxDomain: 'watasuhost.com', envdPort: 49983 }),
    'https://route-token.sandbox.watasuhost.com'
  )
  assert.equal(
    new ConnectionConfig({ apiKey: 'key', sandboxUrl: 'http://localhost:49983' }).getSandboxDirectUrl('route-token', { sandboxDomain: 'watasuhost.com', envdPort: 49983 }),
    'http://localhost:49983'
  )

  const timeoutSignal = config.getSignal(5)
  assert.equal(timeoutSignal.aborted, false)
  await new Promise((resolve) => setTimeout(resolve, 10))
  assert.equal(timeoutSignal.aborted, true)

  const controller = new AbortController()
  const callerSignal = config.getSignal(1000, controller.signal)
  controller.abort()
  assert.equal(callerSignal.aborted, true)
})

test('stream frame helpers match runtime base64 protocol', () => {
  assert.equal(base64DecodeText('NAo='), '4\n')
  assert.equal(base64Encode(new TextEncoder().encode('hi\n')), 'aGkK')
})

test('process socket stdin waits for websocket send completion and runtime ack', async () => {
  const socket = new ProcessSocket('http://localhost:49983', 'token', '/runtime/v1/process')
  let callback
  let sent
  socket.ws = {
    readyState: 1,
    send(payload, done) {
      sent = JSON.parse(payload)
      callback = done
    },
  }

  let settled = false
  const pending = socket.sendStdin('hi\n').then(() => {
    settled = true
  })
  await Promise.resolve()

  assert.equal(settled, false)
  assert.deepEqual(sent, { type: 'stdin', data: 'aGkK' })

  callback()
  await Promise.resolve()
  assert.equal(settled, false)

  socket.onMessage(JSON.stringify({ type: 'stdin_ack', pid: '123' }))
  await pending
  assert.equal(settled, true)
})

test('error mapper preserves explicit file not found code', () => {
  assert.ok(errorFromResponse(404, { error: 'file_not_found' }) instanceof FileNotFoundError)
})

test('command handle raises on non-zero exit and preserves output', async () => {
  async function* frames() {
    yield { type: 'stdout', data: 'YmVmb3JlCg==' }
    yield { type: 'stderr', data: 'YmFkCg==' }
    yield { type: 'exit', exit_code: 7 }
  }
  const socket = { close() {} }
  const handle = new CommandHandle(123, socket, async () => true, frames())

  await assert.rejects(handle.wait(), (error) => {
    assert.ok(error instanceof CommandExitError)
    assert.equal(error.exitCode, 7)
    assert.equal(error.stdout, 'before\n')
    assert.equal(error.stderr, 'bad\n')
    return true
  })
})

test('command handle closes stream after terminal exit frame', async () => {
  async function* frames() {
    yield { type: 'stdout', data: 'b2sK' }
    yield { type: 'exit', exit_code: 0 }
  }
  let closeCount = 0
  const socket = { close() { closeCount += 1 } }
  const handle = new CommandHandle(123, socket, async () => true, frames())

  const result = await handle.wait()

  assert.equal(result.stdout, 'ok\n')
  assert.equal(result.exitCode, 0)
  assert.equal(closeCount, 1)
})

test('command handle treats pty frames as terminal output', async () => {
  async function* frames() {
    yield { type: 'pty', data: 'dGVybQo=' }
    yield { type: 'exit', exit_code: 0 }
  }
  const seen = []
  const socket = { close() {} }
  const handle = new CommandHandle(123, socket, async () => true, frames(), undefined, undefined, (bytes) => {
    seen.push(new TextDecoder().decode(bytes))
  })

  const result = await handle.wait()

  assert.equal(result.stdout, 'term\n')
  assert.deepEqual(seen, ['term\n'])
})

test('terminal manager preserves captured handle stdout', async () => {
  async function* frames() {
    yield { type: 'stdout', data: base64Encode('terminal-ok') }
    yield { type: 'exit', exit_code: 0 }
  }
  const socket = { close() {} }
  const pty = {
    create: async () => new CommandHandle(123, socket, async () => true, frames()),
  }

  const terminal = await new TerminalManager(pty).start({ cmd: 'printf terminal-ok' })

  assert.equal((await terminal.wait()).data, 'terminal-ok')
})

test('command handle can close stdin without disconnecting', async () => {
  const sent = []
  let closeCount = 0
  const socket = {
    close() {
      closeCount += 1
    },
    closeStdin() {
      sent.push({ type: 'close_stdin' })
    },
  }
  const handle = new CommandHandle(123, socket, async () => true, (async function* () { await new Promise(() => {}) })())
  const commands = new Commands({}, new ConnectionConfig({ apiKey: 'key' }))

  await handle.closeStdin()
  await handle.disconnect()

  assert.equal(commands.supportsStdinClose, true)
  assert.equal(closeCount, 1)
  assert.deepEqual(sent, [{ type: 'close_stdin' }])
})

test('commands list prefers stable process id over guest os pid', async () => {
  const commands = new Commands({
    getJson(path) {
      assert.equal(path, '/runtime/v1/process')
      return Promise.resolve({
        processes: [
          {
            id: 'proc-123',
            pid: 456,
            command: 'bash',
            args: ['-lc', 'sleep 60'],
            cwd: '/workspace',
          },
        ],
      })
    },
  }, new ConnectionConfig({ apiKey: 'key' }))

  const processes = await commands.list()

  assert.equal(processes[0].pid, 'proc-123')
  assert.equal(processes[0].cmd, 'bash')
  assert.deepEqual(processes[0].args, ['-lc', 'sleep 60'])
  assert.equal(processes[0].cwd, '/workspace')
})

test('pty kill forwards per-call request options', async () => {
  const controller = new AbortController()
  const calls = []
  const pty = new Pty({
    postJson(path, opts) {
      calls.push([path, opts])
      return Promise.resolve({})
    },
  }, new ConnectionConfig({ apiKey: 'key' }))

  assert.equal(await pty.kill(123, { requestTimeoutMs: 5, signal: controller.signal }), true)
  assert.deepEqual(calls, [
    ['/runtime/v1/process/123/signal', {
      json: { signal: 'SIGKILL' },
      requestTimeoutMs: 5,
      signal: controller.signal,
    }],
  ])
})

test('sandbox construction requires a session', () => {
  assert.throws(
    () =>
      new Sandbox({
        sandboxId: '1',
        connectionConfig: new ConnectionConfig({ apiKey: 'key' }),
        session: undefined,
      }),
    /sandbox session is required/
  )
})

test('sandbox getHost is sync', () => {
  const sbx = new Sandbox({
    sandboxId: '1',
    connectionConfig: new ConnectionConfig({ apiKey: 'key' }),
    session: { data_plane_url: 'https://route.sandbox.watasuhost.com', token: 'data' },
    sandbox: { route_token: 'route-token' },
  })

  assert.equal(sbx.getHost(3000), 'p3000-route-token.sandbox.watasuhost.com')
})

test('sandbox getHost accepts camel-case route token', () => {
  const sbx = new Sandbox({
    sandboxId: '1',
    connectionConfig: new ConnectionConfig({ apiKey: 'key' }),
    session: { data_plane_url: 'https://route.sandbox.watasuhost.com', token: 'data' },
    sandbox: { routeToken: 'camel-token' },
  })

  assert.equal(sbx.getHost(3000), 'p3000-camel-token.sandbox.watasuhost.com')
})

test('sandbox getHost derives route token from data-plane URL', () => {
  const sbx = new Sandbox({
    sandboxId: '1',
    connectionConfig: new ConnectionConfig({ apiKey: 'key' }),
    session: { data_plane_url: 'https://derived-token.sandbox.watasuhost.com', token: 'data' },
    sandbox: {},
  })

  assert.equal(sbx.getHost(3000), 'p3000-derived-token.sandbox.watasuhost.com')
})

test('sandboxUrl overrides the data-plane URL without changing public hosts', () => {
  const sbx = new Sandbox({
    sandboxId: '1',
    connectionConfig: new ConnectionConfig({
      apiKey: 'key',
      sandboxUrl: 'http://localhost:49983',
    }),
    session: { data_plane_url: 'https://remote-token.sandbox.watasuhost.com', token: 'data' },
    sandbox: { route_token: 'remote-token' },
  })

  assert.equal(sbx.getHost(3000), 'p3000-remote-token.sandbox.watasuhost.com')
})

test('sandbox exposes helper modules without destroying local state', () => {
  const sbx = new Sandbox({
    sandboxId: '1',
    connectionConfig: new ConnectionConfig({ apiKey: 'key' }),
    session: { data_plane_url: 'https://derived-token.sandbox.watasuhost.com', token: 'data' },
    sandbox: { route_token: 'derived-token' },
  })

  assert.equal(sbx.id, '1')
  assert.equal(typeof sbx.process.start, 'function')
  assert.equal(typeof sbx.terminal.start, 'function')
  assert.equal('checkpoint' in sbx, false)
  assert.equal(sbx.getHost(8080), 'p8080-derived-token.sandbox.watasuhost.com')
})

test('process output preserves stdout stderr and exit code', () => {
  const output = new ProcessOutput()

  output.addStdout({ line: 'ok\n', timestamp: 1, error: false, toString() { return this.line } })
  output.addStderr({ line: 'bad\n', timestamp: 2, error: true, toString() { return this.line } })
  output.setExitCode(7)

  assert.equal(output.stdout, 'ok\n')
  assert.equal(output.stderr, 'bad\n')
  assert.equal(output.exitCode, 7)
  assert.equal(output.error, true)
})

test('process output keeps streamed output when terminal result is empty', () => {
  const output = new ProcessOutput()

  output.addStdout({ line: 'streamed-out', timestamp: 1, error: false, toString() { return this.line } })
  output.addStderr({ line: 'streamed-err', timestamp: 2, error: true, toString() { return this.line } })
  output.replace({ stdout: '', stderr: '', exitCode: 0 })

  assert.equal(output.stdout, 'streamed-out')
  assert.equal(output.stderr, 'streamed-err')
  assert.equal(output.exitCode, 0)
})

test('process manager keeps options-form cmd as a shell command', async () => {
  const calls = []
  const manager = new ProcessManager({
    start(cmd, opts) {
      calls.push([cmd, opts])
      return Promise.resolve({
        pid: 'pid-1',
        wait() {
          return Promise.resolve({ exitCode: 0, stdout: 'ok\n', stderr: '' })
        },
        kill() {
          return Promise.resolve(true)
        },
        sendStdin() {},
      })
    },
  })

  const output = await manager.startAndWait({ cmd: 'echo ok', timeout: 1_000 })

  assert.equal(output.stdout, 'ok\n')
  assert.equal(calls.length, 1)
  assert.equal(calls[0][0], 'echo ok')
  assert.equal(calls[0][1].cmd, undefined)
  assert.equal(calls[0][1].args, undefined)
  assert.equal(calls[0][1].timeout, 1_000)
})

test('filesystem writeFiles uses snake_case batch route', async () => {
  const calls = []
  const fs = new Filesystem({
    postJson(path, opts) {
      calls.push([path, opts])
      return Promise.resolve({
        files: opts.json.files.map((file) => ({
          path: file.path,
          name: file.path.split('/').pop(),
          type: 'file',
          bytes: 3,
        })),
      })
    },
  })

  const written = await fs.writeFiles([
    { path: '/tmp/a.txt', data: 'abc' },
    { path: '/tmp/b.bin', data: new Uint8Array([0, 1, 2]) },
  ])

  assert.equal(written[0].path, '/tmp/a.txt')
  assert.deepEqual(calls, [
    ['/runtime/v1/files/write_files', {
      json: {
        files: [
          { path: '/tmp/a.txt', data_base64: 'YWJj' },
          { path: '/tmp/b.bin', data_base64: 'AAEC' },
        ],
      },
    }],
  ])
})

test('filesystem write overload accepts batches and browser data objects', async () => {
  const calls = []
  const fs = new Filesystem({
    putJson(path, body, opts) {
      calls.push(['put', path, Array.from(body), opts])
      return Promise.resolve({ file: { path: '/tmp/a.txt', name: 'a.txt', type: 'file', bytes: body.byteLength } })
    },
    postJson(path, opts) {
      calls.push(['post', path, opts])
      return Promise.resolve({
        files: opts.json.files.map((file) => ({
          path: file.path,
          name: file.path.split('/').pop(),
          type: 'file',
          bytes: 3,
        })),
      })
    },
  })

  const blobWritten = await fs.write('/tmp/a.txt', new Blob(['abc']))
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('xyz'))
      controller.close()
    },
  })
  const batchWritten = await fs.write([
    { path: '/tmp/b.txt', data: stream },
    { path: '/tmp/c.bin', data: new Uint8Array([0, 1, 2]).buffer },
  ])

  assert.equal(blobWritten.size, 3)
  assert.deepEqual(batchWritten.map((item) => item.path), ['/tmp/b.txt', '/tmp/c.bin'])
  assert.deepEqual(calls, [
    ['put', '/runtime/v1/files?path=%2Ftmp%2Fa.txt', [97, 98, 99], {}],
    ['post', '/runtime/v1/files/write_files', {
      json: {
        files: [
          { path: '/tmp/b.txt', data_base64: 'eHl6' },
          { path: '/tmp/c.bin', data_base64: 'AAEC' },
        ],
      },
    }],
  ])
})

test('filesystem gzip writes mark compressed upload payloads', async () => {
  const calls = []
  const fs = new Filesystem({
    putJson(path, body, opts) {
      calls.push(['put', path, body, opts])
      return Promise.resolve({ file: { path: '/tmp/a.txt', name: 'a.txt', type: 'file', bytes: 3 } })
    },
    postJson(path, opts) {
      calls.push(['post', path, opts])
      return Promise.resolve({
        files: opts.json.files.map((file) => ({
          path: file.path,
          name: file.path.split('/').pop(),
          type: 'file',
          bytes: 3,
        })),
      })
    },
  })

  await fs.write('/tmp/a.txt', 'abc', { gzip: true })
  await fs.writeFiles([{ path: '/tmp/b.txt', data: 'xyz' }], { gzip: true })

  assert.equal(calls[0][1], '/runtime/v1/files?path=%2Ftmp%2Fa.txt&gzip=true')
  assert.equal(calls[0][3].headers['content-encoding'], 'gzip')
  assert.equal(gunzipSync(calls[0][2]).toString('utf8'), 'abc')
  assert.equal(calls[1][1], '/runtime/v1/files/write_files')
  assert.equal(calls[1][2].json.files[0].gzip, true)
  assert.equal(gunzipSync(Buffer.from(calls[1][2].json.files[0].data_base64, 'base64')).toString('utf8'), 'xyz')
})

test('filesystem read supports blob and readable stream formats', async () => {
  const fs = new Filesystem({
    getBytes(path, opts) {
      assert.equal(path, '/runtime/v1/files?path=%2Ftmp%2Fa.txt')
      assert.equal(opts.format, 'blob')
      return Promise.resolve(new TextEncoder().encode('abc'))
    },
  })

  const blob = await fs.read('/tmp/a.txt', { format: 'blob' })
  assert.equal(await blob.text(), 'abc')

  const streamFs = new Filesystem({
    getBytes() {
      return Promise.resolve(new TextEncoder().encode('xyz'))
    },
  })
  const stream = await streamFs.read('/tmp/a.txt', { format: 'stream' })
  const reader = stream.getReader()
  const first = await reader.read()
  const second = await reader.read()

  assert.equal(new TextDecoder().decode(first.value), 'xyz')
  assert.equal(first.done, false)
  assert.equal(second.done, true)
})

test('filesystem watchDir can return a lazy watcher', () => {
  const fs = new Filesystem({})
  const watcher = fs.watchDir('/tmp')

  assert.ok(watcher instanceof FilesystemWatcher)
  assert.equal(typeof watcher.addEventListener, 'function')
  assert.equal(typeof watcher.start, 'function')
})

test('sandbox create uses root snake_case API payload', async () => {
  const originalFetch = globalThis.fetch
  const requests = []
  try {
    globalThis.fetch = async (url, init) => {
      requests.push({ url: String(url), body: JSON.parse(init.body) })
      return new Response(
        JSON.stringify({
          sandbox: { id: 'created', template_id: 'base' },
          session: { data_plane_url: 'https://route.sandbox.watasuhost.com', token: 'data' },
        }),
        { status: 201, headers: { 'content-type': 'application/json' } }
      )
    }

    const volume = new Volume({
      volumeId: 'models-id',
      name: 'models',
      connectionConfig: new ConnectionConfig({ apiKey: 'key' }),
    })
    const sbx = await Sandbox.create('base:82', {
      apiKey: 'key',
      timeoutMs: 120_000,
      envs: { HELLO: 'world' },
      metadata: { purpose: 'compat' },
      lifecycle: { onTimeout: 'pause', autoResume: true },
      volumeMounts: {
        '/workspace/cache': 'cache',
        '/data/models': volume,
      },
      team: 'watasu',
      network: {
        allowOut: ({ rules }) => [...rules.keys(), 'pypi.org:443'],
        denyOut: ['10.0.0.0/8'],
        allowPackageRegistryAccess: true,
        rules: {
          'api.example.com': [
            { transform: { headers: { authorization: 'Bearer token' } } },
          ],
        },
        maskRequestHost: '${PORT}-sandbox.example.com',
      },
    })

    assert.equal(sbx.sandboxId, 'created')
    assert.deepEqual(requests[0].body, {
      template_id: 'base:82',
      timeout: 120,
      metadata: { purpose: 'compat' },
      env_vars: { HELLO: 'world' },
      secure: true,
      allow_internet_access: true,
      lifecycle: { on_timeout: 'pause', auto_resume: true },
      volume_mounts: [
        { path: '/workspace/cache', name: 'cache' },
        { path: '/data/models', name: 'models' },
      ],
      allow_out: ['api.example.com', 'pypi.org:443'],
      deny_out: ['10.0.0.0/8'],
      allow_package_registry_access: true,
      rules: {
        'api.example.com': [
          { transform: { headers: { authorization: 'Bearer token' } } },
        ],
      },
      mask_request_host: '${PORT}-sandbox.example.com',
      team: 'watasu',
    })
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('sandbox protected create and connect helpers expose session details', async () => {
  class InspectableSandbox extends Sandbox {
    static createRaw(template, timeoutMs, opts) {
      return this.createSandbox(template, timeoutMs, opts)
    }

    static connectRaw(sandboxId, opts) {
      return this.connectSandbox(sandboxId, opts)
    }
  }

  const originalFetch = globalThis.fetch
  const requests = []
  try {
    globalThis.fetch = async (url, init = {}) => {
      const method = init.method ?? 'GET'
      const body = init.body === undefined ? undefined : JSON.parse(init.body)
      requests.push({ url: String(url), method, body })

      if (method === 'POST' && String(url).endsWith('/sandboxes')) {
        return new Response(
          JSON.stringify({
            sandbox: { id: 'created', template_id: 'base', envd_version: '0.6.3' },
            session: {
              data_plane_url: 'https://route.sandbox.watasuhost.com',
              token: 'data-token',
              sandbox_domain: 'sandbox.watasuhost.com',
              traffic_access_token: 'traffic-token',
            },
          }),
          { status: 201, headers: { 'content-type': 'application/json' } }
        )
      }

      if (method === 'GET' && String(url).endsWith('/sandboxes/existing')) {
        return new Response(
          JSON.stringify({ sandbox: { id: 'existing', envd_version: '0.6.3' } }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      }

      return new Response(
        JSON.stringify({
          sandbox: { id: 'existing', envd_version: '0.6.4' },
          session: {
            data_plane_url: 'https://existing.sandbox.watasuhost.com',
            token: 'existing-token',
            sandbox_domain: 'sandbox.watasuhost.com',
          },
        }),
        { status: 200, headers: { 'content-type': 'application/json' } }
      )
    }

    const created = await InspectableSandbox.createRaw('base', 60_000, {
      apiKey: 'key',
      envs: { HELLO: 'world' },
      team: 'watasu',
    })
    const connected = await InspectableSandbox.connectRaw('existing', {
      apiKey: 'key',
      timeoutMs: 90_000,
    })

    assert.equal(created.sandboxId, 'created')
    assert.equal(created.sandboxDomain, 'sandbox.watasuhost.com')
    assert.equal(created.envdVersion, '0.6.3')
    assert.equal(created.envdAccessToken, 'data-token')
    assert.equal(created.trafficAccessToken, 'traffic-token')
    assert.equal(connected.sandboxId, 'existing')
    assert.equal(connected.envdVersion, '0.6.4')
    assert.equal(connected.envdAccessToken, 'existing-token')
    assert.deepEqual(requests, [
      {
        url: 'https://api.watasu.io/v1/sandboxes',
        method: 'POST',
        body: {
          timeout: 60,
          metadata: {},
          env_vars: { HELLO: 'world' },
          secure: true,
          allow_internet_access: true,
          template_id: 'base',
          team: 'watasu',
        },
      },
      { url: 'https://api.watasu.io/v1/sandboxes/existing', method: 'GET', body: undefined },
      {
        url: 'https://api.watasu.io/v1/sandboxes/existing/resume',
        method: 'POST',
        body: { timeout: 90 },
      },
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('sandbox getFullInfo aliases getInfo', async () => {
  const originalFetch = globalThis.fetch
  const requests = []
  try {
    globalThis.fetch = async (url, init = {}) => {
      requests.push({ url: String(url), method: init.method })
      return new Response(
        JSON.stringify({
          sandbox: {
            id: 'full-info',
            sandbox_id: 'full-info',
            template_id: 'base',
            state: 'ready',
            metadata: { purpose: 'test' },
          },
        }),
        { status: 200, headers: { 'content-type': 'application/json' } }
      )
    }

    const info = await Sandbox.getFullInfo('full-info', { apiKey: 'key' })

    assert.equal(info.sandboxId, 'full-info')
    assert.deepEqual(requests, [
      { url: 'https://api.watasu.io/v1/sandboxes/full-info', method: 'GET' },
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('sandbox create rejects lifecycle autoResume without pause timeout', async () => {
  await assert.rejects(
    Sandbox.create({
      apiKey: 'key',
      lifecycle: { onTimeout: 'kill', autoResume: true },
    }),
    /autoResume/
  )
})

test('volume helper uses control API paths and snake_case payloads', async () => {
  const originalFetch = globalThis.fetch
  const requests = []
  try {
    globalThis.fetch = async (url, init = {}) => {
      requests.push({
        url: String(url),
        method: init.method,
        body: init.body ? JSON.parse(init.body) : undefined,
      })

      const parsedUrl = new URL(String(url))
      if (parsedUrl.pathname === '/v1/volumes' && init.method === 'POST') {
        return new Response(
          JSON.stringify({
            volume: {
              id: 42,
              name: 'cache',
              token: 'wvol_secret',
              state: 'ready',
              size_mb: 10240,
              metadata: { purpose: 'tests' },
            },
          }),
          { status: 201, headers: { 'content-type': 'application/json' } }
        )
      }
      if (parsedUrl.pathname === '/v1/volumes/42/files' && init.method === 'PUT') {
        return new Response(
          JSON.stringify({
            file: { path: '/workspace/a.txt', name: 'a.txt', type: 'file', bytes: 5 },
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      }
      if (parsedUrl.pathname === '/v1/volumes/42/files' && init.method === 'GET') {
        return new Response(
          JSON.stringify({ file: { path: '/workspace/a.txt', content_b64: 'aGVsbG8=' } }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      }
      if (parsedUrl.pathname === '/v1/volumes/42/directories' && init.method === 'GET') {
        return new Response(
          JSON.stringify({
            entries: [{ path: '/workspace/a.txt', name: 'a.txt', type: 'file', bytes: 5 }],
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      }
      if (parsedUrl.pathname === '/v1/volumes/42/path' && init.method === 'DELETE') {
        return new Response(JSON.stringify({ status: { status: 'deleted' } }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }
      if (parsedUrl.pathname === '/v1/volumes/42' && init.method === 'DELETE') {
        return new Response(JSON.stringify({ deleted: true }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }
      throw new Error(`unexpected request ${init.method} ${url}`)
    }

    const volume = await Volume.create('cache', {
      apiKey: 'key',
      team: 'watasu',
      domain: 'watasu.io',
      debug: false,
      proxy: 'http://127.0.0.1:8080',
    })
    assert.ok(volume instanceof Volume)
    assert.equal(volume.id, '42')
    assert.equal(volume.name, 'cache')
    assert.equal(volume.token, 'wvol_secret')
    assert.equal(volume.domain, 'watasu.io')
    assert.equal(volume.debug, false)
    assert.equal(volume.proxy, 'http://127.0.0.1:8080')

    const written = await volume.writeFile('/workspace/a.txt', 'hello', { mode: '0644' })
    assert.equal(written.path, '/workspace/a.txt')
    assert.equal(await volume.readFile('/workspace/a.txt'), 'hello')
    assert.equal((await volume.list('/workspace', { depth: 2 }))[0].name, 'a.txt')
    assert.equal(await volume.remove('/workspace/a.txt'), true)
    assert.equal(await volume.destroy(), true)

    assert.deepEqual(requests.map((request) => ({
      url: request.url,
      method: request.method,
      body: request.body,
    })), [
      {
        url: 'https://api.watasu.io/v1/volumes',
        method: 'POST',
        body: { name: 'cache', team: 'watasu' },
      },
      {
        url: 'https://api.watasu.io/v1/volumes/42/files',
        method: 'PUT',
        body: { path: '/workspace/a.txt', content_b64: 'aGVsbG8=', mode: '0644' },
      },
      {
        url: 'https://api.watasu.io/v1/volumes/42/files?path=%2Fworkspace%2Fa.txt',
        method: 'GET',
        body: undefined,
      },
      {
        url: 'https://api.watasu.io/v1/volumes/42/directories?path=%2Fworkspace&depth=2',
        method: 'GET',
        body: undefined,
      },
      {
        url: 'https://api.watasu.io/v1/volumes/42/path?path=%2Fworkspace%2Fa.txt',
        method: 'DELETE',
        body: undefined,
      },
      {
        url: 'https://api.watasu.io/v1/volumes/42',
        method: 'DELETE',
        body: undefined,
      },
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('code interpreter sandbox create defaults to code-interpreter template', async () => {
  const originalFetch = globalThis.fetch
  const requests = []
  try {
    globalThis.fetch = async (url, init) => {
      requests.push({ url: String(url), body: JSON.parse(init.body) })
      return new Response(
        JSON.stringify({
          sandbox: { id: 'code-created', template_id: 'code-interpreter' },
          session: { data_plane_url: 'https://route.sandbox.watasuhost.com', token: 'data' },
        }),
        { status: 201, headers: { 'content-type': 'application/json' } }
      )
    }

    const sbx = await CodeInterpreterSandbox.create({ apiKey: 'key' })

    assert.ok(sbx instanceof CodeInterpreterSandbox)
    assert.equal(sbx.sandboxId, 'code-created')
    assert.equal(requests[0].body.template_id, 'code-interpreter')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('code interpreter package re-exports core SDK helpers', () => {
  assert.equal(CodeInterpreterConnectionConfig, ConnectionConfig)
  assert.equal(DefaultCodeInterpreterSandbox, CodeInterpreterSandbox)
  assert.equal(CodeInterpreterTemplate, Template)
  assert.equal(CodeInterpreterPty, Pty)
  assert.notEqual(CodeInterpreterSandbox, Sandbox)
})

test('code interpreter exposes protected runtime URL to subclasses', () => {
  class InspectableCodeInterpreterSandbox extends CodeInterpreterSandbox {
    runtimeUrl() {
      return this.jupyterUrl
    }
  }

  const sbx = new InspectableCodeInterpreterSandbox({
    sandboxId: 'code',
    connectionConfig: new ConnectionConfig({ apiKey: 'key' }),
    session: { data_plane_url: 'https://route.sandbox.watasuhost.com', token: 'data' },
  })

  assert.equal(sbx.runtimeUrl(), 'https://route.sandbox.watasuhost.com')
})

test('code interpreter runCode uses runtime API and parses callbacks', async () => {
  const originalFetch = globalThis.fetch
  const requests = []
  try {
    globalThis.fetch = async (url, init = {}) => {
      requests.push({ url: String(url), method: init.method, body: init.body ? JSON.parse(init.body) : undefined })
      return new Response(
        JSON.stringify({
          execution: {
            results: [{ text: '5', json: 5, is_main_result: true }],
            logs: { stdout: ['hello'], stderr: ['warn'] },
            error: null,
            execution_count: null,
          },
        }),
        { status: 200, headers: { 'content-type': 'application/json' } }
      )
    }

    const sbx = new CodeInterpreterSandbox({
      sandboxId: 'code',
      connectionConfig: new ConnectionConfig({ apiKey: 'key' }),
      session: { data_plane_url: 'https://route.sandbox.watasuhost.com', token: 'data' },
    })
    const stdout = []
    const stderr = []
    const results = []
    let asyncResultCallbackDone = false

    const execution = await sbx.runCode("print('hello')\n2 + 3", {
      language: 'python',
      envs: { A: 'B' },
      timeoutMs: 5_000,
      requestTimeoutMs: 10,
      onStdout: (message) => stdout.push(message),
      onStderr: (message) => stderr.push(message),
      onResult: async (result) => {
        await new Promise((resolve) => setTimeout(resolve, 1))
        results.push(result)
        asyncResultCallbackDone = true
      },
    })

    assert.equal(execution.text, '5')
    assert.equal(asyncResultCallbackDone, true)
    assert.equal(stdout[0].line, 'hello')
    assert.equal(stderr[0].line, 'warn')
    assert.equal(stderr[0].error, true)
    assert.deepEqual(results[0].formats(), ['text', 'json'])
    assert.deepEqual(requests, [
      {
        url: 'https://route.sandbox.watasuhost.com/runtime/v1/code/run',
        method: 'POST',
        body: {
          code: "print('hello')\n2 + 3",
          language: 'python',
          env_vars: { A: 'B' },
          timeout_ms: 5000,
        },
      },
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('code interpreter context methods use runtime API', async () => {
  const originalFetch = globalThis.fetch
  const requests = []
  try {
    globalThis.fetch = async (url, init = {}) => {
      requests.push({ url: String(url), method: init.method, body: init.body ? JSON.parse(init.body) : undefined })
      const path = new URL(String(url)).pathname
      if (init.method === 'POST' && path.endsWith('/runtime/v1/code/contexts')) {
        return new Response(
          JSON.stringify({ id: 'ctx-1', language: 'python', cwd: '/workspace/app' }),
          { status: 201, headers: { 'content-type': 'application/json' } }
        )
      }
      if (init.method === 'GET' && path.endsWith('/runtime/v1/code/contexts')) {
        return new Response(
          JSON.stringify([{ id: 'ctx-1', language: 'python', cwd: '/workspace/app' }]),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      }
      return new Response(null, { status: 204 })
    }

    const sbx = new CodeInterpreterSandbox({
      sandboxId: 'code',
      connectionConfig: new ConnectionConfig({ apiKey: 'key' }),
      session: { data_plane_url: 'https://route.sandbox.watasuhost.com', token: 'data' },
    })

    const context = await sbx.createCodeContext({ cwd: '/workspace/app', language: 'python', requestTimeoutMs: 5 })
    const contexts = await sbx.listCodeContexts({ requestTimeoutMs: 6 })
    await sbx.restartCodeContext(context, { requestTimeoutMs: 7 })
    await sbx.removeCodeContext('ctx-1', { requestTimeoutMs: 8 })
    const emptyContext = new CodeInterpreterContext('empty')

    assert.equal(context.id, 'ctx-1')
    assert.equal(context.language, 'python')
    assert.equal(contexts[0].cwd, '/workspace/app')
    assert.equal(emptyContext.language, '')
    assert.equal(emptyContext.cwd, '')
    assert.deepEqual(requests, [
      {
        url: 'https://route.sandbox.watasuhost.com/runtime/v1/code/contexts',
        method: 'POST',
        body: { cwd: '/workspace/app', language: 'python' },
      },
      {
        url: 'https://route.sandbox.watasuhost.com/runtime/v1/code/contexts',
        method: 'GET',
        body: undefined,
      },
      {
        url: 'https://route.sandbox.watasuhost.com/runtime/v1/code/contexts/ctx-1/restart',
        method: 'POST',
        body: {},
      },
      {
        url: 'https://route.sandbox.watasuhost.com/runtime/v1/code/contexts/ctx-1',
        method: 'DELETE',
        body: undefined,
      },
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('sandbox create with mcp sends config to API without SDK-side bootstrap', async () => {
  const originalFetch = globalThis.fetch
  const originalRun = Commands.prototype.run
  const requests = []
  const commands = []
  try {
    globalThis.fetch = async (url, init) => {
      requests.push({ url: String(url), body: JSON.parse(init.body) })
      return new Response(
        JSON.stringify({
          sandbox: { id: 'mcp-created', template_id: 'mcp-gateway', route_token: 'route-token' },
          session: { data_plane_url: 'https://route.sandbox.watasuhost.com', token: 'data' },
        }),
        { status: 201, headers: { 'content-type': 'application/json' } }
      )
    }
    Commands.prototype.run = async function (cmd, opts = {}) {
      commands.push({ cmd, opts })
      return { exitCode: 0, stdout: '', stderr: '' }
    }

    const sbx = await Sandbox.create({
      apiKey: 'key',
      mcp: { server: "it's-fine", config: { enabled: true } },
    })

    assert.equal(sbx.sandboxId, 'mcp-created')
    assert.equal(requests.length, 1)
    assert.deepEqual(requests[0].body, {
      timeout: 300,
      metadata: {},
      env_vars: {},
      secure: true,
      allow_internet_access: true,
      mcp: { server: "it's-fine", config: { enabled: true } },
    })
    assert.deepEqual(commands, [])
  } finally {
    Commands.prototype.run = originalRun
    globalThis.fetch = originalFetch
  }
})

test('sandbox list returns a paginator and uses nested query params', async () => {
  const originalFetch = globalThis.fetch
  const requests = []
  try {
    globalThis.fetch = async (url, init = {}) => {
      requests.push({ url: String(url), method: init.method })
      const parsed = new URL(String(url))
      if (parsed.searchParams.get('next_token') === '2') {
        return new Response(
          JSON.stringify({ sandboxes: [{ id: '1', state: 'ready' }] }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      }
      return new Response(
        JSON.stringify({
          sandboxes: [{ id: '2', state: 'creating' }],
          next_token: '2',
        }),
        { status: 200, headers: { 'content-type': 'application/json' } }
      )
    }

    const paginator = Sandbox.list({
      apiKey: 'key',
      team: 'watasu',
      query: { metadata: { purpose: 'ci' }, state: ['running'] },
      limit: 1,
    })

    assert.ok(paginator instanceof SandboxPaginator)
    const firstPage = await paginator.nextItems()
    assert.equal(paginator.hasNext, true)
    assert.equal(paginator.nextToken, '2')
    const secondPage = await paginator.nextItems()
    assert.equal(paginator.hasNext, false)

    assert.deepEqual([...firstPage, ...secondPage].map((item) => item.sandboxId), ['2', '1'])
    assert.equal(
      requests[0].url,
      'https://api.watasu.io/v1/sandboxes?team=watasu&limit=1&query%5Bmetadata%5D%5Bpurpose%5D=ci&query%5Bstate%5D%5B%5D=running'
    )
    assert.equal(
      requests[1].url,
      'https://api.watasu.io/v1/sandboxes?team=watasu&limit=1&next_token=2&query%5Bmetadata%5D%5Bpurpose%5D=ci&query%5Bstate%5D%5B%5D=running'
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('transport applies API headers and caller abort signals', async () => {
  const originalFetch = globalThis.fetch
  const controller = new AbortController()
  const requests = []
  try {
    globalThis.fetch = async (url, init = {}) => {
      const headers = Object.fromEntries(new Headers(init.headers).entries())
      requests.push({ url: String(url), headers, signal: init.signal })
      if (String(url).startsWith('https://api.watasu.io')) {
        assert.equal(init.signal.aborted, false)
        controller.abort()
        await Promise.resolve()
        assert.equal(init.signal.aborted, true)
        return new Response(JSON.stringify({ sandbox: { id: 'headers' } }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }
      return new Response(JSON.stringify({ entries: [] }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    }

    await Sandbox.getInfo('headers', {
      apiKey: 'key',
      headers: { 'x-shared': 'shared' },
      apiHeaders: { 'x-api': 'api' },
      signal: controller.signal,
    })

    const sbx = new Sandbox({
      sandboxId: 'headers',
      connectionConfig: new ConnectionConfig({
        apiKey: 'key',
        headers: { 'x-shared': 'shared' },
        apiHeaders: { 'x-api': 'api' },
      }),
      session: { data_plane_url: 'https://route.sandbox.watasuhost.com', token: 'data-token' },
    })
    await sbx.files.list('/workspace')

    assert.equal(requests[0].headers.authorization, 'Bearer key')
    assert.equal(requests[0].headers['x-shared'], 'shared')
    assert.equal(requests[0].headers['x-api'], 'api')
    assert.equal(requests[1].headers.authorization, 'Bearer data-token')
    assert.equal(requests[1].headers['x-shared'], 'shared')
    assert.equal(requests[1].headers['x-api'], undefined)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('sandbox updateNetwork uses snake_case API payload', async () => {
  const originalFetch = globalThis.fetch
  const requests = []
  try {
    globalThis.fetch = async (url, init = {}) => {
      requests.push({ url: String(url), method: init.method, body: init.body ? JSON.parse(init.body) : undefined })
      return new Response(
        JSON.stringify({
          sandbox: {
            id: 'network-sandbox',
            network_policy: requests.at(-1).body,
          },
        }),
        { status: 200, headers: { 'content-type': 'application/json' } }
      )
    }

    const sbx = new Sandbox({
      sandboxId: 'network-sandbox',
      connectionConfig: new ConnectionConfig({ apiKey: 'key' }),
      session: { data_plane_url: 'https://route.sandbox.watasuhost.com', token: 'data' },
    })

    await sbx.updateNetwork({
      allowOut: ['registry.npmjs.org:443'],
      denyOut: ['10.0.0.0/8'],
      allowInternetAccess: false,
      allowPackageRegistryAccess: true,
      rules: new Map([
        ['registry.npmjs.org', [{ transform: { headers: { authorization: 'Bearer token' } } }]],
      ]),
      maskRequestHost: '${PORT}-sandbox.example.com',
    })

    assert.deepEqual(requests, [
      {
        url: 'https://api.watasu.io/v1/sandboxes/network-sandbox/network',
        method: 'PUT',
        body: {
          allow_out: ['registry.npmjs.org:443'],
          deny_out: ['10.0.0.0/8'],
          allow_internet_access: false,
          allow_package_registry_access: true,
          rules: {
            'registry.npmjs.org': [
              { transform: { headers: { authorization: 'Bearer token' } } },
            ],
          },
          mask_request_host: '${PORT}-sandbox.example.com',
        },
      },
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('sandbox static updateNetwork uses snake_case API payload', async () => {
  const originalFetch = globalThis.fetch
  const requests = []
  try {
    globalThis.fetch = async (url, init = {}) => {
      requests.push({ url: String(url), method: init.method, body: init.body ? JSON.parse(init.body) : undefined })
      return new Response(JSON.stringify({}), { status: 200, headers: { 'content-type': 'application/json' } })
    }

    await Sandbox.updateNetwork(
      'network-sandbox',
      {
        allowOut: ({ rules }) => [...rules.keys()],
        denyOut: ['10.0.0.0/8'],
        allowInternetAccess: false,
        allowPackageRegistryAccess: true,
        rules: {
          'registry.npmjs.org': [
            { transform: { headers: { authorization: 'Bearer token' } } },
          ],
        },
      },
      { apiKey: 'key' }
    )

    assert.deepEqual(requests, [
      {
        url: 'https://api.watasu.io/v1/sandboxes/network-sandbox/network',
        method: 'PUT',
        body: {
          allow_out: ['registry.npmjs.org'],
          deny_out: ['10.0.0.0/8'],
          allow_internet_access: false,
          allow_package_registry_access: true,
          rules: {
            'registry.npmjs.org': [
              { transform: { headers: { authorization: 'Bearer token' } } },
            ],
          },
        },
      },
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('sandbox connect and setTimeout use root timeout payloads', async () => {
  const originalFetch = globalThis.fetch
  const requests = []
  try {
    globalThis.fetch = async (url, init = {}) => {
      requests.push({ url: String(url), method: init.method, body: init.body ? JSON.parse(init.body) : undefined })
      if (String(url).endsWith('/resume')) {
        return new Response(
          JSON.stringify({
            sandbox: { id: 'existing' },
            session: { data_plane_url: 'https://route.sandbox.watasuhost.com', token: 'data' },
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      }
      return new Response(JSON.stringify({ sandbox: { id: 'existing' } }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    }

    const sbx = await Sandbox.connect('existing', { apiKey: 'key', timeoutMs: 90_000 })
    await sbx.setTimeout(180_000)

    assert.deepEqual(requests.map((request) => [request.method, request.url, request.body]), [
      ['GET', 'https://api.watasu.io/v1/sandboxes/existing', undefined],
      ['POST', 'https://api.watasu.io/v1/sandboxes/existing/resume', { timeout: 90 }],
      ['POST', 'https://api.watasu.io/v1/sandboxes/existing/timeout', { timeout: 180 }],
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('sandbox pause and resume use lifecycle routes', async () => {
  const originalFetch = globalThis.fetch
  const requests = []
  try {
    globalThis.fetch = async (url, init = {}) => {
      requests.push({ url: String(url), method: init.method, body: init.body ? JSON.parse(init.body) : undefined })
      if (String(url).endsWith('/pause')) {
        return new Response(JSON.stringify({ sandbox: { id: 'existing', state: 'stopped' } }), {
          status: 202,
          headers: { 'content-type': 'application/json' },
        })
      }
      if (String(url).endsWith('/resume')) {
        return new Response(
          JSON.stringify({
            sandbox: { id: 'existing', state: 'ready' },
            session: { data_plane_url: 'https://route.sandbox.watasuhost.com', token: 'data' },
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      }
      if (String(url).endsWith('/sandboxes/existing')) {
        return new Response(JSON.stringify({ sandbox: { id: 'existing' } }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }
      throw new Error(`unexpected request ${url}`)
    }

    const sbx = new Sandbox({
      sandboxId: 'existing',
      connectionConfig: new ConnectionConfig({ apiKey: 'key' }),
      session: { data_plane_url: 'https://route.sandbox.watasuhost.com', token: 'data' },
      sandbox: { route_token: 'route-token' },
    })

    assert.equal(await sbx.betaPause(), true)
    assert.equal(await sbx.pause(), true)
    assert.equal(await Sandbox.pause('existing', { apiKey: 'key' }), true)
    assert.equal(await sbx.resume({ timeoutMs: 120_000 }), true)

    assert.deepEqual(requests.map((request) => [request.method, request.url, request.body]), [
      ['POST', 'https://api.watasu.io/v1/sandboxes/existing/pause', undefined],
      ['POST', 'https://api.watasu.io/v1/sandboxes/existing/pause', undefined],
      ['POST', 'https://api.watasu.io/v1/sandboxes/existing/pause', undefined],
      ['POST', 'https://api.watasu.io/v1/sandboxes/existing/resume', { timeout: 120 }],
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('sandbox pause returns false for already paused conflicts', async () => {
  const originalFetch = globalThis.fetch
  try {
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ error: 'sandbox_already_paused' }), {
        status: 409,
        headers: { 'content-type': 'application/json' },
      })

    assert.equal(await Sandbox.betaPause('existing', { apiKey: 'key' }), false)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('sandbox isRunning reflects control-plane state', async () => {
  const originalFetch = globalThis.fetch
  try {
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ sandbox: { id: '1', state: 'ready' } }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })

    const sbx = new Sandbox({
      sandboxId: '1',
      connectionConfig: new ConnectionConfig({ apiKey: 'key' }),
      session: { data_plane_url: 'https://route.sandbox.watasuhost.com', token: 'data' },
      sandbox: { route_token: 'route-token' },
    })

    assert.equal(await sbx.isRunning(), true)

    globalThis.fetch = async () =>
      new Response(JSON.stringify({ error: 'not_found' }), {
        status: 404,
        headers: { 'content-type': 'application/json' },
      })

    assert.equal(await sbx.isRunning(), false)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('sandbox metrics and snapshots use supported control-plane routes', async () => {
  const originalFetch = globalThis.fetch
  const requests = []
  try {
    globalThis.fetch = async (url, init = {}) => {
      requests.push({ url: String(url), method: init.method, body: init.body ? JSON.parse(init.body) : undefined })
      if (new URL(String(url)).pathname.endsWith('/metrics')) {
        return new Response(
          JSON.stringify({ metrics: { sandbox_id: '1', state: 'ready', backend: 'firecracker' } }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      }
      if (String(url).endsWith('/snapshots') && init.method === 'POST') {
        return new Response(
          JSON.stringify({ sandbox_checkpoint: { id: 9, sandbox_id: '1', name: 'ready', status: 'pending' } }),
          { status: 202, headers: { 'content-type': 'application/json' } }
        )
      }
      const parsedUrl = new URL(String(url))
      if (parsedUrl.pathname.endsWith('/sandbox_snapshots') && init.method === 'GET') {
        return new Response(
          JSON.stringify({ snapshots: [{ id: 9, sandbox_id: '1', name: 'ready', status: 'ready' }] }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      }
      if (String(url).endsWith('/restore')) {
        return new Response(
          JSON.stringify({ sandbox: { id: 'restored', state: 'restoring', template_id: 'base' } }),
          { status: 202, headers: { 'content-type': 'application/json' } }
        )
      }
      if (String(url).endsWith('/sandbox_snapshots/9') && init.method === 'DELETE') {
        return new Response(JSON.stringify({ deleted: true }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }
      if (String(url).endsWith('/files/upload_url') || String(url).endsWith('/files/download_url')) {
        const body = JSON.parse(init.body)
        return new Response(
          JSON.stringify({ file_url: { method: String(url).endsWith('/upload_url') ? 'POST' : 'GET', path: body.path, url: `https://signed.example${body.path}`, expires_at: '2026-01-01T00:00:00Z' } }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      }
      if (String(url).startsWith('https://route.sandbox.watasuhost.com/runtime/v1/files?path=%2Fetc%2Fmcp-gateway%2F.token')) {
        return new Response(' gateway-token\n', {
          status: 200,
          headers: { 'content-type': 'text/plain' },
        })
      }
      throw new Error(`unexpected request ${url}`)
    }

    const sbx = new Sandbox({
      sandboxId: '1',
      connectionConfig: new ConnectionConfig({ apiKey: 'key' }),
      session: { data_plane_url: 'https://route.sandbox.watasuhost.com', token: 'data' },
      sandbox: { route_token: 'route-token' },
    })

    const metrics = await sbx.getMetrics({
      start: new Date('2025-11-04T12:40:00Z'),
      end: new Date('2025-11-04T12:41:00Z'),
    })
    const snapshot = await sbx.createSnapshot({ name: 'ready', metadata: { reason: 'test' } })
    const snapshots = await sbx.listSnapshots().nextItems()
    const restored = await sbx.restore({ snapshotId: snapshot.snapshotId, timeoutMs: 120_000 })
    const deleted = await sbx.deleteSnapshot(snapshot.snapshotId)
    const uploadUrl = await sbx.uploadUrl('/tmp/a.txt', { useSignatureExpiration: 300 })
    const rootUploadUrl = await sbx.uploadUrl()
    const downloadUrl = await sbx.downloadUrl('/tmp/a.txt')
    const mcpUrl = sbx.getMcpUrl()
    const mcpToken = await sbx.getMcpToken()
    const cachedMcpToken = await sbx.getMcpToken()

    assert.equal(metrics[0].backend, 'firecracker')
    assert.equal(snapshot.snapshotId, '9')
    assert.equal(snapshots[0].status, 'ready')
    assert.equal(restored.sandboxId, 'restored')
    assert.equal(deleted, true)
    assert.equal(uploadUrl, 'https://signed.example/tmp/a.txt')
    assert.equal(rootUploadUrl, 'https://signed.example')
    assert.equal(downloadUrl, 'https://signed.example/tmp/a.txt')
    assert.equal(mcpUrl, 'https://p50005-route-token.sandbox.watasuhost.com/mcp')
    assert.equal(mcpToken, 'gateway-token')
    assert.equal(cachedMcpToken, 'gateway-token')
    assert.deepEqual(requests.map((request) => [request.method, request.url, request.body]), [
      ['GET', 'https://api.watasu.io/v1/sandboxes/1/metrics?start=1762260000&end=1762260060', undefined],
      ['POST', 'https://api.watasu.io/v1/sandboxes/1/snapshots', { name: 'ready', metadata: { reason: 'test' } }],
      ['GET', 'https://api.watasu.io/v1/sandbox_snapshots?sandbox_id=1', undefined],
      ['POST', 'https://api.watasu.io/v1/sandboxes/1/restore', { checkpoint_id: '9', timeout_seconds: 120 }],
      ['DELETE', 'https://api.watasu.io/v1/sandbox_snapshots/9', undefined],
      ['POST', 'https://api.watasu.io/v1/sandboxes/1/files/upload_url', { path: '/tmp/a.txt', use_signature_expiration: 300 }],
      ['POST', 'https://api.watasu.io/v1/sandboxes/1/files/upload_url', { path: '' }],
      ['POST', 'https://api.watasu.io/v1/sandboxes/1/files/download_url', { path: '/tmp/a.txt' }],
      ['GET', 'https://route.sandbox.watasuhost.com/runtime/v1/files?path=%2Fetc%2Fmcp-gateway%2F.token', undefined],
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('sandbox static listSnapshots returns a paginated global snapshot list', async () => {
  const originalFetch = globalThis.fetch
  const requests = []
  try {
    globalThis.fetch = async (url, init = {}) => {
      requests.push({ url: String(url), method: init.method })
      const parsedUrl = new URL(String(url))
      if (parsedUrl.searchParams.get('next_token') === '2') {
        return new Response(
          JSON.stringify({ snapshots: [{ id: 1, sandbox_id: 'sandbox-a', status: 'ready' }] }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      }
      return new Response(
        JSON.stringify({
          snapshots: [{ id: 2, sandbox_id: 'sandbox-b', status: 'ready' }],
          next_token: '2',
        }),
        { status: 200, headers: { 'content-type': 'application/json' } }
      )
    }

    const paginator = Sandbox.listSnapshots({ apiKey: 'key', limit: 1 })

    assert.ok(paginator instanceof SnapshotPaginator)
    const firstPage = await paginator.nextItems()
    assert.equal(paginator.hasNext, true)
    assert.equal(paginator.nextToken, '2')
    const secondPage = await paginator.nextItems()
    assert.equal(paginator.hasNext, false)

    assert.deepEqual([...firstPage, ...secondPage].map((item) => item.snapshotId), ['2', '1'])
    assert.deepEqual(requests, [
      {
        url: 'https://api.watasu.io/v1/sandbox_snapshots?limit=1',
        method: 'GET',
      },
      {
        url: 'https://api.watasu.io/v1/sandbox_snapshots?limit=1&next_token=2',
        method: 'GET',
      },
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('template builder sends snake_case build payloads and parses status', async () => {
  const originalFetch = globalThis.fetch
  const contextPath = mkdtempSync(join(tmpdir(), 'watasu-template-'))
  mkdirSync(join(contextPath, 'src'))
  writeFileSync(join(contextPath, 'src', 'app.js'), 'console.log("ok")\n')
  writeFileSync(join(contextPath, 'Dockerfile'), 'FROM node:22\nWORKDIR /workspace\nCOPY src /workspace/src\nRUN npm install\nENV NODE_ENV=production\nCMD node /workspace/src/app.js\n')
  const requests = []
  try {
    globalThis.fetch = async (url, init = {}) => {
      requests.push({ url: String(url), method: init.method, body: init.body ? JSON.parse(init.body) : undefined })
      if (String(url).endsWith('/templates')) {
        return new Response(
          JSON.stringify({
            template_build: {
              template_id: '42',
              build_id: '99',
              alias: 'python-ci',
              name: 'python-ci:stable',
              tags: ['stable'],
              status: 'building',
            },
          }),
          { status: 201, headers: { 'content-type': 'application/json' } }
        )
      }
      if (String(url).includes('/templates/42/builds/99/status')) {
        return new Response(
          JSON.stringify({
            template_id: '42',
            build_id: '99',
            status: 'ready',
            log_entries: [{ timestamp: '2026-06-15T00:00:00Z', level: 'info', message: 'done' }],
            logs: ['done'],
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      }
      throw new Error(`unexpected request ${url}`)
    }

    const template = Template({ fileContextPath: contextPath })
      .fromPythonImage('3.12')
      .aptInstall(['git'])
      .pipInstall(['pytest'])
      .copy('src/app.js', '/workspace/app.js', { mode: 0o755, user: 'root:root' })
      .setEnvs({ TOKEN: 'secret' })
      .runCmd('echo ready')

    const mcpTemplate = Template()
      .fromTemplate('mcp-gateway')
      .addMcpServer(['exa', 'brave'])

    const build = await Template.buildInBackground(template, 'python-ci:stable', {
      apiKey: 'key',
      cpuCount: 4,
      memoryMB: 4096,
      tags: ['stable'],
      skipCache: true,
      team: 'watasu',
    })
    const status = await Template.getBuildStatus(build, { apiKey: 'key', logsOffset: 1 })

    assert.equal(build.templateId, '42')
    assert.equal(status.status, 'ready')
    assert.equal(status.logEntries[0].message, 'done')
    assert.deepEqual(JSON.parse(await Template.toJSON(mcpTemplate)), {
      from_template: 'mcp-gateway',
      setup: ['mcp-gateway pull exa brave'],
    })
    assert.equal(waitForPort(8000).getCmd(), 'ss -tuln | grep :8000')
    assert.equal(waitForURL('http://localhost:3000/health').getCmd(), 'curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/health | grep -q "200"')
    assert.equal(waitForProcess('server').getCmd(), 'pgrep server > /dev/null')
    assert.equal(waitForFile('/tmp/ready').getCmd(), '[ -f /tmp/ready ]')
    assert.equal(waitForTimeout(2500).getCmd(), 'sleep 2')
    assert.equal(waitForTimeout(500).getCmd(), 'sleep 1')
    assert.equal(new ReadyCmd('test -f /tmp/custom').getCmd(), 'test -f /tmp/custom')
    assert.deepEqual(JSON.parse(await Template.toJSON(
      Template()
        .setStartCmd('npm start', waitForPort(3000))
        .setReadyCmd(waitForTimeout(1000))
    )), {
      start_cmd: 'npm start',
      ready_cmd: 'sleep 1',
    })
    const devcontainerTemplate = Template()
      .fromTemplate('devcontainer')
      .gitClone('https://example.test/project.git', '/workspace/project')
      .betaDevContainerPrebuild('/workspace/project')
      .betaSetDevContainerStart('/workspace/project')
    assert.deepEqual(JSON.parse(await Template.toJSON(devcontainerTemplate)), {
      from_template: 'devcontainer',
      setup: [
        'git clone https://example.test/project.git /workspace/project',
        'devcontainer build --workspace-folder /workspace/project',
      ],
      start_cmd: 'sudo devcontainer up --workspace-folder /workspace/project && sudo /prepare-exec.sh /workspace/project | sudo tee /devcontainer.sh > /dev/null && sudo chmod +x /devcontainer.sh && sudo touch /devcontainer.up',
      ready_cmd: '[ -f /devcontainer.up ]',
    })
    assert.throws(
      () => Template().betaDevContainerPrebuild('/workspace/project'),
      /devcontainer template/
    )
    assert.equal(Template.toDockerfile(template), [
      'FROM python:3.12',
      'RUN apt-get update && apt-get install -y git',
      'RUN python3 -m pip install pytest',
      'COPY src/app.js /workspace/app.js',
      'RUN echo ready',
      '',
    ].join('\n'))
    assert.deepEqual(JSON.parse(await Template.toJSON(
      Template({ fileContextPath: contextPath }).fromDockerfile('Dockerfile')
    )), {
      from_image: 'node:22',
      files: [
        {
          path: '/workspace/src/app.js',
          source_path: 'src/app.js',
          content_b64: Buffer.from('console.log("ok")\n').toString('base64'),
        },
      ],
      setup: ['cd \'/workspace\' && npm install'],
      env: { NODE_ENV: 'production' },
      start_cmd: 'node /workspace/src/app.js',
      ready_cmd: 'sleep 20',
    })
    assert.throws(() => Template().addMcpServer('exa'), /mcp-gateway/)
    assert.deepEqual(Template().fromAWSRegistry('image', { accessKeyId: 'key', secretAccessKey: 'secret', region: 'us-east-1' }).toBuildSpec(), {
      from_image: 'image',
      from_image_registry: {
        type: 'aws',
        aws_access_key_id: 'key',
        aws_secret_access_key: 'secret',
        aws_region: 'us-east-1',
      },
    })
    assert.deepEqual(Template().fromGCPRegistry('image', { serviceAccountJSON: {} }).toBuildSpec(), {
      from_image: 'image',
      from_image_registry: {
        type: 'gcp',
        service_account_json: {},
      },
    })
    assert.deepEqual(requests, [
      {
        url: 'https://api.watasu.io/v1/templates',
        method: 'POST',
        body: {
          name: 'python-ci:stable',
          tags: ['stable'],
          cpu_count: 4,
          memory_mb: 4096,
          skip_cache: true,
          build_spec: {
            from_image: 'python:3.12',
            packages: { apt: ['git'], pip: ['pytest'] },
            files: [
              {
                path: '/workspace/app.js',
                source_path: 'src/app.js',
                content_b64: Buffer.from('console.log("ok")\n').toString('base64'),
                mode: 493,
                user: 'root:root',
              },
            ],
            setup: ['echo ready'],
            env: { TOKEN: 'secret' },
          },
          team: 'watasu',
        },
      },
      {
        url: 'https://api.watasu.io/v1/templates/42/builds/99/status?logs_offset=1',
        method: 'GET',
        body: undefined,
      },
    ])
  } finally {
    globalThis.fetch = originalFetch
    rmSync(contextPath, { recursive: true, force: true })
  }
})

test('template alias and tag helpers use template routes', async () => {
  const originalFetch = globalThis.fetch
  const requests = []
  try {
    globalThis.fetch = async (url, init = {}) => {
      requests.push({ url: String(url), method: init.method, body: init.body ? JSON.parse(init.body) : undefined })
      if (String(url).endsWith('/templates/aliases/missing')) {
        return new Response(JSON.stringify({ error: 'not_found' }), {
          status: 404,
          headers: { 'content-type': 'application/json' },
        })
      }
      if (String(url).includes('/templates/aliases/')) {
        return new Response(JSON.stringify({ template: { slug: 'python-ci' } }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }
      if (String(url).endsWith('/templates/tags') && init.method === 'POST') {
        return new Response(JSON.stringify({ build_id: '99', tags: ['stable', 'prod'] }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }
      if (String(url).endsWith('/templates/tags') && init.method === 'DELETE') {
        return new Response(null, { status: 204 })
      }
      if (String(url).endsWith('/templates/python-ci/tags')) {
        return new Response(JSON.stringify([{ tag: 'prod', build_id: '99', created_at: '2026-06-15T00:00:00Z' }]), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }
      throw new Error(`unexpected request ${url}`)
    }

    assert.equal(await Template.exists('python-ci', { apiKey: 'key' }), true)
    assert.equal(await Template.aliasExists('missing', { apiKey: 'key' }), false)
    assert.deepEqual(await Template.assignTags('python-ci:stable', ['prod'], { apiKey: 'key' }), {
      buildId: '99',
      tags: ['stable', 'prod'],
    })
    await Template.removeTags('python-ci', 'prod', { apiKey: 'key' })
    const tags = await Template.getTags('python-ci', { apiKey: 'key' })
    assert.equal(tags[0].tag, 'prod')
    assert.equal(tags[0].buildId, '99')

    assert.deepEqual(requests.map((request) => [request.method, request.url, request.body]), [
      ['GET', 'https://api.watasu.io/v1/templates/aliases/python-ci', undefined],
      ['GET', 'https://api.watasu.io/v1/templates/aliases/missing', undefined],
      ['POST', 'https://api.watasu.io/v1/templates/tags', { target: 'python-ci:stable', tags: ['prod'] }],
      ['DELETE', 'https://api.watasu.io/v1/templates/tags', { name: 'python-ci', tags: ['prod'] }],
      ['GET', 'https://api.watasu.io/v1/templates/python-ci/tags', undefined],
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('git helper uses data-plane git routes', async () => {
  const originalFetch = globalThis.fetch
  const requests = []
  try {
    globalThis.fetch = async (url, init = {}) => {
      requests.push({ url: String(url), method: init.method, body: init.body ? JSON.parse(init.body) : undefined })
      if (String(url).endsWith('/git/status')) {
        return new Response(
          JSON.stringify({ git: { path: '/workspace/repo', stdout: '## main...origin/main [ahead 1]\n M a.txt\n?? b.txt\n', stderr: '' } }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      }
      if (String(url).endsWith('/git/branches')) {
        return new Response(
          JSON.stringify({ git: { path: '/workspace/repo', branches: ['main', 'feature/test'], current_branch: 'main', stdout: '', stderr: '' } }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      }
      if (String(url).endsWith('/git/get_config')) {
        return new Response(
          JSON.stringify({ git: { path: '/workspace/repo', key: 'pull.rebase', value: 'false', stdout: 'false\n', stderr: '' } }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      }
      if (String(url).endsWith('/git/remote_get')) {
        return new Response(
          JSON.stringify({ git: { path: '/workspace/repo', name: 'origin', value: 'https://git.example/repo.git', url: 'https://git.example/repo.git', stdout: 'https://git.example/repo.git\n', stderr: '' } }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      }
      return new Response(
        JSON.stringify({ git: { path: '/workspace/repo', url: 'https://git.example/repo.git', branch: 'feature/test', remote: 'origin', name: 'origin', stdout: 'ok\n', stderr: '' } }),
        { status: 200, headers: { 'content-type': 'application/json' } }
      )
    }

    const sbx = new Sandbox({
      sandboxId: '1',
      connectionConfig: new ConnectionConfig({ apiKey: 'key' }),
      session: { data_plane_url: 'https://route.sandbox.watasuhost.com', token: 'data' },
      sandbox: { route_token: 'route-token' },
    })

    const clone = await sbx.git.clone('https://git.example/repo.git', { path: '/workspace/repo', branch: 'main', depth: 1, user: 'sandbox', cwd: '/workspace', timeoutMs: 10_000 })
    await sbx.git.dangerouslyAuthenticate({ username: 'user', password: 'token', host: 'git.example.com', protocol: 'https', timeoutMs: 5_000 })
    await sbx.git.configureUser('Watasu Test', 'test@watasu.local', { scope: 'local', path: '/workspace/repo' })
    await sbx.git.init('/workspace/repo', { initialBranch: 'main' })
    const status = await sbx.git.status('/workspace/repo', { user: 'sandbox', cwd: '/workspace' })
    const branches = await sbx.git.branches('/workspace/repo')
    await sbx.git.createBranch('/workspace/repo', 'feature/test')
    await sbx.git.deleteBranch('/workspace/repo', 'feature/test', { force: true })
    await sbx.git.add('/workspace/repo', { files: ['README.md'], all: true, user: 'sandbox', cwd: '/workspace/repo' })
    await sbx.git.commit('/workspace/repo', 'change', { authorName: 'Watasu Test', authorEmail: 'test@watasu.local', allowEmpty: true })
    await sbx.git.reset('/workspace/repo', { mode: 'hard', target: 'HEAD', paths: ['README.md'] })
    await sbx.git.restore('/workspace/repo', { paths: ['README.md'], staged: true })
    await sbx.git.pull('/workspace/repo', { remote: 'origin', branch: 'main', username: 'user', password: 'token' })
    await sbx.git.push('/workspace/repo', { remote: 'origin', branch: 'main', username: 'user', password: 'token' })
    await sbx.git.checkout('/workspace/repo', 'main')
    await sbx.git.checkoutBranch('/workspace/repo', 'main')
    await sbx.git.remoteAdd('/workspace/repo', 'origin', 'https://git.example/repo.git', { fetch: true, overwrite: true })
    const remoteUrl = await sbx.git.remoteGet('/workspace/repo', 'origin')
    await sbx.git.setConfig('pull.rebase', 'false', { scope: 'local', path: '/workspace/repo' })
    const configValue = await sbx.git.getConfig('pull.rebase', { scope: 'local', path: '/workspace/repo' })

    assert.equal(clone.path, '/workspace/repo')
    assert.equal(status.currentBranch, 'main')
    assert.equal(status.ahead, 1)
    assert.equal(status.hasChanges, true)
    assert.equal(status.untrackedCount, 1)
    assert.deepEqual(branches.branches, ['main', 'feature/test'])
    assert.equal(branches.currentBranch, 'main')
    assert.equal(remoteUrl, 'https://git.example/repo.git')
    assert.equal(configValue, 'false')
    assert.deepEqual(requests.map((request) => [request.method, request.url, request.body]), [
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/clone', { url: 'https://git.example/repo.git', user: 'sandbox', cwd: '/workspace', timeout_seconds: 10, path: '/workspace/repo', branch: 'main', depth: 1 }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/dangerously_authenticate', { timeout_seconds: 5, username: 'user', password: 'token', host: 'git.example.com', protocol: 'https' }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/configure_user', { name: 'Watasu Test', email: 'test@watasu.local', scope: 'local', path: '/workspace/repo' }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/init', { path: '/workspace/repo', initial_branch: 'main' }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/status', { path: '/workspace/repo', user: 'sandbox', cwd: '/workspace' }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/branches', { path: '/workspace/repo' }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/create_branch', { path: '/workspace/repo', branch: 'feature/test' }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/delete_branch', { path: '/workspace/repo', branch: 'feature/test', force: true }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/add', { path: '/workspace/repo', files: ['README.md'], all: true, user: 'sandbox', cwd: '/workspace/repo' }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/commit', { path: '/workspace/repo', message: 'change', author_name: 'Watasu Test', author_email: 'test@watasu.local', allow_empty: true }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/reset', { path: '/workspace/repo', mode: 'hard', target: 'HEAD', paths: ['README.md'] }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/restore', { path: '/workspace/repo', paths: ['README.md'], staged: true }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/pull', { path: '/workspace/repo', remote: 'origin', branch: 'main', username: 'user', password: 'token' }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/push', { path: '/workspace/repo', remote: 'origin', branch: 'main', username: 'user', password: 'token', set_upstream: true }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/checkout', { path: '/workspace/repo', ref: 'main' }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/checkout', { path: '/workspace/repo', ref: 'main' }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/remote_add', { path: '/workspace/repo', name: 'origin', url: 'https://git.example/repo.git', fetch: true, overwrite: true }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/remote_get', { path: '/workspace/repo', name: 'origin' }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/set_config', { key: 'pull.rebase', value: 'false', scope: 'local', path: '/workspace/repo' }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/get_config', { key: 'pull.rebase', scope: 'local', path: '/workspace/repo' }],
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('watch handle dispatches filesystem events and can be stopped', async () => {
  let closed = false
  const socket = { close() { closed = true } }
  async function* frames() {
    yield { type: 'events', events: [{ type: 'modify', path: '/tmp/a.txt', file: { path: '/tmp/a.txt', name: 'a.txt', type: 'file', bytes: 2 } }] }
  }
  const events = []
  const handle = new WatchHandle(socket, frames(), (event) => events.push(event))

  await handle.wait()
  handle.stop()

  assert.equal(events[0].type, 'write')
  assert.equal(events[0].entry.name, 'a.txt')
  assert.equal(closed, true)
})
