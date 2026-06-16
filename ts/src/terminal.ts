import { CommandHandle } from './commands.js'
import { Pty, PtySize } from './pty.js'

/** Captured terminal output. */
export class TerminalOutput {
  private _data = ''

  get data(): string { return this._data }

  addData(data: string): void {
    this._data += data
  }
}

export type TerminalOpts = {
  onData?: (data: string) => Promise<void> | void
  onExit?: () => Promise<void> | void
  size?: PtySize
  terminalID?: string
  cmd?: string
  cwd?: string
  envs?: Record<string, string>
  timeoutMs?: number
}

/** A running terminal session in a sandbox. */
export class Terminal {
  readonly finished: Promise<TerminalOutput>
  private waitPromise?: Promise<TerminalOutput>

  constructor(
    readonly terminalID: string,
    private readonly handle: CommandHandle,
    readonly output: TerminalOutput,
    private readonly onExit?: () => Promise<void> | void
  ) {
    this.finished = this.wait()
  }

  get data(): string { return this.output.data }

  async kill(): Promise<void> {
    await this.handle.kill()
  }

  async wait(): Promise<TerminalOutput> {
    if (this.waitPromise) return this.waitPromise
    this.waitPromise = this.waitOnce()
    return this.waitPromise
  }

  private async waitOnce(): Promise<TerminalOutput> {
    await this.handle.wait().catch((error) => {
      if (typeof error?.stdout === 'string') this.output.addData(error.stdout)
      else throw error
    })
    await this.onExit?.()
    return this.output
  }

  async sendData(data: string): Promise<void> {
    await this.handle.sendStdin(data)
  }

  async resize({ cols, rows }: PtySize): Promise<void> {
    await this.handle.resize({ cols, rows })
  }
}

/** Manager for starting terminal sessions. */
export class TerminalManager {
  constructor(private readonly pty: Pty) {}

  async start(opts: TerminalOpts = {}): Promise<Terminal> {
    const output = new TerminalOutput()
    const handle = await this.pty.create({
      cmd: opts.cmd,
      cwd: opts.cwd,
      envs: opts.envs,
      size: opts.size,
      timeoutMs: opts.timeoutMs,
      onData: async (bytes) => {
        const data = new TextDecoder().decode(bytes)
        output.addData(data)
        await opts.onData?.(data)
      },
    })
    return new Terminal(String(handle.pid), handle, output, opts.onExit)
  }
}
