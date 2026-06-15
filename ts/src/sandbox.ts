import { Commands } from './commands.js'
import { ConnectionConfig, ConnectionOpts, SESSION_OPERATION_REQUEST_TIMEOUT_MS } from './connectionConfig.js'
import { DataPlaneClient, ControlClient, withQuery } from './transport.js'
import { ConflictError, FileNotFoundError, NotFoundError, SandboxError, unsupported } from './errors.js'
import { Filesystem } from './filesystem.js'
import { Git } from './git.js'
import { Pty } from './pty.js'
import { ProcessManager } from './process.js'
import { TerminalManager } from './terminal.js'

export interface SandboxCreateOpts extends ConnectionOpts {
  /** Template slug to create. Defaults to "base". */
  template?: string
  /** Sandbox lifetime in milliseconds. Defaults to five minutes. */
  timeoutMs?: number
  metadata?: Record<string, string>
  envs?: Record<string, string>
  secure?: boolean
  allowInternetAccess?: boolean
  network?: SandboxNetworkUpdate
  team?: string
  /** MCP gateway configuration to launch inside an `mcp-gateway` sandbox. */
  mcp?: McpServer
  /** Timeout lifecycle policy. Defaults to killing the sandbox at timeout. */
  lifecycle?: SandboxLifecycle
  /** Persistent volumes to mount, keyed by guest path. */
  volumeMounts?: Record<string, string | { name: string }>
}

export interface SandboxLifecycle {
  onTimeout: 'kill' | 'pause'
  autoResume?: boolean
}

export type SandboxNetworkSelector = string | string[]

export interface SandboxNetworkUpdate {
  allowOut?: SandboxNetworkSelector
  denyOut?: SandboxNetworkSelector
  allowInternetAccess?: boolean
  allowPackageRegistryAccess?: boolean
  allowPublicTraffic?: boolean
  egressProfile?: string
  egressProfiles?: string[]
  networkClass?: string
  rules?: unknown
  maskRequestHost?: string
}

export interface SandboxNetworkUpdateOpts extends ConnectionOpts {}

export interface SandboxConnectOpts extends ConnectionOpts {
  /** Optional new sandbox lifetime in milliseconds. */
  timeoutMs?: number
}

export interface SandboxListOpts extends ConnectionOpts {
  /** Filters applied by the Watasu API. */
  query?: {
    metadata?: Record<string, string>
    state?: Array<'running' | 'paused' | string>
  }
  /** Maximum number of sandboxes to return per page. */
  limit?: number
  /** Pagination cursor returned by a previous page. */
  nextToken?: string
  /** Team slug to list within. */
  team?: string
}

export interface SandboxInfo {
  sandboxId: string
  templateId?: string
  name?: string
  state?: string
  lifecycle?: SandboxInfoLifecycle
  volumeMounts?: Array<{ name: string; path: string }>
  metadata: Record<string, string>
  startedAt?: string
  endAt?: string
}

export interface SandboxInfoLifecycle {
  onTimeout: 'kill' | 'pause' | string
  autoResume: boolean
}

export interface SandboxMetrics {
  sandboxId?: string
  state?: string
  node?: string
  backend?: string
  cpuCount?: number
  memoryMb?: number
  raw: Record<string, unknown>
}

export interface SandboxMetricsOpts extends ConnectionOpts {
  /** Start time for the metrics. Defaults to the sandbox start time. */
  start?: Date
  /** End time for the metrics. Defaults to the current time. */
  end?: Date
}

export interface SnapshotInfo {
  snapshotId: string
  sandboxId?: string
  name?: string
  status?: string
  sizeBytes?: number
  createdAt?: string
  expiresAt?: string
  raw: Record<string, unknown>
}

export interface FileUrlInfo {
  method: string
  path: string
  url: string
  expiresAt?: string
  raw: Record<string, unknown>
}

/** MCP gateway configuration accepted by `Sandbox.create({ mcp })`. */
export type McpServer = Record<string, unknown>

/** Name accepted by `Template.addMcpServer`. Watasu keeps this open-ended. */
export type McpServerName = string

export interface SandboxUrlOpts extends ConnectionOpts {
  user?: string
  useSignatureExpiration?: number
  expiresInSeconds?: number
}

export interface CreateSnapshotOpts extends ConnectionOpts {
  name?: string
  metadata?: Record<string, string>
  expiresAt?: string
  quiesceMode?: string
}

