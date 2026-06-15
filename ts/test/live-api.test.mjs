import assert from 'node:assert/strict'
import test from 'node:test'

import { Sandbox, Template } from '../dist/index.js'

const live = process.env.WATASU_LIVE_API_TESTS === '1'

test('live snapshot list shape', { skip: live ? false : 'set WATASU_LIVE_API_TESTS=1 to run live API smoke tests' }, async () => {
  const snapshots = await Sandbox.listSnapshots({ limit: 2 }).nextItems()

  assert.ok(Array.isArray(snapshots))
})

test('live template helpers expose platform template aliases', { skip: live ? false : 'set WATASU_LIVE_API_TESTS=1 to run live API smoke tests' }, async () => {
  assert.equal(await Template.exists('base'), true)
  assert.equal(await Template.exists('watasu-live-missing-template'), false)
  assert.ok(Array.isArray(await Template.getTags('base')))
})
