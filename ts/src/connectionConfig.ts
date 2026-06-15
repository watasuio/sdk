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
  dataPlaneDomain?: string
  requestTimeoutMs?: number
  headers?: Record<string, string>
  proxy?: unknown
}

/** Resolved connection settings used by control-plane and data-plane clients. */
export class ConnectionConfig {
  readonly apiKey?: string
  readonly domain: string
  readonly apiUrl: string
  readonly dataPlaneDomain: string
  readonly requestTimeoutMs: number
  readonly headers: Record<string, string>
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
    this.dataPlaneDomain =
      opts.dataPlaneDomain ??
      env.WATASU_DATA_PLANE_DOMAIN ??
      'watasuhost.com'
    this.requestTimeoutMs = opts.requestTimeoutMs ?? 60_000
    this.headers = opts.headers ?? {}
    this.proxy = opts.proxy
  }

  /** HTTP headers including the configured bearer token. */
  get authHeaders(): Record<string, string> {
    return this.apiKey
      ? { ...this.headers, Authorization: `Bearer ${this.apiKey}` }
      : { ...this.headers }
  }
}
