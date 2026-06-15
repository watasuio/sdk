import assert from 'node:assert/strict'
import test from 'node:test'

import {
  CommandExitError,
  CommandHandle,
  ConnectionConfig,
  ProcessSocket,
  Sandbox,
  SandboxError,
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
    })

    assert.equal(sbx.sandboxId, 'created')
    assert.deepEqual(requests[0].body, {
      template_id: 'base:82',
      timeout: 120,
      metadata: { purpose: 'compat' },
      env_vars: { HELLO: 'world' },
      secure: true,
      allow_internet_access: true,
      team: 'bridgeapp',
    })
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
      if (String(url).endsWith('/connect')) {
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
      ['POST', 'https://api.watasu.io/v1/sandboxes/existing/connect', { timeout: 90 }],
      ['POST', 'https://api.watasu.io/v1/sandboxes/existing/timeout', { timeout: 180 }],
    ])
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
      if (String(url).endsWith('/checkpoints') && init.method === 'POST') {
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

    assert.equal(metrics[0].backend, 'firecracker')
    assert.equal(snapshot.snapshotId, '9')
    assert.equal(snapshots[0].status, 'ready')
    assert.equal(restored.sandboxId, 'restored')
    assert.deepEqual(requests.map((request) => [request.method, request.url, request.body]), [
      ['GET', 'https://api.watasu.io/v1/sandboxes/1/metrics', undefined],
      ['POST', 'https://api.watasu.io/v1/sandboxes/1/checkpoints', { name: 'ready', metadata: { reason: 'test' } }],
      ['GET', 'https://api.watasu.io/v1/sandboxes/1/checkpoints', undefined],
      ['POST', 'https://api.watasu.io/v1/sandboxes/1/restore', { checkpoint_id: '9', timeout_seconds: 120 }],
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})
