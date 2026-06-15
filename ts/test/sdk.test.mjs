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

test('connection config accepts E2B auth aliases', () => {
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

test('sandbox getHost is sync and E2B-shaped', () => {
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
