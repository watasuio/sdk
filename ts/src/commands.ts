import { ConnectionConfig } from './connectionConfig.js'
import { DataPlaneClient } from './transport.js'
import { ProcessFrame, ProcessSocket, base64DecodeBytes, base64DecodeText } from './processSocket.js'
import { SandboxError } from './errors.js'

export interface CommandResult {
  /** Process exit code. Zero means success. */
  exitCode: number
  error?: string
  stdout: string
  stderr: string
}

/** Error thrown by `CommandHandle.wait()` when a process exits non-zero. */
export class CommandExitError extends SandboxError implements CommandResult {
  constructor(private readonly result: CommandResult) {
    super(result.error ?? `Command exited with code ${result.exitCode}`)
    this.name = 'CommandExitError'
  }

  get exitCode() { return this.result.exitCode }
  get error() { return this.result.error }
  get stdout() { return this.result.stdout }
  get stderr() { return this.result.stderr }
}

export interface ProcessInfo {
  pid: number | string
  tag?: string
  cmd?: string
  args: string[]
  envs: Record<string, string>
  cwd?: string
}

export interface CommandStartOpts {
  /** Return a `CommandHandle` immediately instead of waiting for exit. */
  background?: boolean
  cwd?: string
  user?: string
  envs?: Record<string, string>
  onStdout?: (data: string) => void | Promise<void>
  onStderr?: (data: string) => void | Promise<void>
  onPty?: (data: Uint8Array) => void | Promise<void>
  stdin?: boolean
  timeoutMs?: number
  requestTimeoutMs?: number
}

/** Live handle for one sandbox process stream. */
export class CommandHandle implements Partial<CommandResult> {
  private _stdout = ''
  private _stderr = ''
  private result?: CommandResult
  private readonly pending: Promise<void>

  constructor(
    readonly pid: number | string,
    private readonly socket: ProcessSocket,
    private readonly handleKill: () => Promise<boolean>,
    private readonly events: AsyncIterable<ProcessFrame>,
    private readonly onStdout?: (data: string) => void | Promise<void>,
    private readonly onStderr?: (data: string) => void | Promise<void>,
    private readonly onPty?: (data: Uint8Array) => void | Promise<void>
  ) {
    this.pending = this.handleEvents()
  }

  get stdout() { return this._stdout }
  get stderr() { return this._stderr }
  get exitCode() { return this.result?.exitCode }
  get error() { return this.result?.error }

  /** Wait until the process exits and return captured output. */
  async wait(): Promise<CommandResult> {
    await this.pending
    if (!this.result) throw new SandboxError('Command ended without an exit event')
    if (this.result.exitCode !== 0) throw new CommandExitError(this.result)
    return this.result
  }

  /** Kill the process. */
  async kill(): Promise<boolean> {
    return this.handleKill()
  }

  /** Send stdin bytes or text to the process. */
  async sendStdin(data: string | Uint8Array): Promise<void> {
    this.socket.sendStdin(data)
  }

  /** Resize the attached PTY stream when this handle was created as a PTY. */
  async resize(size: { cols: number; rows: number }): Promise<void> {
    this.socket.sendJson({ type: 'resize', cols: size.cols, rows: size.rows })
  }

  /** Detach the local stream without killing the process. */
  disconnect(): void {
    this.socket.close()
  }

  private async handleEvents(): Promise<void> {
    try {
      for await (const frame of this.events) {
        const type = frame.type
        if (type === 'started' || type === 'ready' || type === 'pong') continue
        if (type === 'stdout') {
          const out = base64DecodeText(frame.data)
          this._stdout += out
          await this.onStdout?.(out)
        } else if (type === 'stderr') {
          const out = base64DecodeText(frame.data)
          this._stderr += out
          await this.onStderr?.(out)
        } else if (type === 'pty') {
          const bytes = base64DecodeBytes(frame.data)
          const out = new TextDecoder().decode(bytes)
          this._stdout += out
          await this.onPty?.(bytes)
        } else if (type === 'exit') {
          this.result = {
            exitCode: Number(frame.exit_code ?? frame.exitCode ?? 0),
            error: typeof frame.error === 'string' ? frame.error : undefined,
            stdout: this._stdout,
            stderr: this._stderr,
          }
          return
        } else if (type === 'error') {
          throw new SandboxError(String(frame.message ?? frame.code ?? 'process error'))
        }
      }
    } finally {
      this.socket.close()
    }
  }
}

/** Command runner for a sandbox data-plane session. */
export class Commands {
  constructor(
    private readonly dataPlane: DataPlaneClient,
    private readonly config: ConnectionConfig,
    private readonly sandboxEnvs: Record<string, string> = {}
  ) {}