export interface SnapshotListOpts extends ConnectionOpts {
  /** Filter snapshots by source sandbox id. */
  sandboxId?: string
  /** Maximum number of snapshots to return per page. */
  limit?: number
  /** Pagination cursor returned by a previous page. */
  nextToken?: string
}

export interface RestoreSnapshotOpts extends ConnectionOpts {
  checkpointId?: string | number
  snapshotId?: string | number
  timeout?: number
  timeoutMs?: number
}

/** Paginator for listing sandbox snapshots. */
export class SnapshotPaginator {
  hasNext = true
  nextToken: string | undefined

  constructor(private readonly opts: SnapshotListOpts = {}) {
    this.nextToken = opts.nextToken
  }

  /** Fetch the next page of snapshot metadata. */
  async nextItems(opts: ConnectionOpts = {}): Promise<SnapshotInfo[]> {
    if (!this.hasNext) throw new SandboxError('No more snapshots to fetch')

    const config = new ConnectionConfig({ ...this.opts, ...opts })
    const control = new ControlClient(config)
    const payload = await control.get(snapshotListPath(this.opts, this.nextToken), {
      requestTimeoutMs: opts.requestTimeoutMs,
    })
    this.nextToken = stringValue(payload.next_token ?? payload.nextToken)
    this.hasNext = this.nextToken !== undefined
    const snapshots = Array.isArray(payload.snapshots)
      ? payload.snapshots
      : Array.isArray(payload.sandbox_checkpoints) ? payload.sandbox_checkpoints : []
    return snapshots.map((item) => snapshotInfo(record(item)))
  }

  /** Drain all remaining pages into one list. */
  async listItems(opts: ConnectionOpts = {}): Promise<SnapshotInfo[]> {
    const items: SnapshotInfo[] = []
    while (this.hasNext) items.push(...await this.nextItems(opts))
    return items
  }
}

/** Paginator for listing sandboxes. */
export class SandboxPaginator {
  hasNext = true
  nextToken: string | undefined

  constructor(private readonly opts: SandboxListOpts = {}) {
    this.nextToken = opts.nextToken
  }

  /** Fetch the next page of sandbox metadata. */
  async nextItems(opts: ConnectionOpts = {}): Promise<SandboxInfo[]> {
    if (!this.hasNext) throw new SandboxError('No more sandboxes to fetch')

    const config = new ConnectionConfig({ ...this.opts, ...opts })
    const control = new ControlClient(config)
    const payload = await control.get(sandboxListPath(this.opts, this.nextToken), {
      requestTimeoutMs: opts.requestTimeoutMs,
    })
    this.nextToken = stringValue(payload.next_token ?? payload.nextToken)
    this.hasNext = this.nextToken !== undefined
    const sandboxes = Array.isArray(payload.sandboxes) ? payload.sandboxes : []
    return sandboxes.map((item) => sandboxInfo(record(item)))
  }

  /** Drain all remaining pages into one list. */
  async listItems(opts: ConnectionOpts = {}): Promise<SandboxInfo[]> {
    const items: SandboxInfo[] = []
    while (this.hasNext) items.push(...await this.nextItems(opts))
    return items
  }
}

/** Running Watasu sandbox with ready `files` and `commands` helpers. */
export class Sandbox {
  /** Default template slug used when create is called without a template. */
  static readonly defaultTemplate: string = 'base'
  /** Default template slug used by MCP creation once Watasu supports it. */
  static readonly defaultMcpTemplate: string = 'mcp-gateway'
  /** Default sandbox lifetime in milliseconds. */
  static readonly defaultSandboxTimeoutMs = 300_000

  files: Filesystem
  filesystem: Filesystem
  commands: Commands
  process: ProcessManager
  pty: Pty
  terminal: TerminalManager
  git: Git
  cwd: string | undefined
  envVars: Record<string, string>
  readonly sandboxId: string

