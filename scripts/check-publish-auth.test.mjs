import assert from 'node:assert/strict'
import test from 'node:test'
import { checkPublishAuth } from './check-publish-auth.mjs'

const env = {
  ACTIONS_ID_TOKEN_REQUEST_URL: 'https://token.actions.githubusercontent.com/token?api-version=2',
  ACTIONS_ID_TOKEN_REQUEST_TOKEN: 'test-github-credential',
}

test('checks every registry through the expected OIDC audience without uploading', async () => {
  const exchanges = []
  const audiences = []
  const result = await checkPublishAuth(env, async (url, options) => {
    url = new URL(url)
    assert.equal(options.redirect, 'error')
    if (url.hostname === 'token.actions.githubusercontent.com') {
      assert.equal(options.headers.Authorization, 'Bearer test-github-credential')
      audiences.push(url.searchParams.get('audience'))
      return { ok: true, json: async () => ({ value: 'test-oidc' }) }
    }
    assert.equal(options.method, 'POST')
    exchanges.push(url.href)
    return { ok: true, json: async () => ({ token: 'test-ephemeral-token' }) }
  })
  assert.deepEqual(result, [])
  assert.deepEqual(audiences, ['pypi', 'npm:registry.npmjs.org', 'npm:registry.npmjs.org', 'crates.io'])
  assert.deepEqual(exchanges, [
    'https://pypi.org/_/oidc/mint-token',
    'https://registry.npmjs.org/-/npm/v1/oidc/token/exchange/package/%40watasu%2Fsdk',
    'https://registry.npmjs.org/-/npm/v1/oidc/token/exchange/package/%40watasu%2Fcode-interpreter',
    'https://crates.io/api/v1/trusted_publishing/tokens',
  ])
})

test('reports each rejected registry without leaking response or exception credentials', async () => {
  const result = await checkPublishAuth(env, async url => {
    if (new URL(url).hostname === 'token.actions.githubusercontent.com') {
      return { ok: true, json: async () => ({ value: 'test-oidc' }) }
    }
    throw new Error('private-token-in-upstream-error')
  })
  assert.deepEqual(result, [
    'PyPI trusted publisher', 'npm @watasu/sdk', 'npm @watasu/code-interpreter', 'crates.io trusted publisher',
  ])
})
