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
await sbx.files.write('/home/user/a.js', 'console.log(2 + 2)')
const result = await sbx.process.startAndWait('node /home/user/a.js')
console.log(result.stdout)
console.log(await sbx.isRunning())
await sbx.kill()
```

`Sandbox.create` and `Sandbox.connect` return only after the Watasu API supplies
a usable data-plane session. The SDK does not poll sandbox readiness.

```ts
await sbx.betaPause()
await sbx.resume({ timeoutMs: 300_000 })
```

Choose what happens when the sandbox timeout expires:

```ts
const sbx = await Sandbox.create({
  lifecycle: { onTimeout: 'pause', autoResume: true },
})
```

`onTimeout: 'kill'` is the default. `onTimeout: 'pause'` keeps the retained
disk after timeout; `autoResume` lets a later data-plane request resume that
paused sandbox automatically.

Mount a named persistent volume when the sandbox starts:

```ts
const sbx = await Sandbox.create({
  volumeMounts: {
    '/workspace/cache': 'cache',
    '/data/models': { name: 'models' },
  },
})
```

Create and edit a persistent volume while it is detached:

```ts
import { Volume } from '@watasu/sdk'

const volume = await Volume.create('cache')
await volume.makeDir('/workspace')
await volume.writeFile('/workspace/status.txt', 'ready\n', { mode: '0644' })
console.log(await volume.readFile('/workspace/status.txt'))
console.log((await volume.list('/workspace')).map((entry) => entry.path))
await volume.remove('/workspace/status.txt')
await volume.destroy()
```

## Code Interpreter

```ts
import { Sandbox } from '@watasu/code-interpreter'

const sbx = await Sandbox.create()
const context = await sbx.createCodeContext()
const execution = await sbx.runCode("print('hello')\n2 + 3", {
  context,
  onStdout: (message) => console.log(message.line),
})

console.log(execution.text)
await sbx.removeCodeContext(context)
await sbx.kill()
```

`@watasu/code-interpreter` starts the `code-interpreter` template by default.
Code runs in persistent Python contexts and returns structured `results`, `logs`,
and `error` fields for each execution.

## MCP Gateway

```ts
const sbx = await Sandbox.create({
  mcp: {
    github: {
      command: 'github-mcp-server',
      args: ['stdio'],
      env: { GITHUB_TOKEN: process.env.GITHUB_TOKEN },
    },
  },
})

console.log(sbx.getMcpUrl())
console.log(await sbx.getMcpToken())
```

## Listing Sandboxes

```ts
import { Sandbox } from '@watasu/sdk'

const paginator = Sandbox.list({
  query: { metadata: { purpose: 'ci' }, state: ['running'] },
  limit: 20,
})

for (const sandbox of await paginator.listItems()) {
  console.log(sandbox.sandboxId, sandbox.state)
}
```

## Git, Watch, PTY, And Signed File URLs

```ts
const sbx = await Sandbox.create()

await sbx.git.init('/workspace/new-project', { initialBranch: 'main' })
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
const remoteUrl = await sbx.git.remoteGet('/workspace/project', 'origin')
await sbx.git.restore('/workspace/project', { paths: ['README.md'] })
await sbx.git.reset('/workspace/project', { mode: 'hard', target: 'HEAD' })

await sbx.files.writeFiles([
  { path: '/workspace/project/a.txt', data: 'alpha' },
  { path: '/workspace/project/b.bin', data: new Uint8Array([0, 1, 2]) },
])
const patch = await sbx.files.applyDiff(
  `*** Begin Patch
*** Update File: /workspace/project/a.txt
@@
-alpha
+beta
*** End Patch`
)
console.log(patch.status)

const watcher = sbx.files.watchDir('/workspace/project')
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

## Network Policy

```ts
const sbx = await Sandbox.create({
  network: {
    allowOut: ({ rules }) => [...rules.keys(), 'pypi.org:443'],
    denyOut: ['169.254.169.254'],
    rules: {
      'api.example.com': [
        { transform: { headers: { authorization: 'Bearer token' } } },
      ],
    },
    maskRequestHost: '${PORT}-sandbox.example.com',
  },
})

await sbx.updateNetwork({
  allowInternetAccess: false,
  allowPackageRegistryAccess: true,
  allowOut: ['registry.npmjs.org:443'],
})
```

## Template Builds

```ts
import { Template } from '@watasu/sdk'

const template = Template()
  .fromPythonImage('3.12')
  .copy('requirements.txt', '/workspace/requirements.txt')
  .aptInstall(['git'])
  .pipInstall(['pytest'])
  .setEnvs({ PIP_DISABLE_PIP_VERSION_CHECK: '1' })
  .runCmd('echo ready')

const build = await Template.buildInBackground(template, 'python-ci:stable', {
  tags: ['stable'],
  cpuCount: 2,
  memoryMB: 2048,
})
const status = await Template.getBuildStatus(build)

await Template.assignTags('python-ci:stable', ['prod'])
console.log(await Template.exists('python-ci'))
```

Template names resolve server-side. `python-ci` starts the latest ready build;
`python-ci:stable` starts the tagged build.

`Template({ fileContextPath: process.cwd() }).fromDockerfile('Dockerfile')`
parses common `FROM`, `WORKDIR`, `COPY`, `RUN`, `ENV`, `CMD`, and `ENTRYPOINT`
instructions into Watasu's package-spec builder.

## Metrics And Snapshots

```ts
import { Sandbox } from '@watasu/sdk'

const sbx = await Sandbox.create()
const metrics = await sbx.getMetrics({
  start: new Date(Date.now() - 5 * 60_000),
  end: new Date(),
})
const snapshot = await sbx.createSnapshot({ name: 'ready' })
const snapshots = await sbx.listSnapshots().nextItems()
const allSnapshots = await Sandbox.listSnapshots({ limit: 100 }).nextItems()
const restored = await sbx.restore({ snapshotId: snapshot.snapshotId })
await sbx.deleteSnapshot(snapshot.snapshotId)
await sbx.kill()
```

Watasu snapshots are backed by sandbox checkpoints. Use the returned
`snapshotId` when restoring from a checkpoint.

The SDK is ESM-first and ships TypeScript declarations.
