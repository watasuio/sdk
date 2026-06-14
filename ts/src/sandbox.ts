import { Commands } from './commands.js'
import { ConnectionConfig, ConnectionOpts, SESSION_OPERATION_REQUEST_TIMEOUT_MS } from './connectionConfig.js'
import { DataPlaneClient, ControlClient } from './transport.js'
import { SandboxError, unsupported } from './errors.js'
import { Filesystem } from './filesystem.js'

export interface SandboxCreateOpts extends ConnectionOpts {
  /** Template slug to create. Defaults to "base". */
  template?: string
  /** Sandbox lifetime in milliseconds. Defaults to five minutes. */
  timeoutMs?: number
  metadata?: Record<string, string>
  envs?: Record<string, string>
  secure?: boolean
  allowInternetAccess?: boolean
  templateVersionId?: number | string
  team?: string
  cpu?: number
  memoryMb?: number
  networkClass?: string
  allowPackageRegistryAccess?: boolean
  exposedPorts?: unknown[]
  mcp?: unknown
  volumeMounts?: unknown
}

export interface SandboxConnectOpts extends ConnectionOpts {
  /** Optional new sandbox lifetime in milliseconds. */
  timeoutMs?: number
}

export interface SandboxInfo {
  sandboxId: string
  templateVersionId?: number
  state?: string
  metadata: Record<string, string>
  startedAt?: string
  endAt?: string
}

/** Running Watasu sandbox with ready `files` and `commands` helpers. */
export class Sandbox {
  /** Default template slug used when create is called without a template. */
  static readonly defaultTemplate = 'base'

  files: Filesystem
  commands: Commands
  readonly sandboxId: string
  readonly pty = { create: () => unsupported('sandbox.pty') }
  readonly git = { clone: () => unsupported('sandbox.git') }

  private readonly config: ConnectionConfig
  private readonly control: ControlClient
  private dataPlane: DataPlaneClient
  private sandbox: Record<string, unknown>

  constructor(opts: {
    sandboxId: string
    connectionConfig: ConnectionConfig
    control?: ControlClient
    session: unknown
    sandbox?: Record<string, unknown>
    envs?: Record<string, string>
  }) {
    this.sandboxId = String(opts.sandboxId)
    this.config = opts.connectionConfig
    this.control = opts.control ?? new ControlClient(this.config)
    this.sandbox = opts.sandbox ?? {}
    const dataPlane = dataPlaneFromSession(opts.session, this.config)
    this.dataPlane = dataPlane
    this.files = new Filesystem(dataPlane)
    this.commands = new Commands(dataPlane, this.config, opts.envs ?? {})
  }

  static async create(opts?: SandboxCreateOpts): Promise<Sandbox>
  static async create(template: string, opts?: SandboxCreateOpts): Promise<Sandbox>
  /** Create a sandbox and return it only after the API supplies a data-plane session. */
  static async create(templateOrOpts?: string | SandboxCreateOpts, opts: SandboxCreateOpts = {}): Promise<Sandbox> {
    const template = typeof templateOrOpts === 'string'
      ? templateOrOpts
      : templateOrOpts?.template ?? Sandbox.defaultTemplate
    const sandboxOpts = typeof templateOrOpts === 'string' ? opts : templateOrOpts ?? {}

    if (sandboxOpts.mcp !== undefined) unsupported('mcp')
    if (sandboxOpts.volumeMounts !== undefined) unsupported('volumeMounts')

    const config = new ConnectionConfig(sandboxOpts)
    const control = new ControlClient(config)
    const sandboxPayload: Record<string, unknown> = {
      template,
      timeout_seconds: Math.ceil((sandboxOpts.timeoutMs ?? 300_000) / 1000),
      metadata: sandboxOpts.metadata ?? {},
      allow_internet_access: sandboxOpts.allowInternetAccess ?? true,
    }
    putIfPresent(sandboxPayload, 'template_version_id', sandboxOpts.templateVersionId)
    putIfPresent(sandboxPayload, 'team', sandboxOpts.team)
    putIfPresent(sandboxPayload, 'cpu', sandboxOpts.cpu)
    putIfPresent(sandboxPayload, 'memory_mb', sandboxOpts.memoryMb)
    putIfPresent(sandboxPayload, 'network_class', sandboxOpts.networkClass)
    putIfPresent(sandboxPayload, 'allow_package_registry_access', sandboxOpts.allowPackageRegistryAccess)
    putIfPresent(sandboxPayload, 'exposed_ports', sandboxOpts.exposedPorts)

    const response = await control.post('/sandboxes', {
      json: { sandbox: sandboxPayload },
      requestTimeoutMs: sessionOperationRequestTimeout(config, sandboxOpts),
    })
    const sandbox = record(response.sandbox ?? response)
    const sandboxId = sandbox.id ?? sandbox.sandbox_id
    if (sandboxId === undefined) throw new SandboxError('create response did not include sandbox id')
    return new Sandbox({
      sandboxId: String(sandboxId),
      connectionConfig: config,
      control,
      session: response.session,
      sandbox,
      envs: sandboxOpts.envs,
    })
  }