  private readonly mcpPort = 50005
  private mcpToken: string | undefined
  private readonly config: ConnectionConfig
  private readonly control: ControlClient
  private readonly envs: Record<string, string>
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
    this.envs = opts.envs ?? {}
    this.envVars = this.envs
    this.sandbox = opts.sandbox ?? {}
    const dataPlane = dataPlaneFromSession(opts.session, this.config)
    this.dataPlane = dataPlane
    this.files = new Filesystem(dataPlane)
    this.filesystem = this.files
    this.commands = new Commands(dataPlane, this.config, this.envs)
    this.process = new ProcessManager(this.commands)
    this.pty = new Pty(dataPlane, this.config)
    this.terminal = new TerminalManager(this.pty)
    this.git = new Git(dataPlane)
  }

  /** Sandbox id alias used by SDK-compatible code. */
  get id(): string {
    return this.sandboxId
  }

  static async create(opts?: SandboxCreateOpts): Promise<Sandbox>
  static async create(template: string, opts?: SandboxCreateOpts): Promise<Sandbox>
  /** Create a sandbox and return it only after the API supplies a data-plane session. */
  static async create(templateOrOpts?: string | SandboxCreateOpts, opts: SandboxCreateOpts = {}): Promise<Sandbox> {
    const sandboxOpts = typeof templateOrOpts === 'string' ? opts : templateOrOpts ?? {}
    const template = typeof templateOrOpts === 'string'
      ? templateOrOpts
      : templateOrOpts?.template ?? (sandboxOpts.mcp === undefined ? this.defaultTemplate : undefined)

    const config = new ConnectionConfig(sandboxOpts)
    const control = new ControlClient(config)
    const sandboxPayload: Record<string, unknown> = {
      timeout: Math.ceil((sandboxOpts.timeoutMs ?? 300_000) / 1000),
      metadata: sandboxOpts.metadata ?? {},
      env_vars: sandboxOpts.envs ?? {},
      secure: sandboxOpts.secure ?? true,
      allow_internet_access: sandboxOpts.allowInternetAccess ?? true,
    }
    putIfPresent(sandboxPayload, 'template_id', template)
    putIfPresent(sandboxPayload, 'mcp', sandboxOpts.mcp)
    putIfPresent(sandboxPayload, 'lifecycle', lifecyclePayload(sandboxOpts.lifecycle))
    putIfPresent(sandboxPayload, 'volume_mounts', volumeMountsPayload(sandboxOpts.volumeMounts))
    Object.assign(sandboxPayload, networkUpdatePayload(sandboxOpts.network))
    putIfPresent(sandboxPayload, 'team', sandboxOpts.team)

    const response = await control.post('/sandboxes', {
      json: sandboxPayload,
      requestTimeoutMs: sessionOperationRequestTimeout(config, sandboxOpts),
    })
    const sandbox = record(response.sandbox ?? response)
    const sandboxId = sandbox.id ?? sandbox.sandbox_id
    if (sandboxId === undefined) throw new SandboxError('create response did not include sandbox id')
    const sandboxInstance = new this({
      sandboxId: String(sandboxId),
      connectionConfig: config,
      control,
      session: response.session,
      sandbox,
      envs: sandboxOpts.envs,
    })
    return sandboxInstance
  }

  /** Connect to an existing sandbox and return it with a fresh data-plane session. */
  static async connect(sandboxId: string, opts: SandboxConnectOpts = {}): Promise<Sandbox> {
    const config = new ConnectionConfig(opts)
    const control = new ControlClient(config)
    const info = await control.get(`/sandboxes/${sandboxId}`)
    const response = await control.post(`/sandboxes/${sandboxId}/resume`, {
      json: opts.timeoutMs ? { timeout: Math.ceil(opts.timeoutMs / 1000) } : {},
      requestTimeoutMs: sessionOperationRequestTimeout(config, opts),
    })
    return new this({
      sandboxId,
      connectionConfig: config,
      control,
      session: response.session,
      sandbox: record(response.sandbox ?? info.sandbox ?? {}),
    })
  }

  /** Alias for `connect`. */
  static async reconnect(sandboxId: string, opts?: SandboxConnectOpts): Promise<Sandbox>
  static async reconnect(opts: SandboxConnectOpts & { sandboxID: string }): Promise<Sandbox>
  static async reconnect(sandboxOrOpts: string | (SandboxConnectOpts & { sandboxID: string }), opts: SandboxConnectOpts = {}): Promise<Sandbox> {
    if (typeof sandboxOrOpts === 'string') return this.connect(sandboxOrOpts, opts)
    return this.connect(sandboxOrOpts.sandboxID, sandboxOrOpts)
  }

  /** Refresh this sandbox's data-plane session in place. */
  async connect(opts: SandboxConnectOpts = {}): Promise<this> {
    const response = await this.control.post(`/sandboxes/${this.sandboxId}/resume`, {
      json: opts.timeoutMs ? { timeout: Math.ceil(opts.timeoutMs / 1000) } : {},
      requestTimeoutMs: sessionOperationRequestTimeout(this.config, opts),
    })
    this.sandbox = record(response.sandbox ?? this.sandbox)
    const dataPlane = dataPlaneFromSession(response.session, this.config)
    this.dataPlane = dataPlane
    this.files = new Filesystem(dataPlane)
    this.filesystem = this.files
    this.commands = new Commands(dataPlane, this.config, this.envs)
    this.process = new ProcessManager(this.commands)
    this.pty = new Pty(dataPlane, this.config)
    this.terminal = new TerminalManager(this.pty)
    this.git = new Git(dataPlane)
    return this
  }

  /** Resume a paused sandbox by id. */
  static async resume(sandboxId: string, opts: SandboxConnectOpts = {}): Promise<boolean> {
    await Sandbox.connect(sandboxId, opts)
    return true
  }

  /** Pause a sandbox by id. Returns false when it was already paused. */
  static async betaPause(sandboxId: string, opts: ConnectionOpts = {}): Promise<boolean> {
    const control = new ControlClient(new ConnectionConfig(opts))
    try {
      await control.post(`/sandboxes/${sandboxId}/pause`, {
        requestTimeoutMs: opts.requestTimeoutMs,
      })
      return true
    } catch (error) {
      if (error instanceof ConflictError) return false
      throw error
    }
  }

  /** Alias for `betaPause`. */
  static async pause(sandboxId: string, opts: ConnectionOpts = {}): Promise<boolean> {
    return this.betaPause(sandboxId, opts)
  }

  /** Destroy a sandbox by id. */
  static async kill(sandboxId: string, opts: ConnectionOpts | string = {}): Promise<boolean> {
    const control = new ControlClient(new ConnectionConfig(typeof opts === 'string' ? { apiKey: opts } : opts))
    await control.delete(`/sandboxes/${sandboxId}`)
    return true
  }

  /** Fetch sandbox metrics by id. */
  static async getMetrics(sandboxId: string, opts: SandboxMetricsOpts = {}): Promise<SandboxMetrics[]> {
    const control = new ControlClient(new ConnectionConfig(opts))
    const payload = await control.get(metricsPath(sandboxId, opts), {
      requestTimeoutMs: opts.requestTimeoutMs,
    })
    return metricsList(payload.metrics ?? payload)
  }

  /** Atomically replace a sandbox's network egress policy by id. */
  static async updateNetwork(sandboxId: string, network: SandboxNetworkUpdate, opts: SandboxNetworkUpdateOpts = {}): Promise<void> {
    await this.putNetwork(sandboxId, network, opts)
  }

  private static async putNetwork(sandboxId: string, network: SandboxNetworkUpdate, opts: SandboxNetworkUpdateOpts = {}): Promise<Record<string, unknown> | undefined> {
    const control = new ControlClient(new ConnectionConfig(opts))
    const response = await control.put(`/sandboxes/${sandboxId}/network`, {
      json: networkUpdatePayload(network),
      requestTimeoutMs: opts.requestTimeoutMs,
    })
    return response.sandbox === undefined ? undefined : record(response.sandbox)
  }

  /** Deprecated alias for `getInfo`. */
  static async getFullInfo(sandboxId: string, opts: ConnectionOpts = {}): Promise<SandboxInfo> {
    return this.getInfo(sandboxId, opts)
  }

  /** Create a Watasu checkpoint using snapshot naming. */
  static async createSnapshot(sandboxId: string, opts: CreateSnapshotOpts = {}): Promise<SnapshotInfo> {
    const control = new ControlClient(new ConnectionConfig(opts))
    const payload = await control.post(`/sandboxes/${sandboxId}/snapshots`, {
      json: snapshotPayload(opts),
      requestTimeoutMs: opts.requestTimeoutMs,
    })
    return snapshotInfo(record(payload.sandbox_checkpoint ?? payload.snapshot ?? payload))
  }

  /** List snapshots visible to the configured API key. */
  static listSnapshots(opts: SnapshotListOpts = {}): SnapshotPaginator {
    return new SnapshotPaginator(opts)
  }

  /** Delete a snapshot by id. Returns `false` when the snapshot does not exist. */
  static async deleteSnapshot(snapshotId: string, opts: ConnectionOpts = {}): Promise<boolean> {
    const control = new ControlClient(new ConnectionConfig(opts))
    try {
      await control.delete(`/sandbox_snapshots/${snapshotId}`, {
        requestTimeoutMs: opts.requestTimeoutMs,
      })
      return true
    } catch (error) {
      if (error instanceof NotFoundError) return false
      throw error
    }
  }

  /** Destroy this sandbox. */
  async kill(): Promise<boolean> {
    await this.control.delete(`/sandboxes/${this.sandboxId}`)
    return true
  }

  /** Check if this sandbox is in a runtime-active lifecycle state. */
  async isRunning(opts: Pick<ConnectionOpts, 'requestTimeoutMs'> = {}): Promise<boolean> {
    try {
      const payload = await this.control.get(`/sandboxes/${this.sandboxId}`, {
        requestTimeoutMs: opts.requestTimeoutMs,
      })
      const item = record(payload.sandbox ?? payload)
      return ['creating', 'ready', 'checkpointing', 'restoring', 'stopping'].includes(String(item.state ?? ''))
    } catch (error) {
      if (error instanceof NotFoundError) return false
      throw error
    }
  }

  /** Set a sandbox's lifetime by id. */
  static async setTimeout(sandboxId: string, timeoutMs: number, opts: ConnectionOpts = {}): Promise<void> {
    const control = new ControlClient(new ConnectionConfig(opts))
    await control.post(`/sandboxes/${sandboxId}/timeout`, {
      json: { timeout: Math.ceil(timeoutMs / 1000) },
    })
  }

  /** Set this sandbox's lifetime. */
  async setTimeout(timeoutMs: number): Promise<void> {
    await this.control.post(`/sandboxes/${this.sandboxId}/timeout`, {
      json: { timeout: Math.ceil(timeoutMs / 1000) },
    })
  }

  /** Keep the sandbox alive for `duration` milliseconds. */
  async keepAlive(duration: number): Promise<void> {
    await this.setTimeout(duration)
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

  /** Fetch latest sandbox metrics. */
  async getMetrics(opts: SandboxMetricsOpts = {}): Promise<SandboxMetrics[]> {
    return Sandbox.getMetrics(this.sandboxId, { ...this.configOptions(), ...opts })
  }

  /** Create a Watasu checkpoint using snapshot naming. */
  async createSnapshot(opts: CreateSnapshotOpts = {}): Promise<SnapshotInfo> {
    return Sandbox.createSnapshot(this.sandboxId, { ...this.configOptions(), ...opts })
  }

  /** Delete a snapshot by id. */
  async deleteSnapshot(snapshotId: string, opts: ConnectionOpts = {}): Promise<boolean> {
    return Sandbox.deleteSnapshot(snapshotId, { ...this.configOptions(), ...opts })
  }

  /** Watasu-native alias for `createSnapshot`. */
  async checkpoint(opts: CreateSnapshotOpts = {}): Promise<SnapshotInfo> {
    return this.createSnapshot(opts)
  }

  /** List checkpoints for this sandbox using snapshot naming. */
  listSnapshots(opts: Omit<SnapshotListOpts, 'sandboxId'> = {}): SnapshotPaginator {
    return Sandbox.listSnapshots({ ...this.configOptions(), ...opts, sandboxId: this.sandboxId })
  }

  /** Restore a checkpoint into a new sandbox and return its control-plane info. */
  async restore(opts: RestoreSnapshotOpts | string | number = {}): Promise<SandboxInfo> {
    const restoreOpts = typeof opts === 'string' || typeof opts === 'number'
      ? { checkpointId: opts }
      : opts
    const checkpointId = restoreOpts.checkpointId ?? restoreOpts.snapshotId
    if (checkpointId === undefined) throw new SandboxError('checkpointId or snapshotId is required')

    const payload: Record<string, unknown> = { checkpoint_id: checkpointId }
    if (restoreOpts.timeout !== undefined) payload.timeout_seconds = restoreOpts.timeout
    if (restoreOpts.timeoutMs !== undefined) payload.timeout_seconds = Math.ceil(restoreOpts.timeoutMs / 1000)

    const response = await this.control.post(`/sandboxes/${this.sandboxId}/restore`, {
      json: payload,
      requestTimeoutMs: restoreOpts.requestTimeoutMs,
    })
    return sandboxInfo(record(response.sandbox ?? response))
  }

  /** Return a paginator for sandboxes visible to the configured API key. */
  static list(opts: SandboxListOpts | string = {}): SandboxPaginator {
    const listOpts = typeof opts === 'string' ? { apiKey: opts } : opts
    return new SandboxPaginator(listOpts)
  }

  /** Return the public hostname for an exposed sandbox port. */
  getHost(port: number): string {
    const routeToken =
      this.sandbox.route_token ??
      this.sandbox.routeToken ??
      routeTokenFromDataPlaneUrl(this.dataPlane.baseUrl, this.config.dataPlaneDomain)
    if (typeof routeToken !== 'string') throw new SandboxError('port response did not include host or url')
    return `p${port}-${routeToken}.sandbox.${this.config.dataPlaneDomain}`
  }

  /** Return the public hostname for the sandbox or an exposed sandbox port. */
  getHostname(port?: number): string {
    if (port !== undefined) return this.getHost(port)
    return new URL(this.dataPlane.baseUrl).host
  }

  /** Return the conventional MCP URL for this sandbox. */
  getMcpUrl(): string {
    return `https://${this.getHost(this.mcpPort)}/mcp`
  }

  /** Return the MCP gateway token when the sandbox contains one. */
  async getMcpToken(): Promise<string | undefined> {
    if (this.mcpToken !== undefined) return this.mcpToken
    try {
      const token = await this.files.read('/etc/mcp-gateway/.token', { user: 'root' })
      this.mcpToken = String(token).trim() || undefined
      return this.mcpToken
    } catch (error) {
      if (error instanceof FileNotFoundError || error instanceof NotFoundError) return undefined
      throw error
    }
  }

  /** Return a protocol string for a secure or insecure sandbox URL. */
  getProtocol(baseProtocol = 'http', secure = true): string {
    return `${baseProtocol}${secure ? 's' : ''}`
  }

  /** Close the local SDK attachment. This does not destroy the sandbox. */
  async close(): Promise<void> {}

  /** Get a signed URL that accepts a POST upload for a sandbox file path. */
  async uploadUrl(path = '', opts: SandboxUrlOpts = {}): Promise<string> {
    const fileUrl = await this.fileUrl('/upload_url', path, opts)
    return fileUrl.url
  }

  /** Get a signed URL that accepts a GET download for a sandbox file path. */
  async downloadUrl(path: string, opts: SandboxUrlOpts = {}): Promise<string> {
    const fileUrl = await this.fileUrl('/download_url', path, opts)
    return fileUrl.url
  }

  /** Get signed upload URL metadata for a sandbox file path. */
  async uploadUrlInfo(path = '', opts: SandboxUrlOpts = {}): Promise<FileUrlInfo> {
    return this.fileUrl('/upload_url', path, opts)
  }

  /** Get signed download URL metadata for a sandbox file path. */
  async downloadUrlInfo(path: string, opts: SandboxUrlOpts = {}): Promise<FileUrlInfo> {
    return this.fileUrl('/download_url', path, opts)
  }

  /** Atomically replace this sandbox's network egress policy. */
  async updateNetwork(network: SandboxNetworkUpdate, opts: SandboxNetworkUpdateOpts = {}): Promise<void> {
    const sandbox = await Sandbox.putNetwork(this.sandboxId, network, { ...this.configOptions(), ...opts })
    this.sandbox = sandbox ?? this.sandbox
  }

  /** Pause this sandbox. Returns false when it was already paused. */
  async betaPause(opts: ConnectionOpts = {}): Promise<boolean> {
    return Sandbox.betaPause(this.sandboxId, { ...this.configOptions(), ...opts })
  }

  /** Alias for `betaPause`. */
  async pause(opts: ConnectionOpts = {}): Promise<boolean> {
    return this.betaPause(opts)
  }

  /** Resume this sandbox and refresh its data-plane session. */
  async resume(opts: SandboxConnectOpts = {}): Promise<boolean> {
    await this.connect(opts)
    return true
  }

  private async fileUrl(route: '/upload_url' | '/download_url', path: string, opts: SandboxUrlOpts): Promise<FileUrlInfo> {
    const payload = await this.control.post(`/sandboxes/${this.sandboxId}/files${route}`, {
      json: compactRecord({
        path,
        user: opts.user,
        use_signature_expiration: opts.useSignatureExpiration,
        expires_in_seconds: opts.expiresInSeconds,
      }),
      requestTimeoutMs: opts.requestTimeoutMs,
    })
    return fileUrlInfo(record(payload.file_url ?? payload))
  }

  /** POST JSON to the sandbox data-plane runtime API. */
  protected async runtimePostJson(path: string, json: Record<string, unknown>, opts: ConnectionOpts = {}): Promise<Record<string, unknown>> {
    return this.dataPlane.postJson(path, {
      json,
      requestTimeoutMs: opts.requestTimeoutMs,
    })
  }

  /** GET JSON from the sandbox data-plane runtime API. */
  protected async runtimeGetJson(path: string, opts: ConnectionOpts = {}): Promise<Record<string, unknown>> {
    return this.dataPlane.getJson(path, {
      requestTimeoutMs: opts.requestTimeoutMs,
    })
  }

  /** DELETE JSON from the sandbox data-plane runtime API. */
  protected async runtimeDeleteJson(path: string, opts: ConnectionOpts = {}): Promise<Record<string, unknown>> {
    return this.dataPlane.deleteJson(path, {
      requestTimeoutMs: opts.requestTimeoutMs,
    })
  }

  private configOptions(): ConnectionOpts {
    return {
      apiKey: this.config.apiKey,
      apiUrl: this.config.apiUrl,
      dataPlaneDomain: this.config.dataPlaneDomain,
      requestTimeoutMs: this.config.requestTimeoutMs,
    }
  }
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

function sandboxListPath(opts: SandboxListOpts, nextToken: string | undefined): string {
  const params = new URLSearchParams()
  if (opts.team) params.set('team', opts.team)
  if (opts.limit !== undefined) params.set('limit', String(opts.limit))
  if (nextToken) params.set('next_token', nextToken)

  if (opts.query?.metadata) {
    for (const [key, value] of Object.entries(opts.query.metadata)) {
      params.append(`query[metadata][${key}]`, value)
    }
  }
  for (const state of opts.query?.state ?? []) {
    params.append('query[state][]', state)
  }

  const query = params.toString()
  return query ? `/sandboxes?${query}` : '/sandboxes'
}

function snapshotListPath(opts: SnapshotListOpts, nextToken: string | undefined): string {
  const params = new URLSearchParams()
  if (opts.sandboxId) params.set('sandbox_id', opts.sandboxId)
  if (opts.limit !== undefined) params.set('limit', String(opts.limit))
  if (nextToken) params.set('next_token', nextToken)

  const query = params.toString()
  return query ? `/sandbox_snapshots?${query}` : '/sandbox_snapshots'
}

function metricsPath(sandboxId: string, opts: SandboxMetricsOpts): string {
  return withQuery(`/sandboxes/${sandboxId}/metrics`, {
    start: dateTimestampSeconds(opts.start),
    end: dateTimestampSeconds(opts.end),
  })
}

function dateTimestampSeconds(value: Date | undefined): number | undefined {
  return value === undefined ? undefined : Math.round(value.getTime() / 1000)
}

function fileUrlInfo(payload: Record<string, unknown>): FileUrlInfo {
  return {
    method: String(payload.method ?? ''),
    path: String(payload.path ?? ''),
    url: String(payload.url ?? ''),
    expiresAt: typeof payload.expires_at === 'string' ? payload.expires_at : typeof payload.expiresAt === 'string' ? payload.expiresAt : undefined,
    raw: payload,
  }
}

function compactRecord(payload: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(payload).filter(([, value]) => value !== undefined))
}

