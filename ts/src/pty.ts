import { CommandHandle, CommandStartOpts } from './commands.js'
import { ConnectionConfig, type ConnectionOpts } from './connectionConfig.js'
import { ProcessFrame, ProcessSocket } from './processSocket.js'
import { DataPlaneClient } from './transport.js'
import { SandboxError } from './errors.js'

export interface PtySize {
  cols: number
  rows: number
}

export interface PtyCreateOpts extends Omit<CommandStartOpts, 'background' | 'onStdout' | 'onStderr'> {
  cols?: number
  rows?: number
  size?: PtySize
  cmd?: string
  onData?: (data: Uint8Array) => void | Promise<void>
}

export interface PtyConnectOpts {
  onData?: (data: Uint8Array) => void | Promise<void>
  timeoutMs?: number
  requestTimeoutMs?: number
}

/** PTY helper backed by the sandbox process WebSocket runtime. */
export class Pty {
  constructor(
    private readonly dataPlane: DataPlaneClient,
    private readonly config: ConnectionConfig
  ) {}

  /** Create an interactive shell PTY and return its live command handle. */
  async create(opts: PtyCreateOpts): Promise<CommandHandle> {
    const socket = await new ProcessSocket(
      this.dataPlane.baseUrl,
      this.dataPlane.token,
      '/runtime/v1/process',
      opts.requestTimeoutMs ?? this.config.requestTimeoutMs,
      this.config.headers
    ).connect()
    const envs = { TERM: 'xterm-256color', LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8', ...(opts.envVars ?? opts.envs ?? {}) }
    const size = opts.size ?? { cols: opts.cols ?? 80, rows: opts.rows ?? 24 }
    const args = opts.cmd === undefined ? ['-i', '-l'] : ['-l', '-c', opts.cmd]
    socket.sendJson({
      type: 'start',
      cmd: '/bin/bash',
      args,
      cwd: opts.cwd ?? opts.rootDir,
      user: opts.user,
      environment: envs,
      envs,
      stdin: true,
      pty: { cols: size.cols, rows: size.rows },
      timeout_ms: opts.timeoutMs ?? opts.timeout ?? 60_000,
    })
    const first = await nextStarted(socket)
    const pid = framePid(first)
    if (pid === undefined) throw new SandboxError('PTY started frame did not include pid')
    return new CommandHandle(pid, socket, () => this.kill(pid), withFirst(first, socket), undefined, undefined, opts.onData ?? opts.onPty)
  }

  /** Connect to a running PTY by pid. */
  async connect(pid: number | string, opts: PtyConnectOpts = {}): Promise<CommandHandle> {
    const socket = await new ProcessSocket(
      this.dataPlane.baseUrl,
      this.dataPlane.token,
      `/runtime/v1/process/${pid}/connect?since=0`,
      opts.requestTimeoutMs ?? this.config.requestTimeoutMs,
      this.config.headers
    ).connect()
    const first = await nextStarted(socket)
    const actualPid = framePid(first) ?? pid
    return new CommandHandle(actualPid, socket, () => this.kill(actualPid), withFirst(first, socket), undefined, undefined, opts.onData)
  }

  /** Send input bytes or text to a PTY. */
  async sendStdin(pid: number | string, data: string | Uint8Array, opts: PtyConnectOpts = {}): Promise<void> {
    const handle = await this.connect(pid, opts)
    try {
      await handle.sendStdin(data)
    } finally {
      await handle.disconnect()
    }
  }

  /** Alias for `sendStdin`. */
  async sendInput(pid: number | string, data: string | Uint8Array, opts: PtyConnectOpts = {}): Promise<void> {
    return this.sendStdin(pid, data, opts)
  }

  /** Resize a running PTY. */
  async resize(pid: number | string, size: PtySize, opts: PtyConnectOpts = {}): Promise<void> {
    const handle = await this.connect(pid, opts)
    try {
      await handle.resize(size)
    } finally {
      await handle.disconnect()
    }
  }

  /** Kill a running PTY. */
  async kill(pid: number | string, opts: Pick<ConnectionOpts, 'requestTimeoutMs' | 'signal'> = {}): Promise<boolean> {
    await this.dataPlane.postJson(`/runtime/v1/process/${pid}/signal`, {
      json: { signal: 'SIGKILL' },
      requestTimeoutMs: opts.requestTimeoutMs,
      signal: opts.signal,
    })
    return true
  }
}

async function nextStarted(events: AsyncIterable<ProcessFrame>): Promise<ProcessFrame> {
  for await (const frame of events) {
    if (frame.type === 'started') return frame
  }
  throw new SandboxError('PTY ended before started frame')
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
