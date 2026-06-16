import { CommandExitError, CommandHandle, CommandResult, Commands, CommandStartOpts } from './commands.js'
import { TimeoutError } from './errors.js'

/** A message emitted by a sandbox process. */
export class ProcessMessage {
  constructor(
    readonly line: string,
    /** Unix epoch in nanoseconds. */
    readonly timestamp: number,
    readonly error: boolean
  ) {}

  toString(): string {
    return this.line
  }
}

/** Captured output from a sandbox process. */
export class ProcessOutput {
  private messages: ProcessMessage[] = []
  private _finished = false
  private _error = false
  private _exitCode: number | undefined

  get error(): boolean { return this._error }
  get exitCode(): number | undefined { return this._exitCode }
  get stdout(): string { return this.messages.filter((message) => !message.error).map(String).join('') }
  get stderr(): string { return this.messages.filter((message) => message.error).map(String).join('') }

  addStdout(message: ProcessMessage): void {
    this.messages.push(message)
  }

  addStderr(message: ProcessMessage): void {
    this.messages.push(message)
    this._error = true
  }

  setExitCode(exitCode: number): void {
    this._exitCode = exitCode
    this._finished = true
    if (exitCode !== 0) this._error = true
  }

  replace(result: CommandResult): void {
    this.messages = []
    if (result.stdout) this.addStdout(processMessage(result.stdout, false))
    if (result.stderr) this.addStderr(processMessage(result.stderr, true))
    this.setExitCode(result.exitCode)
  }
}

export interface ProcessOpts extends Omit<CommandStartOpts, 'cmd' | 'args' | 'onStdout' | 'onStderr' | 'onExit'> {
  cmd: string
  onStdout?: (out: ProcessMessage) => Promise<void> | void
  onStderr?: (out: ProcessMessage) => Promise<void> | void
  onExit?: ((exitCode: number) => Promise<void> | void) | (() => Promise<void> | void)
}

/** A running sandbox process. */
export class Process {
  readonly finished: Promise<ProcessOutput>
  private waitPromise?: Promise<ProcessOutput>

  constructor(
    readonly processID: string,
    private readonly handle: CommandHandle,
    readonly output: ProcessOutput,
    private readonly onExit?: (exitCode: number) => Promise<void> | void
  ) {
    this.waitPromise = this.waitOnce()
    this.finished = this.waitPromise
  }

  async kill(): Promise<void> {
    await this.handle.kill()
  }

  async wait(timeoutMs?: number): Promise<ProcessOutput> {
    if (!this.waitPromise) this.waitPromise = this.waitOnce()
    return waitFor(this.waitPromise, timeoutMs)
  }

  private async waitOnce(): Promise<ProcessOutput> {
    try {
      this.output.replace(await this.handle.wait())
    } catch (error) {
      if (error instanceof CommandExitError) {
        this.output.replace(error)
      } else {
        throw error
      }
    }
    await this.onExit?.(this.output.exitCode ?? 0)
    return this.output
  }

  async sendStdin(data: string): Promise<void> {
    await this.handle.sendStdin(data)
  }
}

function waitFor<T>(promise: Promise<T>, timeoutMs?: number): Promise<T> {
  if (timeoutMs === undefined || timeoutMs <= 0) return promise
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new TimeoutError()), timeoutMs)
    promise.then(resolve, reject).finally(() => clearTimeout(timer))
  })
}

/** Manager for starting and interacting with sandbox processes. */
export class ProcessManager {
  constructor(private readonly commands: Commands) {}

  async start(cmd: string): Promise<Process>
  async start(opts: ProcessOpts): Promise<Process>
  async start(cmdOrOpts: string | ProcessOpts): Promise<Process> {
    const opts = processOpts(cmdOrOpts)
    const { cmd, onStdout, onStderr, onExit, ...commandOpts } = opts
    const output = new ProcessOutput()
    const handle = await this.commands.start(cmd, {
      ...commandOpts,
      onStdout: async (data) => {
        const message = processMessage(data, false)
        output.addStdout(message)
        await onStdout?.(message)
      },
      onStderr: async (data) => {
        const message = processMessage(data, true)
        output.addStderr(message)
        await onStderr?.(message)
      },
    })
    return new Process(String(handle.pid), handle, output, onExit)
  }

  async startAndWait(cmd: string): Promise<ProcessOutput>
  async startAndWait(opts: ProcessOpts): Promise<ProcessOutput>
  async startAndWait(cmdOrOpts: string | ProcessOpts): Promise<ProcessOutput> {
    const process = await this.start(cmdOrOpts as ProcessOpts)
    return process.wait(typeof cmdOrOpts === 'string' ? undefined : cmdOrOpts.timeoutMs)
  }
}

function processOpts(cmdOrOpts: string | ProcessOpts): ProcessOpts {
  return typeof cmdOrOpts === 'string' ? { cmd: cmdOrOpts } : cmdOrOpts
}

function processMessage(line: string, error: boolean): ProcessMessage {
  return new ProcessMessage(line, Date.now() * 1_000_000, error)
}