function sessionOperationRequestTimeout(config: ConnectionConfig, opts: ConnectionOpts): number {
  if (opts.requestTimeoutMs !== undefined) return opts.requestTimeoutMs
  return Math.max(config.requestTimeoutMs, SESSION_OPERATION_REQUEST_TIMEOUT_MS)
}

function sandboxInfo(payload: Record<string, unknown>): SandboxInfo {
  return {
    sandboxId: String(payload.id ?? payload.sandbox_id ?? ''),
    templateId: typeof payload.template_id === 'string' ? payload.template_id : templateSlug(payload.template),
    name: typeof payload.name === 'string' ? payload.name : undefined,
    state: typeof payload.state === 'string' ? payload.state : undefined,
    lifecycle: sandboxLifecycleInfo(payload.lifecycle),
    volumeMounts: volumeMountsInfo(payload.volume_mounts ?? payload.volumeMounts),
    metadata: recordOfStrings(payload.metadata),
    startedAt: typeof payload.started_at === 'string'
      ? payload.started_at
      : typeof payload.created_at === 'string' ? payload.created_at : undefined,
    endAt: typeof payload.end_at === 'string'
      ? payload.end_at
      : typeof payload.deadline_at === 'string' ? payload.deadline_at : undefined,
  }
}

function lifecyclePayload(lifecycle?: SandboxLifecycle): Record<string, unknown> | undefined {
  if (lifecycle === undefined) return undefined
  const onTimeout = lifecycle.onTimeout ?? 'kill'
  const autoResume = lifecycle.autoResume ?? false
  if (autoResume && onTimeout !== 'pause') {
    throw new SandboxError("lifecycle.autoResume can only be true when lifecycle.onTimeout is 'pause'")
  }
  return { on_timeout: onTimeout, auto_resume: autoResume }
}

