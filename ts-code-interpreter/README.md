# Watasu Code Interpreter TypeScript SDK

TypeScript package for Watasu code interpreter sandboxes.

## Install

```bash
npm install @watasu/code-interpreter
```

Set `WATASU_API_KEY` before using the SDK.

## Usage

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

`Sandbox.create()` starts the `code-interpreter` template by default. Code runs
in persistent contexts and returns structured `results`, `logs`, and `error`
fields for each execution.
