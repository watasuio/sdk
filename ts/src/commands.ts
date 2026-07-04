import { ConnectionConfig } from './connectionConfig.js'
import { DataPlaneClient, withQuery } from './transport.js'
import { ProcessFrame, ProcessSocket, base64DecodeBytes, base64DecodeText } from './processSocket.js'
import { NotFoundError, SandboxError, TimeoutError } from './errors.js'

export interface CommandResult {
  /** Process exit code. Zero means success. */
  exitCode: number
  error?: string
  stdout: string
  stderr: string
  /**
   * True when the runtime dropped stream output that could not be replayed
   * (retention evicted the events before the SDK could backfill them).
   */
  truncated?: boolean
}

/** Error thrown by `CommandHandle.wait()` when a process exits non-zero. */
export class CommandExitError extends SandboxError implements CommandResult {
  constructor(private readonly result: CommandResult) {
    super(
      (result.error ?? `Command exited with code ${result.exitCode}`) +
        (result.truncated ? '; some process output was dropped before delivery' : '')
    )
    this.name = 'CommandExitError'
  }

  get exitCode() { return this.result.exitCode }
  get error() { return this.result.error }
  get stdout() { return this.result.stdout }
  get stderr() { return this.result.stderr }
  get truncated() { return this.result.truncated }
}

export interface ProcessInfo {
  pid: number | string
  tag?: string
  cmd?: string
  args: string[]
  envs: Record<string, string>
  cwd?: string
}

export interface ProcessStatus {
  pid: number | string
  id?: number | string
  osPid?: number
  command?: string
  args: string[]
  cwd?: string
  user?: string
  pty?: boolean
  status: string
  startedAt?: string
  finishedAt?: string
  exitCode?: number
  /**
   * Set when a kill has been requested but the process has not finished yet
   * (the runtime reports a non-terminal `killing` status in that window).
   */
  killRequestedAt?: string
}

export interface ProcessOutputEvent {
  cursor: number
  type: 'stdout' | 'stderr' | 'pty' | string
  data: Uint8Array
}

export interface ProcessOutputSnapshot {
  pid: number | string
  status: string
  exitCode?: number
  finishedAt?: string
  nextCursor: number
  truncatedBeforeCursor: boolean
  events: ProcessOutputEvent[]
}

export interface ReadProcessOutputOptions extends CommandRequestOpts {
  since?: number
  limitBytes?: number
}

export interface StopProcessOptions {
  signal?: string
  killGroup?: boolean
  graceMs?: number
  requestTimeoutMs?: number
  abortSignal?: AbortSignal
}

export interface CommandStartOpts {
  /** Return a `CommandHandle` immediately instead of waiting for exit. */
  background?: boolean
  /** Executable to start directly. When omitted, `cmd` strings run through a login shell. */
  cmd?: string
  /** Arguments for `cmd` when starting a direct executable. */
  args?: string[]
  cwd?: string
  user?: string
  envs?: Record<string, string>
  onStdout?: (data: string) => void | Promise<void>
  onStderr?: (data: string) => void | Promise<void>
  onPty?: (data: Uint8Array) => void | Promise<void>
  onExit?: (exitCode: number) => void | Promise<void>
  stdin?: boolean
  timeoutMs?: number
  processID?: string
  requestTimeoutMs?: number
  signal?: AbortSignal
}

export type CommandRequestOpts = Pick<CommandStartOpts, 'requestTimeoutMs' | 'signal'>
export interface CommandConnectOpts extends CommandRequestOpts {
  timeoutMs?: number
  onStdout?: (data: string) => void | Promise<void>
  onStderr?: (data: string) => void | Promise<void>
}
export type Stdout = string
export type Stderr = string
export type PtyOutput = Uint8Array

type ProcessReconnect = (cursor: number) => Promise<{
  socket: ProcessSocket
  events: AsyncIterable<ProcessFrame>
}>

const STREAM_RECONNECT_ATTEMPTS = 12
const STREAM_RECONNECT_BASE_DELAY_MS = 250
const STREAM_RECONNECT_MAX_DELAY_MS = 2_000

/**
 * Sentinel exit code reported when the runtime finishes a command without an
 * explicit exit code (for example when it was killed or failed). Success is
 * only ever an explicit exit code of 0.
 */