function volumeMountsPayload(volumeMounts: SandboxCreateOpts['volumeMounts']): Array<{ name: string; path: string }> | undefined {
  if (volumeMounts === undefined) return undefined
  return Object.entries(volumeMounts).map(([path, volume]) => ({
    path,
    name: typeof volume === 'string' ? volume : volume.name,
  }))
}

function volumeMountsInfo(value: unknown): Array<{ name: string; path: string }> | undefined {
  if (!Array.isArray(value)) return undefined

  return value
    .map((item) => {
      const entry = record(item)
      return { name: String(entry.name ?? ''), path: String(entry.path ?? '') }
    })
    .filter((entry) => entry.name !== '' && entry.path !== '')
}

function sandboxLifecycleInfo(value: unknown): SandboxInfoLifecycle | undefined {
  const lifecycle = record(value)
  const onTimeout = stringValue(lifecycle.on_timeout ?? lifecycle.onTimeout)
  const autoResume = booleanValue(lifecycle.auto_resume ?? lifecycle.autoResume)
  if (onTimeout === undefined && autoResume === undefined) return undefined
  return {
    onTimeout: onTimeout ?? 'kill',
    autoResume: autoResume ?? false,
  }
}

function metricsList(value: unknown): SandboxMetrics[] {
  if (Array.isArray(value)) return value.map((item) => metricsInfo(record(item)))
  return [metricsInfo(record(value))]
}

