import DefaultSandbox, {
  Sandbox,
  type GitCloneOpts,
  type GitDeleteBranchOpts,
  type GitPullOpts,
  type GitPushOpts,
  type SandboxOpts,
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
const sandboxOpts: SandboxOpts = {
  template: 'base',
  timeoutMs: 300_000,
  metadata: { owner: 'watasu' },
  envs: { A: 'B' },
  secure: true,
  allowInternetAccess: true,
  network: { allowOut: ['0.0.0.0/0'] },
  volumeMounts: { '/workspace': 'workspace' },
}
const gitCloneOpts: GitCloneOpts = { username: 'user', password: 'token' }
const gitPushOpts: GitPushOpts = { username: 'user', password: 'token', setUpstream: true }
const gitPullOpts: GitPullOpts = { username: 'user', password: 'token' }
const gitDeleteBranchOpts: GitDeleteBranchOpts = { force: true }

void sandboxCtor
void codeInterpreterCtor
void ({} as ApiPaths)
void ({} as ApiSchemas)
void languageOpts
void contextOpts
void sandboxOpts
void gitCloneOpts
void gitPushOpts
void gitPullOpts
void gitDeleteBranchOpts
