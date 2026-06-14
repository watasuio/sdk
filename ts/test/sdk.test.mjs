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