function metricsInfo(value: Record<string, unknown>): SandboxMetrics {
  return {
    sandboxId: stringValue(value.sandbox_id ?? value.sandboxId),
    state: stringValue(value.state),
    node: stringValue(value.node),
    backend: stringValue(value.backend),
    cpuCount: numberValue(value.cpu_count ?? value.cpuCount),
    memoryMb: numberValue(value.memory_mb ?? value.memoryMb),
    raw: value,
  }
}

function snapshotPayload(opts: CreateSnapshotOpts): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  putIfPresent(payload, 'name', opts.name)
  putIfPresent(payload, 'metadata', opts.metadata)
  putIfPresent(payload, 'expires_at', opts.expiresAt)
  putIfPresent(payload, 'quiesce_mode', opts.quiesceMode)
  return payload
}

function snapshotInfo(value: Record<string, unknown>): SnapshotInfo {
  const id = value.snapshot_id ?? value.snapshotId ?? value.checkpoint_id ?? value.checkpointId ?? value.id
  if (id === undefined) throw new SandboxError('snapshot response did not include id')
  return {
    snapshotId: String(id),
    sandboxId: stringValue(value.sandbox_id ?? value.sandboxId),
    name: stringValue(value.name),
    status: stringValue(value.status),
    sizeBytes: numberValue(value.size_bytes ?? value.sizeBytes),
    createdAt: stringValue(value.created_at ?? value.createdAt),
    expiresAt: stringValue(value.expires_at ?? value.expiresAt),
    raw: value,
  }
}