  /** Connect to an existing sandbox and return it with a fresh data-plane session. */
  static async connect(sandboxId: string, opts: SandboxConnectOpts = {}): Promise<Sandbox> {
    const config = new ConnectionConfig(opts)
    const control = new ControlClient(config)
    const info = await control.get(`/sandboxes/${sandboxId}`)
    const response = await control.post(`/sandboxes/${sandboxId}/connect`, {
      json: { connect: opts.timeoutMs ? { timeout_seconds: Math.ceil(opts.timeoutMs / 1000) } : {} },
      requestTimeoutMs: sessionOperationRequestTimeout(config, opts),
    })
    return new Sandbox({
      sandboxId,
      connectionConfig: config,
      control,
      session: response.session,
      sandbox: record(response.sandbox ?? info.sandbox ?? {}),
    })
  }

  /** Refresh this sandbox's data-plane session in place. */
  async connect(opts: SandboxConnectOpts = {}): Promise<this> {
    const response = await this.control.post(`/sandboxes/${this.sandboxId}/connect`, {
      json: { connect: opts.timeoutMs ? { timeout_seconds: Math.ceil(opts.timeoutMs / 1000) } : {} },
      requestTimeoutMs: sessionOperationRequestTimeout(this.config, opts),
    })
    this.sandbox = record(response.sandbox ?? this.sandbox)
    const dataPlane = dataPlaneFromSession(response.session, this.config)
    this.dataPlane = dataPlane
    this.files = new Filesystem(dataPlane)
    this.commands = new Commands(dataPlane, this.config)
    return this
  }

  /** Destroy a sandbox by id. */
  static async kill(sandboxId: string, opts: ConnectionOpts = {}): Promise<boolean> {
    const control = new ControlClient(new ConnectionConfig(opts))
    await control.delete(`/sandboxes/${sandboxId}`)
    return true
  }

  /** Destroy this sandbox. */
  async kill(): Promise<boolean> {
    await this.control.delete(`/sandboxes/${this.sandboxId}`)
    return true
  }

  /** Set a sandbox's lifetime by id. */
  static async setTimeout(sandboxId: string, timeoutMs: number, opts: ConnectionOpts = {}): Promise<void> {
    const control = new ControlClient(new ConnectionConfig(opts))
    await control.patch(`/sandboxes/${sandboxId}`, {
      json: { sandbox: { timeout_seconds: Math.ceil(timeoutMs / 1000) } },
    })
  }

  /** Set this sandbox's lifetime. */
  async setTimeout(timeoutMs: number): Promise<void> {
    await this.control.patch(`/sandboxes/${this.sandboxId}`, {
      json: { sandbox: { timeout_seconds: Math.ceil(timeoutMs / 1000) } },
    })
  }

