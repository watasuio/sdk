import assert from 'node:assert/strict'
import test from 'node:test'

import {
  CommandExitError,
  CommandHandle,
  ConnectionConfig,
  Filesystem,
  FilesystemWatcher,
  ProcessManager,
  ProcessOutput,
  ProcessSocket,
  Sandbox,
  SandboxError,
  SandboxPaginator,
  WatchHandle,
  base64DecodeText,
  base64Encode,
} from '../dist/index.js'

test('connection config defaults to Watasu hosts', () => {
  const config = new ConnectionConfig({ apiKey: 'key' })
  assert.equal(config.apiUrl, 'https://api.watasu.io/v1')
  assert.equal(config.dataPlaneDomain, 'watasuhost.com')
  assert.equal(config.authHeaders.Authorization, 'Bearer key')
})

test('connection config accepts access token alias', () => {
  const config = new ConnectionConfig({ accessToken: 'alias-key' })
  assert.equal(config.authHeaders.Authorization, 'Bearer alias-key')
})

test('stream frame helpers match runtime base64 protocol', () => {
  assert.equal(base64DecodeText('NAo='), '4\n')
  assert.equal(base64Encode(new TextEncoder().encode('hi\n')), 'aGkK')
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

test('sandbox exposes compatibility aliases without destroying local state', async () => {
  const sbx = new Sandbox({
    sandboxId: '1',
    connectionConfig: new ConnectionConfig({ apiKey: 'key' }),
    session: { data_plane_url: 'https://derived-token.sandbox.watasuhost.com', token: 'data' },
    sandbox: { route_token: 'derived-token' },
  })

  assert.equal(sbx.id, '1')
  assert.equal(sbx.filesystem, sbx.files)
  assert.equal(typeof sbx.process.start, 'function')
  assert.equal(typeof sbx.terminal.start, 'function')
  assert.equal(sbx.getHostname(), 'derived-token.sandbox.watasuhost.com')
  assert.equal(sbx.getHostname(8080), 'p8080-derived-token.sandbox.watasuhost.com')
  assert.equal(sbx.getProtocol('http', true), 'https')
  await sbx.close()
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

    const sbx = await Sandbox.create('base:82', {
      apiKey: 'key',
      timeoutMs: 120_000,
      envs: { HELLO: 'world' },
      metadata: { purpose: 'compat' },
      team: 'bridgeapp',
      network: {
        allowOut: ['pypi.org:443'],
        denyOut: ['10.0.0.0/8'],
        allowPackageRegistryAccess: true,
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
      allow_out: ['pypi.org:443'],
      deny_out: ['10.0.0.0/8'],
      allow_package_registry_access: true,
      team: 'bridgeapp',
    })
  } finally {
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
      team: 'bridgeapp',
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
      'https://api.watasu.io/v1/sandboxes?team=bridgeapp&limit=1&query%5Bmetadata%5D%5Bpurpose%5D=ci&query%5Bstate%5D%5B%5D=running'
    )
    assert.equal(
      requests[1].url,
      'https://api.watasu.io/v1/sandboxes?team=bridgeapp&limit=1&next_token=2&query%5Bmetadata%5D%5Bpurpose%5D=ci&query%5Bstate%5D%5B%5D=running'
    )
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

test('sandbox pause and resume use lifecycle compatibility routes', async () => {
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
      if (String(url).endsWith('/metrics')) {
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
      if (String(url).endsWith('/checkpoints')) {
        return new Response(
          JSON.stringify({ sandbox_checkpoints: [{ id: 9, sandbox_id: '1', name: 'ready', status: 'ready' }] }),
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
      throw new Error(`unexpected request ${url}`)
    }

    const sbx = new Sandbox({
      sandboxId: '1',
      connectionConfig: new ConnectionConfig({ apiKey: 'key' }),
      session: { data_plane_url: 'https://route.sandbox.watasuhost.com', token: 'data' },
      sandbox: { route_token: 'route-token' },
    })

    const metrics = await sbx.getMetrics()
    const snapshot = await sbx.createSnapshot({ name: 'ready', metadata: { reason: 'test' } })
    const snapshots = await sbx.listSnapshots().nextItems()
    const restored = await sbx.restore({ snapshotId: snapshot.snapshotId, timeoutMs: 120_000 })
    const deleted = await sbx.deleteSnapshot(snapshot.snapshotId)
    const uploadUrl = await sbx.uploadUrl('/tmp/a.txt', { useSignatureExpiration: 300 })
    const downloadUrl = await sbx.downloadUrl('/tmp/a.txt')

    assert.equal(metrics[0].backend, 'firecracker')
    assert.equal(snapshot.snapshotId, '9')
    assert.equal(snapshots[0].status, 'ready')
    assert.equal(restored.sandboxId, 'restored')
    assert.equal(deleted, true)
    assert.equal(uploadUrl, 'https://signed.example/tmp/a.txt')
    assert.equal(downloadUrl, 'https://signed.example/tmp/a.txt')
    assert.deepEqual(requests.map((request) => [request.method, request.url, request.body]), [
      ['GET', 'https://api.watasu.io/v1/sandboxes/1/metrics', undefined],
      ['POST', 'https://api.watasu.io/v1/sandboxes/1/snapshots', { name: 'ready', metadata: { reason: 'test' } }],
      ['GET', 'https://api.watasu.io/v1/sandboxes/1/checkpoints', undefined],
      ['POST', 'https://api.watasu.io/v1/sandboxes/1/restore', { checkpoint_id: '9', timeout_seconds: 120 }],
      ['DELETE', 'https://api.watasu.io/v1/sandbox_snapshots/9', undefined],
      ['POST', 'https://api.watasu.io/v1/sandboxes/1/files/upload_url', { path: '/tmp/a.txt', use_signature_expiration: 300 }],
      ['POST', 'https://api.watasu.io/v1/sandboxes/1/files/download_url', { path: '/tmp/a.txt' }],
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

    const clone = await sbx.git.clone('https://git.example/repo.git', { path: '/workspace/repo', branch: 'main', depth: 1, timeoutMs: 10_000 })
    await sbx.git.dangerouslyAuthenticate({ username: 'user', password: 'token', host: 'git.example.com', protocol: 'https', timeout: 5 })
    await sbx.git.configureUser('Watasu Test', 'test@watasu.local', { scope: 'local', path: '/workspace/repo' })
    await sbx.git.init('/workspace/repo', { initialBranch: 'main' })
    const status = await sbx.git.status('/workspace/repo')
    const branches = await sbx.git.branches('/workspace/repo')
    await sbx.git.createBranch('/workspace/repo', 'feature/test')
    await sbx.git.deleteBranch('/workspace/repo', 'feature/test', { force: true })
    await sbx.git.add('/workspace/repo', { files: ['README.md'] })
    await sbx.git.commit('/workspace/repo', 'change', { authorName: 'Watasu Test', authorEmail: 'test@watasu.local', allowEmpty: true })
    await sbx.git.reset('/workspace/repo', { mode: 'hard', target: 'HEAD', paths: ['README.md'] })
    await sbx.git.restore('/workspace/repo', { paths: ['README.md'], staged: true })
    await sbx.git.pull('/workspace/repo', { remote: 'origin', branch: 'main', username: 'user', password: 'token' })
    await sbx.git.push('/workspace/repo', { remote: 'origin', branch: 'main', setUpstream: true, username: 'user', password: 'token' })
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
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/clone', { url: 'https://git.example/repo.git', timeout_seconds: 10, path: '/workspace/repo', branch: 'main', depth: 1 }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/dangerously_authenticate', { timeout_seconds: 5, username: 'user', password: 'token', host: 'git.example.com', protocol: 'https' }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/configure_user', { name: 'Watasu Test', email: 'test@watasu.local', scope: 'local', path: '/workspace/repo' }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/init', { path: '/workspace/repo', initial_branch: 'main' }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/status', { path: '/workspace/repo' }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/branches', { path: '/workspace/repo' }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/create_branch', { path: '/workspace/repo', branch: 'feature/test' }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/delete_branch', { path: '/workspace/repo', branch: 'feature/test', force: true }],
      ['POST', 'https://route.sandbox.watasuhost.com/runtime/v1/git/add', { path: '/workspace/repo', files: ['README.md'] }],
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