function stringValue(value: unknown): string | undefined {
  if (typeof value === 'string') return value
  if (typeof value === 'number') return String(value)
  return undefined
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' ? value : undefined
}

function booleanValue(value: unknown): boolean | undefined {
  if (typeof value === 'boolean') return value
  if (value === 'true' || value === '1') return true
  if (value === 'false' || value === '0') return false
  return undefined
}

function templateSlug(value: unknown): string | undefined {
  const template = record(value)
  return typeof template.slug === 'string' ? template.slug : undefined
}

function putIfPresent(target: Record<string, unknown>, key: string, value: unknown): void {
  if (value !== undefined && value !== null) target[key] = value
}

function networkUpdatePayload(network: SandboxNetworkUpdate | undefined): Record<string, unknown> {
  if (network === undefined) return {}
  if (typeof network !== 'object' || network === null) unsupported('network callable rules')
  if (network.rules !== undefined) unsupported('network rules')
  if (network.maskRequestHost !== undefined) unsupported('network request host masking')

  return compactRecord({
    allow_out: network.allowOut,
    deny_out: network.denyOut,
    allow_internet_access: network.allowInternetAccess,
    allow_package_registry_access: network.allowPackageRegistryAccess,
    allow_public_traffic: network.allowPublicTraffic,
    egress_profile: network.egressProfile,
    egress_profiles: network.egressProfiles,
    network_class: network.networkClass,
  })
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

function routeTokenFromDataPlaneUrl(value: string, dataPlaneDomain: string): string | undefined {
  const host = hostOnly(value)
  const suffix = `.sandbox.${dataPlaneDomain}`
  if (!host.endsWith(suffix)) return undefined
  const token = host.slice(0, -suffix.length)
  return token || undefined
}