const MISSING_EXIT_CODE = -1

/** Server-side command ceiling used when the caller supplied no timeout. */
const DEFAULT_WAIT_TIMEOUT_MS = 3_600_000
/** Extra client-side grace on top of the command timeout before giving up. */
const WAIT_TIMEOUT_GRACE_MS = 60_000

/** Client-side wait deadline for a command: its timeout plus grace. */
export function commandWaitDeadlineMs(timeoutMs?: number): number {
  const base = timeoutMs !== undefined && timeoutMs > 0 ? timeoutMs : DEFAULT_WAIT_TIMEOUT_MS
  return base + WAIT_TIMEOUT_GRACE_MS
}

/** Live handle for one sandbox process stream. */
export class CommandHandle implements Partial<CommandResult> {
  private _stdout = ''
  private _stderr = ''
  private result?: CommandResult
  private readonly pending: Promise<void>
  private nextCursor = 0
  private disconnected = false
  private pendingLagged = false
  private outputDropped = false

  constructor(
    readonly pid: number | string,
    private socket: ProcessSocket,
    private readonly handleKill: () => Promise<boolean>,
    private events: AsyncIterable<ProcessFrame>,
    private readonly onStdout?: (data: string) => void | Promise<void>,
    private readonly onStderr?: (data: string) => void | Promise<void>,
    private readonly onPty?: (data: Uint8Array) => void | Promise<void>,
    private readonly onExit?: (exitCode: number) => void | Promise<void>,
    private readonly reconnect?: ProcessReconnect
  ) {
    this.pending = this.handleEvents()
  }

  get stdout() { return this._stdout }
  get stderr() { return this._stderr }
  get exitCode() { return this.result?.exitCode }
  get error() { return this.result?.error }

  /** Wait until the process exits and return captured output. */
  async wait(timeoutMs?: number): Promise<CommandResult> {
    await waitFor(
      this.pending,
      commandWaitDeadlineMs(timeoutMs),
      `timed out waiting for command ${this.pid} to exit; the command may still be running, check its status with commands.process(pid)`
    )
    if (!this.result) throw new SandboxError('Command ended without an exit event')
    if (this.result.exitCode !== 0) throw new CommandExitError(this.result)
    return this.result
  }

  /** Kill the process. */
  async kill(): Promise<boolean> {
    return this.handleKill()
  }

  /** Send stdin bytes or text to the process. */
  async sendStdin(data: string | Uint8Array, opts: CommandRequestOpts = {}): Promise<void> {
    await this.socket.sendStdin(data, opts)
  }

  /** Close the stdin stream and signal EOF to the process. */
  async closeStdin(opts: CommandRequestOpts = {}): Promise<void> {
    await this.socket.closeStdin(opts)
  }

  /** Resize the attached PTY stream when this handle was created as a PTY. */
  async resize(size: { cols: number; rows: number }): Promise<void> {
    await this.socket.sendJson({ type: 'resize', cols: size.cols, rows: size.rows })
  }

  /** Detach the local stream without killing the process. */
  async disconnect(): Promise<void> {
    this.disconnected = true
    this.socket.close()
  }

