#!/usr/bin/env node
import { pathToFileURL } from 'node:url'

// Exchange short-lived identities only. Never upload a package or log a token.
export async function checkPublishAuth(env = process.env, request = fetch) {
  const failures = []
  const headers = { 'Content-Type': 'application/json', 'User-Agent': 'WatasuSDK-release' }

  async function json(url, options = {}) {
    const response = await request(url, {
      ...options, redirect: 'error', signal: AbortSignal.timeout(15_000),
    })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    return response.json()
  }

  async function identity(audience) {
    if (!env.ACTIONS_ID_TOKEN_REQUEST_URL || !env.ACTIONS_ID_TOKEN_REQUEST_TOKEN) {
      throw new Error('GitHub Actions OIDC credentials unavailable')
    }
    const url = new URL(env.ACTIONS_ID_TOKEN_REQUEST_URL)
    url.searchParams.set('audience', audience)
    const result = await json(url, {
      headers: { Authorization: `Bearer ${env.ACTIONS_ID_TOKEN_REQUEST_TOKEN}` },
    })
    if (typeof result.value !== 'string' || !result.value) throw new Error('OIDC token unavailable')
    return result.value
  }

  const checks = [
    ['PyPI trusted publisher', async () => json('https://pypi.org/_/oidc/mint-token', {
      method: 'POST', headers, body: JSON.stringify({ token: await identity('pypi') }),
    })],
    ...['@watasu/sdk', '@watasu/code-interpreter'].map(name => [
      `npm ${name}`, async () => json(
        `https://registry.npmjs.org/-/npm/v1/oidc/token/exchange/package/${encodeURIComponent(name)}`,
        { method: 'POST', headers: { ...headers, Authorization: `Bearer ${await identity('npm:registry.npmjs.org')}` } },
      ),
    ]),
    ['crates.io trusted publisher', async () => json('https://crates.io/api/v1/trusted_publishing/tokens', {
      method: 'POST', headers, body: JSON.stringify({ jwt: await identity('crates.io') }),
    })],
  ]

  for (const [name, check] of checks) {
    try {
      const result = await check()
      if (typeof result.token !== 'string' || !result.token) throw new Error('token unavailable')
    } catch {
      // Registry errors may echo credentials. Only emit the fixed check name.
      failures.push(name)
    }
  }
  return failures
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const failures = await checkPublishAuth()
  if (failures.length) {
    console.error(`Registry authentication failed: ${failures.join(', ')}`)
    process.exitCode = 1
  } else {
    console.log('All registry identity exchanges succeeded; no packages were uploaded.')
  }
}
