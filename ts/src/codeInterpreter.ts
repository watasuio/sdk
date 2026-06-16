import { InvalidArgumentError } from './errors.js'
import { Sandbox as BaseSandbox } from './sandbox.js'

export {
  ApiError,
  AuthenticationError,
  BuildError,
  ConflictError,
  FileNotFoundError,
  FileUploadError,
  GitAuthError,
  GitUpstreamError,
  InvalidArgumentError,
  NotEnoughSpaceError,
  NotFoundError,
  RateLimitError,
  SandboxError,
  SandboxNotFoundError,
  TemplateError,
  TimeoutError,
  VolumeError,
} from './errors.js'
export { ConnectionConfig, KEEPALIVE_PING_INTERVAL_SEC } from './connectionConfig.js'
export type { ConnectionOpts, Username } from './connectionConfig.js'
export { ControlClient as ApiClient } from './transport.js'
export {
  ALL_TRAFFIC,
  SandboxPaginator,
  SnapshotPaginator,
  getSignature,
} from './sandbox.js'
export type {
  CreateSnapshotOpts,
  FileUrlInfo,
  McpServer,
  McpServerName,
  RestoreSnapshotOpts,
  SandboxApiOpts,
  SandboxConnectOpts,
  SandboxCreateOpts,
  SandboxInfo,
  SandboxInfoLifecycle,
  SandboxLifecycle,
  SandboxListOpts,
  SandboxMetrics,
  SandboxMetricsOpts,
  SandboxNetworkInfo,
  SandboxNetworkOpts,
  SandboxNetworkRule,
  SandboxNetworkRuleInfo,
  SandboxNetworkRules,
  SandboxNetworkSelector,
  SandboxNetworkSelectorContext,
  SandboxNetworkTransform,
  SandboxNetworkUpdate,
  SandboxNetworkUpdateOpts,
  SandboxOpts,
  SandboxState,
  SandboxUrlOpts,
  SignatureOpts,
  SnapshotInfo,
  SnapshotListOpts,
} from './sandbox.js'
export { CommandExitError, CommandHandle, Commands } from './commands.js'
export type {
  CommandConnectOpts,
  CommandRequestOpts,
  CommandResult,
  CommandStartOpts,
  ProcessInfo,
  PtyOutput,
  Stderr,
  Stdout,
} from './commands.js'
export { Process, ProcessManager, ProcessMessage, ProcessOutput } from './process.js'
export type { ProcessOpts } from './process.js'
export {
  FileType,
  Filesystem,
  FilesystemEventType,
  FilesystemWatcher,
  WatchHandle,
} from './filesystem.js'
export type {
  EntryInfo,
  FilesystemEvent,
  FilesystemReadOpts,
  FilesystemRequestOpts,
  FilesystemWriteOpts,
  WatchOpts,
  WriteData,
  WriteEntry,
  WriteInfo,
} from './filesystem.js'
export { Git } from './git.js'
export type {
  GitAddOpts,
  GitAuthOpts,
  GitBranches,
  GitBranchOpts,
  GitCloneOpts,
  GitCommandResult,
  GitCommitOpts,
  GitConfigScope,
  GitConfigOpts,
  GitConfigureUserOpts,
  GitCredentialOpts,
  GitDangerouslyAuthenticateOpts,
  GitDeleteBranchOpts,
  GitFileStatus,
  GitInitOpts,
  GitPullOpts,
  GitPushOpts,
  GitRemoteAddOpts,
  GitResetMode,
  GitResetOpts,
  GitRestoreOpts,
  GitStatus,
} from './git.js'
export { Pty } from './pty.js'
export type { PtyConnectOpts, PtyCreateOpts, PtySize } from './pty.js'
export { Terminal, TerminalManager, TerminalOutput } from './terminal.js'
export type { TerminalOpts } from './terminal.js'
export { Volume, VolumeConnectionConfig, VolumeFileType } from './volume.js'
export type {
  VolumeAndToken,
  VolumeApiParams,
  VolumeApiOpts,
  VolumeEntryStat,
  VolumeInfo,
  VolumeListFilesOpts,
  VolumeListOpts,
  VolumeMetadataOpts,
  VolumeMetadataOptions,
  VolumeReadFileOpts,
  VolumeReadFormat,
  VolumeWriteData,
  VolumeWriteFileOpts,
  VolumeWriteOptions,
} from './volume.js'
export { ProcessSocket, base64DecodeBytes, base64DecodeText, base64Encode } from './processSocket.js'
export {
  ReadyCmd,
  Template,
  TemplateBase,
  LogEntry,
  LogEntryEnd,
  LogEntryStart,
  defaultBuildLogger,
  waitForFile,
  waitForPort,
  waitForProcess,
  waitForTimeout,
  waitForURL,
} from './template.js'
export type {
  BuildInfo,
  BuildOptions,
  BuildStatusReason,
  CopyItem,
  GetBuildStatusOptions,
  LogEntryLevel,
  Logger,
  ReadyCommand,
  TemplateBuilder,
  TemplateBuildStatus,
  TemplateBuildStatusResponse,
  TemplateClass,
  TemplateFactory,
  TemplateFinal,
  TemplateFromImage,
  TemplateOptions,
  TemplateTag,
  TemplateTagInfo,
} from './template.js'
export type { components, paths } from './index.js'