  private async handleEvents(): Promise<void> {
    while (!this.disconnected && !this.result) {
      let streamError: unknown
      for await (const frame of this.events) {
        const type = frame.type
        if (type === 'lagged') {
          // The runtime dropped frames from this stream. Replay them from the
          // next undelivered cursor when the stream supports reconnecting;
          // otherwise flag the dropped output. Do not advance the cursor from
          // a lagged frame, or the replay would skip the dropped events.
          if (!this.reconnect) {
            this.outputDropped = true
            continue
          }
          this.pendingLagged = true
          break
        }
        if (this.pendingLagged) {
          const cursor = numberValue(frame.cursor)
          if (cursor !== undefined) {
            // Backfill was impossible when the first replayed event sits past
            // the requested cursor: the runtime evicted the events between.
            if (cursor > this.nextCursor) this.outputDropped = true
            this.pendingLagged = false
          }
        }
        this.advanceCursor(frame)
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
          const rawExitCode = numericExitCode(frame.exit_code ?? frame.exitCode)
          let error = typeof frame.error === 'string' ? frame.error : undefined
          let exitCode: number
          if (rawExitCode === undefined) {
            // Killed/failed commands may finish without an exit code. Keep
            // exitCode a plain number and take the failure path.
            const status = typeof frame.status === 'string' && frame.status ? frame.status : 'failed'
            exitCode = MISSING_EXIT_CODE
            error ??= `command ${status} without an exit code${this._stderr ? `; stderr:\n${this._stderr}` : ''}`
          } else {
            exitCode = rawExitCode
          }
          this.result = {
            exitCode,
            error,
            stdout: this._stdout,
            stderr: this._stderr,
            truncated: this.outputDropped,
          }
          await this.onExit?.(exitCode)
          this.socket.close()
          return
        } else if (type === 'error') {
          streamError = new SandboxError(String(frame.message ?? frame.code ?? 'process error'))
          if (!isReconnectableStreamError(streamError)) throw streamError
          break
        }
      }

      if (this.result || this.disconnected) return
      if (!this.reconnect) {
        this.socket.close()
        if (streamError) throw streamError
        return
      }
      await this.reconnectStream()
    }
  }

  private advanceCursor(frame: ProcessFrame): void {
    const cursor = numberValue(frame.cursor)
    if (cursor !== undefined) this.nextCursor = Math.max(this.nextCursor, cursor + 1)
  }

  private async reconnectStream(): Promise<void> {
    let lastError: unknown
    for (let attempt = 0; attempt < STREAM_RECONNECT_ATTEMPTS && !this.disconnected; attempt += 1) {
      this.socket.close()
      if (attempt > 0) await sleep(reconnectDelayMs(attempt))
      try {
        const next = await this.reconnect!(this.nextCursor)
        this.socket = next.socket
        this.events = next.events
        return
      } catch (error) {
        lastError = error
      }
    }
    if (lastError instanceof Error) throw lastError
    throw new SandboxError('process websocket closed before exit and could not reconnect')
  }
}

/** Command runner for a sandbox data-plane session. */
export class Commands {
  constructor(
    private readonly dataPlane: DataPlaneClient,
    private readonly config: ConnectionConfig,
    private readonly sandboxEnvs: Record<string, string> = {}
  ) {}

  /** Whether this runtime supports stdin EOF frames. */
  get supportsStdinClose(): boolean {
    return true
  }

  /** List processes currently known by the sandbox runtime. */
  async list(opts: { requestTimeoutMs?: number; signal?: AbortSignal } = {}): Promise<ProcessInfo[]> {
    const payload = await this.dataPlane.getJson('/runtime/v1/process', opts)
    const processes = Array.isArray(payload.processes) ? payload.processes : []
    return processes.map((item) => processInfo(item))
  }

  /** Send SIGKILL to a process by pid. */
  async kill(pid: number | string, opts: { requestTimeoutMs?: number; signal?: AbortSignal } = {}): Promise<boolean> {
    await this.stopProcess(pid, {
      signal: 'SIGKILL',
      requestTimeoutMs: opts.requestTimeoutMs,
      abortSignal: opts.signal,
    })
    return true
  }

  /** Attach to a process and send stdin bytes or text. */
  async sendStdin(pid: number | string, data: string | Uint8Array, opts: { requestTimeoutMs?: number; signal?: AbortSignal } = {}) {
    const handle = await this.connect(pid, opts)
    try {
      await handle.sendStdin(data, opts)
    } finally {
      await handle.disconnect()
    }
  }

  /** Attach to a process and close stdin, signalling EOF. */
  async closeStdin(pid: number | string, opts: { requestTimeoutMs?: number; signal?: AbortSignal } = {}) {
    const handle = await this.connect(pid, opts)
    try {
      await handle.closeStdin(opts)
    } finally {
      await handle.disconnect()
    }
  }

  async run(cmd: string, opts: CommandStartOpts & { background: true }): Promise<CommandHandle>
  async run(cmd: string, opts?: CommandStartOpts): Promise<CommandResult>
  /** Run a shell command over the WebSocket process runtime. */
  async run(cmd: string, opts: CommandStartOpts = {}): Promise<CommandHandle | CommandResult> {
    const handle = await this.start(cmd, opts)
    if (opts.background) return handle
    return handle.wait(opts.timeoutMs)
  }

