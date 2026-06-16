import Sandbox, {
  Context,
  Execution,
  Result,
  type RunCodeOpts,
} from '../dist/index.js'

const sandboxCtor: typeof Sandbox = Sandbox
const context = new Context('ctx-1', 'python', '/workspace')
const result = new Result({ text: 'ok' })
const execution = new Execution([result])
const opts: RunCodeOpts & { context?: Context } = { context }

void sandboxCtor
void execution
void opts