export type RunCodeLanguage =
  | 'python'
  | 'javascript'
  | 'typescript'
  | 'r'
  | 'java'
  | 'bash'
  | (string & {})
type OutputHandler<T> = (output: T) => Promise<unknown> | unknown

export interface RunCodeOpts {
  onStdout?: OutputHandler<OutputMessage>
  onStderr?: OutputHandler<OutputMessage>
  onResult?: OutputHandler<Result>
  onError?: OutputHandler<ExecutionError>
  envs?: Record<string, string>
  timeoutMs?: number
  requestTimeoutMs?: number
  signal?: AbortSignal
}

export interface CreateCodeContextOpts {
  cwd?: string
  language?: RunCodeLanguage
  requestTimeoutMs?: number
  signal?: AbortSignal
}

/** Chart types returned by code execution results. */
export enum ChartType {
  LINE = 'line',
  SCATTER = 'scatter',
  BAR = 'bar',
  PIE = 'pie',
  BOX_AND_WHISKER = 'box_and_whisker',
  SUPERCHART = 'superchart',
  UNKNOWN = 'unknown',
}

/** Axis scale types returned by chart results. */
export enum ScaleType {
  LINEAR = 'linear',
  DATETIME = 'datetime',
  CATEGORICAL = 'categorical',
  LOG = 'log',
  SYMLOG = 'symlog',
  LOGIT = 'logit',
  FUNCTION = 'function',
  FUNCTIONLOG = 'functionlog',
  ASINH = 'asinh',
}

export type Chart = {
  type: ChartType
  title: string
  elements: unknown[]
}

export type Chart2D = Chart & {
  x_label?: string
  y_label?: string
  x_unit?: string
  y_unit?: string
}

export type PointData = {
  label: string
  points: [number | string, number | string][]
}

export type PointChart = Chart2D & {
  x_ticks: (number | string)[]
  x_scale: ScaleType
  x_tick_labels: string[]
  y_ticks: (number | string)[]
  y_scale: ScaleType
  y_tick_labels: string[]
  elements: PointData[]
}

export type LineChart = PointChart & {
  type: ChartType.LINE
}

export type ScatterChart = PointChart & {
  type: ChartType.SCATTER
}

export type BarData = {
  label: string
  value: string
  group: string
}

export type BarChart = Chart2D & {
  type: ChartType.BAR
  elements: BarData[]
}

export type PieData = {
  label: string
  angle: number
  radius: number
}

export type PieChart = Chart & {
  type: ChartType.PIE
  elements: PieData[]
}

export type BoxAndWhiskerData = {
  label: string
  min: number
  first_quartile: number
  median: number
  third_quartile: number
  max: number
  outliers: number[]
}

export type BoxAndWhiskerChart = Chart2D & {
  type: ChartType.BOX_AND_WHISKER
  elements: BoxAndWhiskerData[]
}

export type SuperChart = Chart & {
  type: ChartType.SUPERCHART
  elements: Chart[]
}

export type ChartTypes = LineChart | ScatterChart | BarChart | PieChart | BoxAndWhiskerChart | SuperChart
export type MIMEType = string
export type RawData = Record<string, unknown>

/** One stdout or stderr line emitted by code execution. */
export class OutputMessage {
  constructor(
    readonly line: string,
    readonly timestamp = Date.now() / 1000,
    readonly error = false
  ) {}

  toString(): string {
    return this.line
  }
}

/** Structured exception raised by user code inside the sandbox. */
export class ExecutionError {
  constructor(
    readonly name: string,
    readonly value: string,
    readonly traceback: string
  ) {}
}