  /** Reconnect to a live process stream by pid. */
  async connect(pid: number | string, opts: CommandStartOpts = {}): Promise<CommandHandle> {
    return this.connectSince(pid, 0, opts)
  }

  /** Reconnect to a live process stream by pid starting at a cursor. */
  async connectSince(pid: number | string, cursor = 0, opts: CommandStartOpts = {}): Promise<CommandHandle> {
    const stream = await this.openProcessStream(pid, cursor, opts)
    const reconnect = async (nextCursor: number) => this.openProcessStream(stream.actualPid, nextCursor, opts)
    return new CommandHandle(stream.actualPid, stream.socket, () => this.kill(stream.actualPid), stream.events, opts.onStdout, opts.onStderr, opts.onPty, undefined, reconnect)
  }

  /** Look up process status without attaching a WebSocket. */
  async process(pid: number | string, opts: CommandRequestOpts = {}): Promise<ProcessStatus> {
    const payload = await this.dataPlane
      .getJson(`/runtime/v1/process/${encodeURIComponent(String(pid))}`, opts)
      .catch((error) => { throw processPurgedNotFound(error, pid) })
    return processStatus(payload)
  }

  /** Read available process output since a cursor without blocking. */
  async readProcessOutput(pid: number | string, opts: ReadProcessOutputOptions = {}): Promise<ProcessOutputSnapshot> {
    const payload = await this.dataPlane
      .getJson(
        withQuery(`/runtime/v1/process/${encodeURIComponent(String(pid))}/output`, {
          since: opts.since,
          limit_bytes: opts.limitBytes,
        }),
        opts
      )
      .catch((error) => { throw processPurgedNotFound(error, pid) })
    return processOutputSnapshot(payload)
  }

  /** Stop a process, optionally signalling the full process group. */
  async stopProcess(pid: number | string, opts: StopProcessOptions = {}): Promise<ProcessStatus> {
    const payload = await this.dataPlane.deleteJson(
      withQuery(`/runtime/v1/process/${encodeURIComponent(String(pid))}`, {
        signal: opts.signal,
        kill_group: opts.killGroup ?? true,
        grace_ms: opts.graceMs,
      }),
      {
        requestTimeoutMs: opts.requestTimeoutMs,
        signal: opts.abortSignal,
      }
    )
    return processStatus(payload)
  }

  /** Start a command and return a live handle immediately. */
  async start(cmd: string, opts: CommandStartOpts = {}): Promise<CommandHandle> {
    const socket = await new ProcessSocket(
      this.dataPlane.baseUrl,
      this.dataPlane.token,
      '/runtime/v1/process',
      opts.requestTimeoutMs ?? this.config.requestTimeoutMs,
      this.config.headers
    ).connect()
    const environment = { ...this.sandboxEnvs, ...(opts.envs ?? {}) }
    const processConfig = processStartConfig(cmd, opts)
    await socket.sendJson({
      type: 'start',
      id: opts.processID,
      cmd: processConfig.cmd,
      args: processConfig.args,
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
    const reconnect = async (nextCursor: number) => this.openProcessStream(pid, nextCursor, opts)
    return new CommandHandle(pid, socket, () => this.kill(pid), withFirst(first, socket), opts.onStdout, opts.onStderr, opts.onPty, opts.onExit, reconnect)
  }

  private async openProcessStream(pid: number | string, cursor: number, opts: CommandStartOpts = {}): Promise<{
    actualPid: number | string
    socket: ProcessSocket
    events: AsyncIterable<ProcessFrame>
  }> {
    const encodedPid = encodeURIComponent(String(pid))
    const socket = await new ProcessSocket(
      this.dataPlane.baseUrl,
      this.dataPlane.token,
      withQuery(`/runtime/v1/process/${encodedPid}/connect`, { since: cursor }),
      opts.requestTimeoutMs ?? this.config.requestTimeoutMs,
      this.config.headers
    ).connect()
    const first = await nextStarted(socket)
    return {
      actualPid: framePid(first) ?? pid,
      socket,
      events: socket,
    }
  }
}

function processStartConfig(cmd: string, opts: CommandStartOpts): { cmd: string; args: string[] } {
  if (opts.args !== undefined || opts.cmd !== undefined) {
    return { cmd: opts.cmd ?? cmd, args: opts.args ?? [] }
  }
  return { cmd: '/bin/bash', args: ['-l', '-c', cmd] }
}

function waitFor<T>(promise: Promise<T>, timeoutMs: number, message?: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new TimeoutError(message)), timeoutMs)
    promise.then(resolve, reject).finally(() => clearTimeout(timer))
  })
}

