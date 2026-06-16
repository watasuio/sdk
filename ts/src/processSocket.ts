import { Buffer } from 'node:buffer'

import WebSocket, { type RawData } from 'ws'

import { KEEPALIVE_PING_INTERVAL_SEC } from './connectionConfig.js'
import { SandboxError, TimeoutError } from './errors.js'

export type ProcessFrame = Record<string, unknown>

/** Streaming WebSocket connection to the sandbox process runtime. */
export class ProcessSocket implements AsyncIterable<ProcessFrame> {
  private ws?: WebSocket
  private queue: ProcessFrame[] = []
  private waiters: Array<(value: IteratorResult<ProcessFrame>) => void> = []
  private closed = false
  private keepalive?: ReturnType<typeof setInterval>

  constructor(
    private readonly baseUrl: string,
    private readonly token: string,
    private readonly path: string,
    private readonly requestTimeoutMs = 60_000,
    private readonly headers: Record<string, string> = {}
  ) {}

  async connect(): Promise<this> {
    const ws = new WebSocket(wsUrl(this.baseUrl, this.path), {
      headers: { ...this.headers, Authorization: `Bearer ${this.token}` },
    })
    this.ws = ws

    ws.on('message', (data) => this.onMessage(data))
    ws.on('close', () => this.finish())
    ws.on('error', () => this.finish(new SandboxError('process websocket failed')))

    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => reject(new TimeoutError()), this.requestTimeoutMs)
      ws.once('open', () => {
        clearTimeout(timeout)
        resolve()
      })
      ws.once('error', () => {
        clearTimeout(timeout)
        reject(new SandboxError('process websocket failed to connect'))
      })
    })

    this.keepalive = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.ping('watasu-sdk')
      }
    }, KEEPALIVE_PING_INTERVAL_SEC * 1000)
    return this
  }

  sendJson(payload: ProcessFrame): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new SandboxError('process websocket is not connected')
    }
    this.ws.send(JSON.stringify(payload))
  }

  sendStdin(data: string | Uint8Array): void {
    const raw = typeof data === 'string' ? new TextEncoder().encode(data) : data
    this.sendJson({ type: 'stdin', data: base64Encode(raw) })
  }

  closeStdin(): void {
    this.sendJson({ type: 'close_stdin' })
  }

  close(): void {
    this.closed = true
    if (this.keepalive) clearInterval(this.keepalive)
    this.ws?.close()
    this.flushDone()
  }

  [Symbol.asyncIterator](): AsyncIterator<ProcessFrame> {
    return {
      next: () => this.next(),
    }
  }

  private next(): Promise<IteratorResult<ProcessFrame>> {
    if (this.queue.length > 0) {
      return Promise.resolve({ done: false, value: this.queue.shift()! })
    }
    if (this.closed) return Promise.resolve({ done: true, value: undefined })
    return new Promise((resolve) => this.waiters.push(resolve))
  }

  private onMessage(message: RawData): void {
    const text = rawDataToText(message)
    try {
      const frame = JSON.parse(text) as ProcessFrame
      if (frame.type === 'pong' || frame.type === 'ready') return
      if (frame.type === 'error') {
        this.finish(new SandboxError(String(frame.message ?? frame.code ?? 'process error')))
        return
      }
      const waiter = this.waiters.shift()
      if (waiter) waiter({ done: false, value: frame })
      else this.queue.push(frame)
    } catch (error) {
      this.finish(error instanceof Error ? error : new SandboxError(String(error)))
    }
  }

  private finish(error?: Error): void {
    this.closed = true
    if (this.keepalive) clearInterval(this.keepalive)
    if (error) {
      const frame = { type: 'error', message: error.message }
      const waiter = this.waiters.shift()
      if (waiter) waiter({ done: false, value: frame })
      else this.queue.push(frame)
    }
    this.flushDone()
  }

  private flushDone(): void {
    for (const waiter of this.waiters.splice(0)) {
      waiter({ done: true, value: undefined })
    }
  }
}

export function base64Encode(bytes: Uint8Array): string {
  return Buffer.from(bytes).toString('base64')
}

export function base64DecodeText(value: unknown): string {
  if (typeof value !== 'string') return String(value ?? '')
  try {
    return Buffer.from(base64DecodeBytes(value)).toString('utf8')
  } catch {
    return value
  }
}

export function base64DecodeBytes(value: unknown): Uint8Array {
  if (typeof value !== 'string') return new TextEncoder().encode(String(value ?? ''))
  return new Uint8Array(Buffer.from(value, 'base64'))
}

function rawDataToText(message: RawData): string {
  if (typeof message === 'string') return message
  if (Array.isArray(message)) return Buffer.concat(message).toString('utf8')
  if (message instanceof ArrayBuffer) return Buffer.from(new Uint8Array(message)).toString('utf8')
  return Buffer.from(message as Uint8Array).toString('utf8')
}

function wsUrl(baseUrl: string, path: string): string {
  const url = new URL(path, baseUrl.replace(/^http/, 'ws'))
  return url.toString()
}