/** Rich result produced by the last expression of a code execution. */
export class Result {
  readonly raw: RawData
  readonly text?: string
  readonly html?: string
  readonly markdown?: string
  readonly svg?: string
  readonly png?: string
  readonly jpeg?: string
  readonly pdf?: string
  readonly latex?: string
  readonly json?: unknown
  readonly javascript?: string
  readonly data?: Record<string, unknown>
  readonly chart?: ChartTypes
  readonly extra: Record<string, unknown>
  readonly isMainResult: boolean

  constructor(payload: RawData = {}, isMainResult?: boolean) {
    this.raw = payload
    this.text = stringValue(payload.text)
    this.html = stringValue(payload.html)
    this.markdown = stringValue(payload.markdown)
    this.svg = stringValue(payload.svg)
    this.png = stringValue(payload.png)
    this.jpeg = stringValue(payload.jpeg)
    this.pdf = stringValue(payload.pdf)
    this.latex = stringValue(payload.latex)
    this.json = payload.json
    this.javascript = stringValue(payload.javascript)
    this.data = recordOrUndefined(payload.data)
    this.chart = chartFromApi(payload.chart)
    this.extra = resultExtra(payload)
    this.isMainResult = Boolean(isMainResult ?? payload.is_main_result ?? payload.isMainResult)
  }

  formats(): string[] {
    const names = ['text', 'html', 'markdown', 'svg', 'png', 'jpeg', 'pdf', 'latex', 'json', 'javascript', 'data', 'chart']
    const formats = names.filter((name) => (this as unknown as Record<string, unknown>)[name] !== undefined)
    formats.push(...Object.keys(this.extra))
    return formats
  }

  toJSON(): Record<string, unknown> {
    return compactRecord({
      text: this.text,
      html: this.html,
      markdown: this.markdown,
      svg: this.svg,
      png: this.png,
      jpeg: this.jpeg,
      pdf: this.pdf,
      latex: this.latex,
      json: this.json,
      javascript: this.javascript,
      extra: Object.keys(this.extra).length === 0 ? undefined : this.extra,
    })
  }
}

export interface Logs {
  stdout: string[]
  stderr: string[]
}

/** Complete result of a sandbox code execution. */
export class Execution {
  constructor(
    readonly results: Result[] = [],
    readonly logs: Logs = { stdout: [], stderr: [] },
    readonly error: ExecutionError | undefined = undefined,
    readonly executionCount: number | undefined = undefined
  ) {}

  get text(): string | undefined {
    return this.results.find((result) => result.isMainResult && result.text !== undefined)?.text ??
      this.results.find((result) => result.text !== undefined)?.text
  }

  toJSON(): Record<string, unknown> {
    return {
      results: this.results,
      logs: this.logs,
      error: this.error,
    }
  }
}

/** Code execution context metadata. */
export class Context {
  constructor(
    readonly id: string,
    readonly language: string = '',
    readonly cwd: string = ''
  ) {}
}

/** Sandbox specialized for running Python code. */
export class Sandbox extends BaseSandbox {
  protected static readonly defaultTemplate: string = 'code-interpreter'

  protected get jupyterUrl(): string {
    return this.runtimeBaseUrl
  }

  /** Run Python code in the sandbox and return structured execution output. */
  async runCode(code: string, opts?: RunCodeOpts & {
    /**
     * Language to use for code execution.
     *
     * If not defined, the default Python context is used.
     */
    language?: RunCodeLanguage
  }): Promise<Execution>
  async runCode(code: string, opts?: RunCodeOpts & {
    /**
     * Context to run the code in.
     */
    context?: Context
  }): Promise<Execution>
  async runCode(
    code: string,
    opts: RunCodeOpts & { language?: RunCodeLanguage; context?: Context | string } = {}
  ): Promise<Execution> {
    if (typeof code !== 'string') throw new InvalidArgumentError('code must be a string')
    if (opts.language !== undefined && opts.context !== undefined) {
      throw new InvalidArgumentError('language and context cannot both be set')
    }

    const payload = compactRecord({
      code,
      language: opts.language,
      context_id: contextId(opts.context),
      env_vars: opts.envs,
      timeout_ms: opts.timeoutMs,
    })
    const response = await this.runtimePostJson('/runtime/v1/code/run', payload, {
      requestTimeoutMs: opts.requestTimeoutMs,
      signal: opts.signal,
    })
    const execution = executionFromApi(response)
    await emitCallbacks(execution, opts)
    return execution
  }

  /** Create a persistent code context. */
  async createCodeContext(opts: CreateCodeContextOpts = {}): Promise<Context> {
    const response = await this.runtimePostJson('/runtime/v1/code/contexts', compactRecord({
      cwd: opts.cwd,
      language: opts.language,
    }), {
      requestTimeoutMs: opts.requestTimeoutMs,
      signal: opts.signal,
    })
    return contextFromApi(response)
  }

