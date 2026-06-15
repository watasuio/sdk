# Watasu TypeScript SDK

TypeScript SDK for Watasu.

## Install

```bash
npm install @watasu/sdk
```

Set `WATASU_API_KEY` before using the SDK.

## Usage

```ts
import { Sandbox } from '@watasu/sdk'

const sbx = await Sandbox.create()
await sbx.filesystem.write('/home/user/a.js', 'console.log(2 + 2)')
const result = await sbx.process.startAndWait('node /home/user/a.js')
console.log(result.stdout)
console.log(await sbx.isRunning())
await sbx.kill()
```

`Sandbox.create` and `Sandbox.connect` return only after the Watasu API supplies
a usable data-plane session. The SDK does not poll sandbox readiness.

## Git, Watch, PTY, And Signed File URLs

```ts
const sbx = await Sandbox.create()

await sbx.git.clone('https://github.com/acme/project.git', {
  path: '/workspace/project',
  branch: 'main',
  depth: 1,
})
const status = await sbx.git.status('/workspace/project')
await sbx.git.configureUser('Watasu Bot', 'bot@watasu.local', {
  scope: 'local',
  path: '/workspace/project',
})
await sbx.git.createBranch('/workspace/project', 'feature/docs')
await sbx.git.add('/workspace/project', { files: ['README.md'] })
await sbx.git.commit('/workspace/project', 'Update docs', {
  authorName: 'Watasu Bot',
  authorEmail: 'bot@watasu.local',
})
await sbx.git.push('/workspace/project', {
  remote: 'origin',
  branch: 'feature/docs',
  setUpstream: true,
})

await sbx.filesystem.writeFiles([
  { path: '/workspace/project/a.txt', data: 'alpha' },
  { path: '/workspace/project/b.bin', data: new Uint8Array([0, 1, 2]) },
])

const watcher = sbx.filesystem.watchDir('/workspace/project')
watcher.addEventListener((event) => {
  console.log(event.type, event.path)
})
await watcher.start({ recursive: true })

const terminal = await sbx.terminal.start({
  size: { cols: 100, rows: 30 },
  onData: (data) => process.stdout.write(data),
})
await terminal.sendData('echo hello\n')

const uploadUrl = await sbx.uploadUrl('/workspace/input.bin')
const downloadUrl = await sbx.downloadUrl('/workspace/output.bin')

watcher.stop()
await terminal.kill()
await sbx.kill()
```

## Metrics And Snapshots

```ts
import { Sandbox } from '@watasu/sdk'

const sbx = await Sandbox.create()
const metrics = await sbx.getMetrics()
const snapshot = await sbx.createSnapshot({ name: 'ready' })
const snapshots = await sbx.listSnapshots().nextItems()
const restored = await sbx.restore({ snapshotId: snapshot.snapshotId })
await sbx.deleteSnapshot(snapshot.snapshotId)
await sbx.kill()
```

Watasu snapshots are backed by sandbox checkpoints. Use the returned
`snapshotId` when restoring from a checkpoint.

The SDK is ESM-first and ships TypeScript declarations.