  /** List processes currently known by the sandbox runtime. */
  async list(opts: { requestTimeoutMs?: number } = {}): Promise<ProcessInfo[]> {
    const payload = await this.dataPlane.getJson('/runtime/v1/process', opts)
    const processes = Array.isArray(payload.processes) ? payload.processes : []
    return processes.map((item) => processInfo(item))
  }

  /** Send SIGKILL to a process by pid. */
  async kill(pid: number | string, opts: { requestTimeoutMs?: number } = {}): Promise<boolean> {
    await this.dataPlane.postJson(`/runtime/v1/process/${pid}/signal`, {
      ...opts,
      json: { signal: 'SIGKILL' },
    })
    return true
  }

  /** Attach to a process and send stdin bytes or text. */
  async sendStdin(pid: number | string, data: string | Uint8Array, opts: { requestTimeoutMs?: number } = {}) {
    const handle = await this.connect(pid, opts)
    try {
      await handle.sendStdin(data)
    } finally {
      handle.disconnect()
    }
  }

  async run(cmd: string, opts: CommandStartOpts & { background: true }): Promise<CommandHandle>
  async run(cmd: string, opts?: CommandStartOpts): Promise<CommandResult>
  /** Run a shell command over the WebSocket process runtime. */
  async run(cmd: string, opts: CommandStartOpts = {}): Promise<CommandHandle | CommandResult> {
    const handle = await this.start(cmd, opts)
    if (opts.background) return handle
    return handle.wait()
  }

  /** Reconnect to a live process stream by pid. */
  async connect(pid: number | string, opts: CommandStartOpts = {}): Promise<CommandHandle> {
    const socket = await new ProcessSocket(
      this.dataPlane.baseUrl,
      this.dataPlane.token,
      `/runtime/v1/process/${pid}/connect?since=0`,
      opts.requestTimeoutMs ?? this.config.requestTimeoutMs
    ).connect()
    const first = await nextStarted(socket)
    const actualPid = framePid(first) ?? pid
    return new CommandHandle(actualPid, socket, () => this.kill(actualPid), socket, opts.onStdout, opts.onStderr, opts.onPty)
  }

  private async start(cmd: string, opts: CommandStartOpts): Promise<CommandHandle> {
    const socket = await new ProcessSocket(
      this.dataPlane.baseUrl,
      this.dataPlane.token,
      '/runtime/v1/process',
      opts.requestTimeoutMs ?? this.config.requestTimeoutMs
    ).connect()
    const environment = { ...this.sandboxEnvs, ...(opts.envs ?? {}) }
    socket.sendJson({
      type: 'start',
      cmd: '/bin/bash',
      args: ['-l', '-c', cmd],
      cwd: opts.cwd,
      user: opts.user,
      environment,
      envs: environment,
      stdin: opts.stdin ?? false,
      timeout_ms: opts.timeoutMs ?? 60_000,
    })
    const first = await nextStarted(socket)
    const pid = framePid(first)
    if (pid === undefined) throw new SandboxError('process started frame did not include pid')
    return new CommandHandle(pid, socket, () => this.kill(pid), withFirst(first, socket), opts.onStdout, opts.onStderr, opts.onPty)
  }
}

async function nextStarted(events: AsyncIterable<ProcessFrame>): Promise<ProcessFrame> {
  for await (const frame of events) {
    if (frame.type === 'started') return frame
  }
  throw new SandboxError('process ended before started frame')
}

async function* withFirst(first: ProcessFrame, rest: AsyncIterable<ProcessFrame>) {
  yield first
  yield* rest
}

function framePid(frame: ProcessFrame): number | string | undefined {
  const process = frame.process && typeof frame.process === 'object' ? frame.process as Record<string, unknown> : {}
  const pid = frame.pid ?? process.pid ?? process.id
  return typeof pid === 'number' || typeof pid === 'string' ? pid : undefined
}

function processInfo(value: unknown): ProcessInfo {
  const item = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  const process = item.process && typeof item.process === 'object' ? item.process as Record<string, unknown> : item
  return {
    pid: framePid(process) ?? '',
    tag: typeof process.tag === 'string' ? process.tag : undefined,
    cmd: typeof process.cmd === 'string' ? process.cmd : undefined,
    args: Array.isArray(process.args) ? process.args.map(String) : [],
    envs: recordOfStrings(process.envs ?? process.environment),
    cwd: typeof process.cwd === 'string' ? process.cwd : undefined,
  }
}

function recordOfStrings(value: unknown): Record<string, string> {
  if (!value || typeof value !== 'object') return {}
  return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([key, item]) => [key, String(item)]))
}
