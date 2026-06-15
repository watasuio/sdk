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
const result = await sbx.commands.run('node /home/user/a.js')
console.log(result.stdout)
console.log(await sbx.isRunning())
await sbx.kill()
```

`Sandbox.create` and `Sandbox.connect` return only after the Watasu API supplies
a usable data-plane session. The SDK does not poll sandbox readiness.

The SDK is ESM-first and ships TypeScript declarations.
