import { InvalidArgumentError, NotImplementedError } from './errors.js'
import { Sandbox as BaseSandbox, SandboxConnectOpts, SandboxCreateOpts } from './sandbox.js'

export type RunCodeLanguage = 'python' | 'python3' | string

export interface RunCodeOpts {
  language?: RunCodeLanguage
  context?: Context
  onStdout?: (message: OutputMessage) => void
  onStderr?: (message: OutputMessage) => void
  onResult?: (result: Result) => void
  onError?: (error: ExecutionError) => void
  envs?: Record<string, string>
  timeout?: number
  requestTimeoutMs?: number
}

export interface CreateCodeContextOpts {
  cwd?: string
  language?: RunCodeLanguage
  requestTimeoutMs?: number
}

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

  toJSON(): Record<string, unknown> {
    return {
      line: this.line,
      timestamp: this.timestamp,
      error: this.error,
    }
  }
}

/** Structured exception raised by user code inside the sandbox. */
export class ExecutionError {
  constructor(
    readonly name: string,
    readonly value: string,
    readonly traceback: string
  ) {}

  toJSON(): Record<string, unknown> {
    return {
      name: this.name,
      value: this.value,
      traceback: this.traceback,
    }
  }
}

/** Rich result produced by the last expression of a code execution. */
export class Result {
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
  readonly data?: unknown
  readonly chart?: unknown
  readonly extra: Record<string, unknown>
  readonly isMainResult: boolean

  constructor(payload: Record<string, unknown> = {}) {
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
    this.data = payload.data
    this.chart = payload.chart
    this.extra = record(payload.extra)
    this.isMainResult = Boolean(payload.is_main_result ?? payload.isMainResult)
  }

  formats(): string[] {
    const names = ['text', 'html', 'markdown', 'svg', 'png', 'jpeg', 'pdf', 'latex', 'json', 'javascript', 'data', 'chart']
    return names.filter((name) => (this as unknown as Record<string, unknown>)[name] !== undefined)
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
      data: this.data,
      chart: this.chart,
      extra: Object.keys(this.extra).length === 0 ? undefined : this.extra,
      is_main_result: this.isMainResult,
    })
  }
}

export interface Logs {
  stdout: OutputMessage[]
  stderr: OutputMessage[]
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
      results: this.results.map((result) => result.toJSON()),
      logs: {
        stdout: this.logs.stdout.map((message) => message.toJSON()),
        stderr: this.logs.stderr.map((message) => message.toJSON()),
      },
      error: this.error?.toJSON() ?? null,
      execution_count: this.executionCount,
    }
  }
}

/** Code execution context metadata. */
export class Context {
  constructor(
    readonly id: string,
    readonly language?: string,
    readonly cwd?: string
  ) {}

  toJSON(): Record<string, unknown> {
    return compactRecord({
      id: this.id,
      language: this.language,
      cwd: this.cwd,
    })
  }
}

/** Sandbox specialized for running Python code. */
export class Sandbox extends BaseSandbox {
  static readonly defaultTemplate = 'code-interpreter'

  static async create(opts?: SandboxCreateOpts): Promise<Sandbox>
  static async create(template: string, opts?: SandboxCreateOpts): Promise<Sandbox>
  static async create(templateOrOpts?: string | SandboxCreateOpts, opts: SandboxCreateOpts = {}): Promise<Sandbox> {
    return await super.create(templateOrOpts as string & SandboxCreateOpts, opts) as Sandbox
  }

  static async connect(sandboxId: string, opts: SandboxConnectOpts = {}): Promise<Sandbox> {
    return await super.connect(sandboxId, opts) as Sandbox
  }

  /** Run Python code in the sandbox and return structured execution output. */
  async runCode(code: string, opts: RunCodeOpts = {}): Promise<Execution> {
    if (typeof code !== 'string') throw new InvalidArgumentError('code must be a string')
    if (opts.language !== undefined && opts.context !== undefined) {
      throw new InvalidArgumentError('language and context cannot both be set')
    }

    const payload = compactRecord({
      code,
      language: opts.language,
      context_id: contextId(opts.context),
      env_vars: opts.envs,
      timeout_seconds: opts.timeout,
    })
    const response = await this.runtimePostJson('/runtime/v1/code/run', payload, {
      requestTimeoutMs: opts.requestTimeoutMs,
    })
    const execution = executionFromApi(response)
    emitCallbacks(execution, opts)
    return execution
  }

  /** Create a persistent code context. */
  async createCodeContext(_opts: CreateCodeContextOpts = {}): Promise<Context> {
    throw new NotImplementedError('code contexts are not supported by Watasu yet')
  }

  /** Remove a persistent code context. */
  async removeCodeContext(_context: Context, _opts: { requestTimeoutMs?: number } = {}): Promise<boolean> {
    throw new NotImplementedError('code contexts are not supported by Watasu yet')
  }

  /** List persistent code contexts. */
  async listCodeContexts(_opts: { requestTimeoutMs?: number } = {}): Promise<Context[]> {
    throw new NotImplementedError('code contexts are not supported by Watasu yet')
  }

  /** Restart a persistent code context. */
  async restartCodeContext(_context: Context, _opts: { requestTimeoutMs?: number } = {}): Promise<Context> {
    throw new NotImplementedError('code contexts are not supported by Watasu yet')
  }
}

function executionFromApi(payload: Record<string, unknown>): Execution {
  const execution = record(payload.execution ?? payload)
  const logs = record(execution.logs)
  return new Execution(
    arrayOfRecords(execution.results).map((item) => new Result(item)),
    {
      stdout: arrayOfUnknown(logs.stdout).map((item) => outputMessageFromApi(item, false)),
      stderr: arrayOfUnknown(logs.stderr).map((item) => outputMessageFromApi(item, true)),
    },
    executionErrorFromApi(execution.error),
    numberValue(execution.execution_count ?? execution.executionCount)
  )
}

function outputMessageFromApi(value: unknown, error: boolean): OutputMessage {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const item = value as Record<string, unknown>
    return new OutputMessage(
      String(item.line ?? ''),
      numberValue(item.timestamp) ?? Date.now() / 1000,
      Boolean(item.error ?? error)
    )
  }
  return new OutputMessage(String(value), Date.now() / 1000, error)
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

function emitCallbacks(execution: Execution, opts: RunCodeOpts): void {
  for (const message of execution.logs.stdout) opts.onStdout?.(message)
  for (const message of execution.logs.stderr) opts.onStderr?.(message)
  for (const result of execution.results) opts.onResult?.(result)
  if (execution.error !== undefined) opts.onError?.(execution.error)
}

function contextId(context: Context | undefined): string | undefined {
  return context?.id
}

function compactRecord(payload: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(payload).filter(([, value]) => value !== undefined))
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
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
