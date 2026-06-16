declare const process:
  | {
      env: Record<string, string | undefined>
    }
  | undefined

export const KEEPALIVE_PING_INTERVAL_SEC = 50
export const SESSION_OPERATION_REQUEST_TIMEOUT_MS = 150_000

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
  signal?: AbortSignal
  proxy?: unknown
}

/** Resolved connection settings used by control-plane and data-plane clients. */
export class ConnectionConfig {
  readonly apiKey?: string
  readonly domain: string
  readonly apiUrl: string
  /** Absolute sandbox data-plane URL override, primarily for local runtimes. */
  readonly sandboxUrl?: string
  readonly dataPlaneDomain: string
  readonly requestTimeoutMs: number
  readonly headers: Record<string, string>
  readonly apiHeaders: Record<string, string>
  readonly debug: boolean
  readonly signal?: AbortSignal
  readonly proxy?: unknown

  constructor(opts: ConnectionOpts = {}) {
    const env = typeof process !== 'undefined' ? process.env : {}
    this.apiKey =
      opts.apiKey ??
      opts.accessToken ??
      env.WATASU_API_KEY
    this.domain = opts.domain ?? env.WATASU_DOMAIN ?? 'watasu.io'
    this.apiUrl =
      opts.apiUrl ?? env.WATASU_API_URL ?? `https://api.${this.domain}/v1`
    this.sandboxUrl =
      opts.sandboxUrl ??
      env.WATASU_SANDBOX_URL
    this.dataPlaneDomain =
      opts.dataPlaneDomain ??
      env.WATASU_DATA_PLANE_DOMAIN ??
      'watasuhost.com'
    this.requestTimeoutMs = opts.requestTimeoutMs ?? 60_000
    this.headers = opts.headers ?? {}
    this.apiHeaders = opts.apiHeaders ?? {}
    this.debug = opts.debug ?? false
    this.signal = opts.signal
    this.proxy = opts.proxy
  }

  /** HTTP headers including the configured bearer token. */
  get authHeaders(): Record<string, string> {
    return this.apiKey
      ? { ...this.headers, ...this.apiHeaders, Authorization: `Bearer ${this.apiKey}` }
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
  getSandboxUrl(sandboxId: string, opts: { sandboxDomain?: string; envdPort: number }): string {
    if (this.sandboxUrl) return this.sandboxUrl
    if (this.debug) return `http://localhost:${opts.envdPort}`
    return `https://${sandboxId}.sandbox.${opts.sandboxDomain ?? this.dataPlaneDomain}`
  }

  /** Return the direct sandbox data-plane API URL for a Watasu route token. */
  getSandboxDirectUrl(sandboxId: string, opts: { sandboxDomain?: string; envdPort: number }): string {
    return this.getSandboxUrl(sandboxId, opts)
  }

  /** Return the public hostname for a Watasu sandbox route token and port. */
  getHost(sandboxId: string, port: number, sandboxDomain = this.dataPlaneDomain): string {
    if (this.debug) return `localhost:${port}`
    return `p${port}-${sandboxId}.sandbox.${sandboxDomain}`
  }
}
