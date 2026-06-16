declare const process:
  | {
      env: Record<string, string | undefined>
    }
  | undefined

export const KEEPALIVE_PING_INTERVAL_SEC = 50
export const SESSION_OPERATION_REQUEST_TIMEOUT_MS = 150_000

export type Username = string
export interface Logger {
  debug?: (...args: unknown[]) => void
  info?: (...args: unknown[]) => void
  warn?: (...args: unknown[]) => void
  error?: (...args: unknown[]) => void
}

/** Connection options accepted by Watasu SDK entrypoints. */
export interface ConnectionOpts {
  apiKey?: string
  accessToken?: string
  domain?: string
  apiUrl?: string
  /** Absolute sandbox data-plane URL override, primarily for local runtimes. */
  sandboxUrl?: string
  dataPlaneDomain?: string
  requestTimeoutMs?: number
  headers?: Record<string, string>
  apiHeaders?: Record<string, string>
  debug?: boolean
  logger?: Logger
  signal?: AbortSignal
  proxy?: string
}

/** Resolved connection settings used by control-plane and data-plane clients. */
export class ConnectionConfig {
  static envdPort = 49983

  static get domain(): string {
    const env = typeof process !== 'undefined' ? process.env : {}
    return env.WATASU_DOMAIN ?? 'watasu.io'
  }

  static get apiUrl(): string {
    const env = typeof process !== 'undefined' ? process.env : {}
    return env.WATASU_API_URL ?? `https://api.${this.domain}/v1`
  }

  static get sandboxUrl(): string | undefined {
    const env = typeof process !== 'undefined' ? process.env : {}
    return env.WATASU_SANDBOX_URL
  }

  static get debug(): boolean {
    const env = typeof process !== 'undefined' ? process.env : {}
    const value = (env.WATASU_DEBUG ?? 'false').toLowerCase()
    return value === 'true' || value === '1'
  }

  static get apiKey(): string | undefined {
    const env = typeof process !== 'undefined' ? process.env : {}
    return env.WATASU_API_KEY ?? env.WATASU_ACCESS_TOKEN
  }

  static get accessToken(): string | undefined {
    const env = typeof process !== 'undefined' ? process.env : {}
    return env.WATASU_ACCESS_TOKEN ?? this.apiKey
  }

  readonly apiKey?: string
  readonly accessToken?: string
  readonly domain: string
  readonly apiUrl: string
  /** Absolute sandbox data-plane URL override, primarily for local runtimes. */
  readonly sandboxUrl?: string
  readonly dataPlaneDomain: string
  readonly requestTimeoutMs: number
  readonly headers: Record<string, string>
  readonly apiHeaders: Record<string, string>
  readonly debug: boolean
  readonly logger?: Logger
  readonly signal?: AbortSignal
  readonly proxy?: string

  constructor(opts: ConnectionOpts = {}) {
    const env = typeof process !== 'undefined' ? process.env : {}
    const token =
      opts.apiKey ??
      opts.accessToken ??
      ConnectionConfig.apiKey ??
      ConnectionConfig.accessToken
    this.apiKey = token
    this.accessToken = opts.accessToken ?? token
    this.domain = opts.domain ?? ConnectionConfig.domain
    this.apiUrl = opts.apiUrl ?? env.WATASU_API_URL ?? `https://api.${this.domain}/v1`
    this.sandboxUrl = opts.sandboxUrl ?? ConnectionConfig.sandboxUrl
    this.dataPlaneDomain =
      opts.dataPlaneDomain ??
      env.WATASU_DATA_PLANE_DOMAIN ??
      'watasuhost.com'
    this.requestTimeoutMs = opts.requestTimeoutMs ?? 60_000
    this.headers = opts.headers ?? {}
    this.apiHeaders = opts.apiHeaders ?? {}
    this.debug = opts.debug ?? ConnectionConfig.debug
    this.logger = opts.logger
    this.signal = opts.signal
    this.proxy = opts.proxy
  }

  /** HTTP headers including the configured bearer token. */
  get authHeaders(): Record<string, string> {
    const token = this.accessToken ?? this.apiKey
    return token
      ? { ...this.headers, ...this.apiHeaders, Authorization: `Bearer ${token}` }
      : { ...this.headers, ...this.apiHeaders }
  }

  /** Return an abort signal that follows the caller signal and optional timeout. */
  getSignal(requestTimeoutMs = this.requestTimeoutMs, signal = this.signal): AbortSignal | undefined {
    if (requestTimeoutMs === undefined && signal === undefined) return undefined

    const controller = new AbortController()
    if (signal?.aborted) controller.abort()
    else signal?.addEventListener('abort', () => controller.abort(), { once: true })

    if (requestTimeoutMs > 0) {
      const timeout = setTimeout(() => controller.abort(), requestTimeoutMs)
      const maybeTimeout = timeout as { unref?: () => void }
      if (typeof maybeTimeout.unref === 'function') maybeTimeout.unref()
      controller.signal.addEventListener('abort', () => clearTimeout(timeout), { once: true })
    }

    return controller.signal
  }

  /** Return the sandbox data-plane API URL for a Watasu route token. */
  getSandboxUrl(sandboxId: string, opts: { sandboxDomain: string; envdPort: number }): string
  getSandboxUrl(sandboxId: string, opts: { sandboxDomain?: string; envdPort: number }): string {
    if (this.sandboxUrl) return this.sandboxUrl
    if (this.debug) return `http://localhost:${opts.envdPort}`
    return `https://${sandboxId}.sandbox.${opts.sandboxDomain ?? this.dataPlaneDomain}`
  }

  /** Return the direct sandbox data-plane API URL for a Watasu route token. */
  getSandboxDirectUrl(sandboxId: string, opts: { sandboxDomain: string; envdPort: number }): string
  getSandboxDirectUrl(sandboxId: string, opts: { sandboxDomain?: string; envdPort: number }): string {
    return this.getSandboxUrl(sandboxId, {
      sandboxDomain: opts.sandboxDomain ?? this.dataPlaneDomain,
      envdPort: opts.envdPort,
    })
  }

  /** Return the public hostname for a Watasu sandbox route token and port. */
  getHost(sandboxId: string, port: number, sandboxDomain: string): string
  getHost(sandboxId: string, port: number, sandboxDomain = this.dataPlaneDomain): string {
    if (this.debug) return `localhost:${port}`
    return `p${port}-${sandboxId}.sandbox.${sandboxDomain}`
  }
}