  /** Fetch control-plane metadata for a sandbox by id. */
  static async getInfo(sandboxId: string, opts: ConnectionOpts = {}): Promise<SandboxInfo> {
    const control = new ControlClient(new ConnectionConfig(opts))
    const payload = await control.get(`/sandboxes/${sandboxId}`)
    return sandboxInfo(record(payload.sandbox ?? payload))
  }

  /** Fetch the latest control-plane metadata for this sandbox. */
  async getInfo(): Promise<SandboxInfo> {
    const payload = await this.control.get(`/sandboxes/${this.sandboxId}`)
    return sandboxInfo(record(payload.sandbox ?? payload))
  }

  /** List sandboxes visible to the configured API key. */
  static async list(opts: ConnectionOpts & { team?: string } = {}): Promise<SandboxInfo[]> {
    const control = new ControlClient(new ConnectionConfig(opts))
    const payload = await control.get(opts.team ? `/sandboxes?team=${encodeURIComponent(opts.team)}` : '/sandboxes')
    const sandboxes = Array.isArray(payload.sandboxes) ? payload.sandboxes : []
    return sandboxes.map((item) => sandboxInfo(record(item)))
  }

  /** Return the public hostname for an exposed sandbox port. */
  async getHost(port: number): Promise<string> {
    const payload = await this.control.get(`/sandboxes/${this.sandboxId}/ports/${port}`)
    const portInfo = record(payload.sandbox_port ?? payload.port ?? payload)
    const value = portInfo.host ?? portInfo.url
    if (typeof value === 'string') return hostOnly(value)
    const routeToken = this.sandbox.route_token
    if (typeof routeToken !== 'string') throw new SandboxError('port response did not include host or url')
    return `p${port}-${routeToken}.sandbox.${this.config.dataPlaneDomain}`
  }

  pause(): never { unsupported('Sandbox.pause') }
  resume(): never { unsupported('Sandbox.resume') }
  createSnapshot(): never { unsupported('Sandbox.createSnapshot') }
  checkpoint(): never { unsupported('Sandbox.checkpoint') }
  restore(): never { unsupported('Sandbox.restore') }
}

function dataPlaneFromSession(session: unknown, config: ConnectionConfig): DataPlaneClient {
  const item = record(session)
  const token = item.token ?? item.access_token
  const url = item.data_plane_url
  if (!session) throw new SandboxError('sandbox session is required for data-plane operations')
  if (typeof token !== 'string' || typeof url !== 'string') {
    throw new SandboxError('sandbox session did not include data_plane_url and token')
  }
  return new DataPlaneClient(url, token, config)
}

function sessionOperationRequestTimeout(config: ConnectionConfig, opts: ConnectionOpts): number {
  if (opts.requestTimeoutMs !== undefined) return opts.requestTimeoutMs
  return Math.max(config.requestTimeoutMs, SESSION_OPERATION_REQUEST_TIMEOUT_MS)
}

function sandboxInfo(payload: Record<string, unknown>): SandboxInfo {
  return {
    sandboxId: String(payload.id ?? payload.sandbox_id ?? ''),
    templateVersionId: typeof payload.template_version_id === 'number' ? payload.template_version_id : undefined,
    state: typeof payload.state === 'string' ? payload.state : undefined,
    metadata: recordOfStrings(payload.metadata),
    startedAt: typeof payload.created_at === 'string' ? payload.created_at : undefined,
    endAt: typeof payload.deadline_at === 'string' ? payload.deadline_at : undefined,
  }
}

function putIfPresent(target: Record<string, unknown>, key: string, value: unknown): void {
  if (value !== undefined && value !== null) target[key] = value
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {}
}

function recordOfStrings(value: unknown): Record<string, string> {
  if (!value || typeof value !== 'object') return {}
  return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([key, item]) => [key, String(item)]))
}

function hostOnly(value: string): string {
  if (value.includes('://')) return new URL(value).host
  return value.split('/')[0]
}
