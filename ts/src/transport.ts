import { ConnectionConfig } from './connectionConfig.js'
import { AuthenticationError, TimeoutError, errorFromResponse } from './errors.js'

type JsonRecord = Record<string, unknown>

export class ControlClient {
  constructor(private readonly config: ConnectionConfig) {}

  get(path: string, opts: RequestOpts = {}) {
    return this.request(path, { ...opts, method: 'GET' })
  }

  post(path: string, opts: RequestOpts = {}) {
    return this.request(path, { ...opts, method: 'POST' })
  }

  put(path: string, opts: RequestOpts = {}) {
    return this.request(path, { ...opts, method: 'PUT' })
  }

  patch(path: string, opts: RequestOpts = {}) {
    return this.request(path, { ...opts, method: 'PATCH' })
  }

  delete(path: string, opts: RequestOpts = {}) {
    return this.request(path, { ...opts, method: 'DELETE' })
  }

  private async request(path: string, opts: RequestOpts): Promise<JsonRecord> {
    if (!this.config.apiKey) {
      throw new AuthenticationError('WATASU_API_KEY is required')
    }

    const response = await fetchWithTimeout(
      joinUrl(this.config.apiUrl, path),
      {
        method: opts.method,
        headers: {
          ...this.config.authHeaders,
          ...(opts.json ? { 'content-type': 'application/json' } : {}),
        },
        body: opts.json ? JSON.stringify(opts.json) : undefined,
      },
      opts.requestTimeoutMs ?? this.config.requestTimeoutMs
    )

    return parseJsonResponse(response)
  }
}

export class DataPlaneClient {
  constructor(
    readonly baseUrl: string,
    readonly token: string,
    private readonly config: ConnectionConfig
  ) {}

  getJson(path: string, opts: RequestOpts = {}) {
    return this.request(path, { ...opts, method: 'GET' }) as Promise<JsonRecord>
  }

  postJson(path: string, opts: RequestOpts = {}) {
    return this.request(path, { ...opts, method: 'POST' }) as Promise<JsonRecord>
  }

  deleteJson(path: string, opts: RequestOpts = {}) {
    return this.request(path, { ...opts, method: 'DELETE' }) as Promise<JsonRecord>
  }

  async getBytes(path: string, opts: RequestOpts = {}): Promise<Uint8Array> {
    const response = await this.raw(path, { ...opts, method: 'GET' })
    return new Uint8Array(await response.arrayBuffer())
  }

  async putJson(path: string, data: BodyInit | Uint8Array, opts: RequestOpts = {}) {
    return parseJsonResponse(
      await this.raw(path, {
        ...opts,
        method: 'PUT',
        body: data,
        headers: { 'content-type': 'application/octet-stream', ...(opts.headers ?? {}) },
      })
    )
  }

  async raw(path: string, opts: RequestOpts): Promise<Response> {
    const response = await fetchWithTimeout(
      joinUrl(this.baseUrl, path),
      {
        method: opts.method,
        headers: {
          Authorization: `Bearer ${this.token}`,
          ...(opts.json ? { 'content-type': 'application/json' } : {}),
          ...(opts.headers ?? {}),
        },
        body: opts.json ? JSON.stringify(opts.json) : (opts.body as BodyInit | undefined),
      },
      opts.requestTimeoutMs ?? this.config.requestTimeoutMs
    )

    if (!response.ok) {
      throw errorFromResponse(response.status, await readJsonOrText(response))
    }
    return response
  }

  private async request(path: string, opts: RequestOpts): Promise<unknown> {
    return parseJsonResponse(await this.raw(path, opts))
  }
}

export interface RequestOpts {
  method?: string
  json?: unknown
  body?: BodyInit | Uint8Array
  headers?: Record<string, string>
  requestTimeoutMs?: number
}

export function withQuery(path: string, params: Record<string, string | number | boolean | undefined>) {
  const url = new URL(path, 'https://placeholder.local')
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) url.searchParams.set(key, String(value))
  }
  return `${url.pathname}${url.search}`
}

async function parseJsonResponse(response: Response): Promise<JsonRecord> {
  const payload = await readJsonOrText(response)
  if (!response.ok) {
    throw errorFromResponse(response.status, payload)
  }
  return payload && typeof payload === 'object' ? (payload as JsonRecord) : {}
}

async function readJsonOrText(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return {}
  try {
    return JSON.parse(text) as unknown
  } catch {
    return { message: text }
  }
}

function fetchWithTimeout(url: string, init: RequestInit, timeoutMs: number): Promise<Response> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  return fetch(url, { ...init, signal: controller.signal }).catch((error) => {
    if (error?.name === 'AbortError') throw new TimeoutError()
    throw error
  }).finally(() => clearTimeout(timeout))
}

function joinUrl(base: string, path: string): string {
  const normalizedBase = base.replace(/\/+$/, '')
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${normalizedBase}${normalizedPath}`
}
