import DefaultSandbox, {
  Sandbox,
  type components,
  type paths,
} from '../dist/index.js'
import DefaultCodeInterpreterSandbox, {
  Sandbox as CodeInterpreterSandbox,
  type Context,
  type RunCodeOpts,
} from '../dist/codeInterpreter.js'

const sandboxCtor: typeof Sandbox = DefaultSandbox
const codeInterpreterCtor: typeof CodeInterpreterSandbox = DefaultCodeInterpreterSandbox

type ApiPaths = paths
type ApiSchemas = components['schemas']

const languageOpts: RunCodeOpts & { language?: 'python' } = { language: 'python' }
const contextOpts: RunCodeOpts & { context?: Context } = {} as RunCodeOpts & { context?: Context }

void sandboxCtor
void codeInterpreterCtor
void ({} as ApiPaths)
void ({} as ApiSchemas)
void languageOpts
void contextOpts