function numericExitCode(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value !== '') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return undefined
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function reconnectDelayMs(attempt: number): number {
  return Math.min(STREAM_RECONNECT_MAX_DELAY_MS, STREAM_RECONNECT_BASE_DELAY_MS * 2 ** Math.max(0, attempt - 1))
}

function isReconnectableStreamError(error: unknown): boolean {
  return error instanceof Error && /websocket|closed/i.test(error.message)
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
  const stablePid = typeof process.id === 'number' || typeof process.id === 'string' ? process.id : framePid(process)
  return {
    pid: stablePid ?? '',
    tag: typeof process.tag === 'string' ? process.tag : undefined,
    cmd: typeof process.cmd === 'string' ? process.cmd : typeof process.command === 'string' ? process.command : undefined,
    args: Array.isArray(process.args) ? process.args.map(String) : [],
    envs: recordOfStrings(process.envs ?? process.environment),
    cwd: typeof process.cwd === 'string' ? process.cwd : undefined,
  }
}

function processStatus(value: unknown): ProcessStatus {
  const item = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  const process = item.process && typeof item.process === 'object' ? item.process as Record<string, unknown> : item
  const pid = scalar(process.pid ?? process.id) ?? ''
  return {
    pid,
    id: scalar(process.id),
    osPid: numberValue(process.os_pid ?? process.osPid),
    command: stringValue(process.command ?? process.cmd),
    args: arrayOfStrings(process.args ?? process.arguments),
    cwd: stringValue(process.cwd ?? process.working_directory),
    user: stringValue(process.user),
    pty: typeof process.pty === 'boolean' ? process.pty : undefined,
    status: stringValue(process.status) ?? '',
    startedAt: stringValue(process.started_at ?? process.startedAt),
    finishedAt: stringValue(process.finished_at ?? process.finishedAt),
    exitCode: numberValue(process.exit_code ?? process.exitCode),
    killRequestedAt: stringValue(process.kill_requested_at ?? process.killRequestedAt),
  }
}

function processPurgedNotFound(error: unknown, pid: number | string): unknown {
  if (error instanceof NotFoundError) {
    return new NotFoundError(
      `process ${pid} not found: process records are purged when the sandbox is destroyed; terminal results were delivered via wait()/callbacks`
    )
  }
  return error
}

function processOutputSnapshot(value: unknown): ProcessOutputSnapshot {
  const payload = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  return {
    pid: scalar(payload.pid ?? payload.id) ?? '',
    status: stringValue(payload.status) ?? '',
    exitCode: numberValue(payload.exit_code ?? payload.exitCode),
    finishedAt: stringValue(payload.finished_at ?? payload.finishedAt),
    nextCursor: numberValue(payload.next_cursor ?? payload.nextCursor) ?? 0,
    truncatedBeforeCursor: payload.truncated_before_cursor === true || payload.truncatedBeforeCursor === true,
    events: Array.isArray(payload.events) ? payload.events.map(processOutputEvent) : [],
  }
}

function processOutputEvent(value: unknown): ProcessOutputEvent {
  const event = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  return {
    cursor: numberValue(event.cursor) ?? 0,
    type: stringValue(event.type) ?? '',
    data: base64DecodeBytes(stringValue(event.data) ?? ''),
  }
}

function recordOfStrings(value: unknown): Record<string, string> {
  if (!value || typeof value !== 'object') return {}
  return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([key, item]) => [key, String(item)]))
}

function scalar(value: unknown): number | string | undefined {
  return typeof value === 'number' || typeof value === 'string' ? value : undefined
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function numberValue(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  return undefined
}

function arrayOfStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : []
}