  /** Remove a persistent code context. */
  async removeCodeContext(context: Context | string): Promise<void>
  async removeCodeContext(context: Context | string, opts: { requestTimeoutMs?: number; signal?: AbortSignal } = {}): Promise<void> {
    await this.runtimeDeleteJson(`/runtime/v1/code/contexts/${encodeURIComponent(requireContextId(context))}`, {
      requestTimeoutMs: opts.requestTimeoutMs,
      signal: opts.signal,
    })
  }

  /** List persistent code contexts. */
  async listCodeContexts(): Promise<Context[]>
  async listCodeContexts(opts: { requestTimeoutMs?: number; signal?: AbortSignal } = {}): Promise<Context[]> {
    const response = await this.runtimeGetJson('/runtime/v1/code/contexts', {
      requestTimeoutMs: opts.requestTimeoutMs,
      signal: opts.signal,
    })
    const contexts = Array.isArray(response) ? response : arrayOfUnknown(response.contexts)
    return contexts.map((item) => contextFromApi(record(item)))
  }

  /** Restart a persistent code context. */
  async restartCodeContext(context: Context | string): Promise<void>
  async restartCodeContext(context: Context | string, opts: { requestTimeoutMs?: number; signal?: AbortSignal } = {}): Promise<void> {
    await this.runtimePostJson(`/runtime/v1/code/contexts/${encodeURIComponent(requireContextId(context))}/restart`, {}, {
      requestTimeoutMs: opts.requestTimeoutMs,
      signal: opts.signal,
    })
  }
}

export { Sandbox as default }

function executionFromApi(payload: Record<string, unknown>): Execution {
  const execution = record(payload.execution ?? payload)
  const logs = record(execution.logs)
  return new Execution(
    arrayOfRecords(execution.results).map((item) => new Result(item)),
    {
      stdout: arrayOfUnknown(logs.stdout).map(outputLineFromApi),
      stderr: arrayOfUnknown(logs.stderr).map(outputLineFromApi),
    },
    executionErrorFromApi(execution.error),
    numberValue(execution.execution_count ?? execution.executionCount)
  )
}

function outputLineFromApi(value: unknown): string {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const item = value as Record<string, unknown>
    return String(item.line ?? item.text ?? '')
  }
  return String(value)
}

function executionErrorFromApi(value: unknown): ExecutionError | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const item = value as Record<string, unknown>
  return new ExecutionError(
    String(item.name ?? ''),
    String(item.value ?? ''),
    String(item.traceback ?? '')
  )
}

async function emitCallbacks(execution: Execution, opts: RunCodeOpts): Promise<void> {
  for (const message of execution.logs.stdout) await opts.onStdout?.(new OutputMessage(message, Date.now() / 1000, false))
  for (const message of execution.logs.stderr) await opts.onStderr?.(new OutputMessage(message, Date.now() / 1000, true))
  for (const result of execution.results) await opts.onResult?.(result)
  if (execution.error !== undefined) await opts.onError?.(execution.error)
}

function contextId(context: Context | string | undefined): string | undefined {
  if (context === undefined) return undefined
  return requireContextId(context)
}

function requireContextId(context: Context | string): string {
  if (typeof context === 'string') return context
  return context.id
}

function contextFromApi(payload: Record<string, unknown>): Context {
  return new Context(
    String(payload.id ?? ''),
    stringValue(payload.language) ?? '',
    stringValue(payload.cwd) ?? ''
  )
}

function compactRecord(payload: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(payload).filter(([, value]) => value !== undefined))
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function resultExtra(payload: Record<string, unknown>): Record<string, unknown> {
  const extra = record(payload.extra)
  for (const [key, value] of Object.entries(payload)) {
    if (!knownResultKeys.has(key)) extra[key] = value
  }
  return extra
}

const knownResultKeys = new Set([
  'plain',
  'text',
  'html',
  'markdown',
  'svg',
  'png',
  'jpeg',
  'pdf',
  'latex',
  'json',
  'javascript',
  'data',
  'chart',
  'extra',
  'type',
  'is_main_result',
  'isMainResult',
])

function recordOrUndefined(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined
}

function chartFromApi(value: unknown): ChartTypes | undefined {
  const item = recordOrUndefined(value)
  if (item === undefined) return undefined
  return item as ChartTypes
}

function arrayOfRecords(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map((item) => record(item)) : []
}

function arrayOfUnknown(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' ? value : undefined
}
