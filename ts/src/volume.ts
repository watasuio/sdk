import { Blob } from 'node:buffer'

import { ConnectionConfig, type ConnectionOpts } from './connectionConfig.js'
import { NotFoundError, SandboxError } from './errors.js'
import { base64DecodeBytes, base64DecodeText, base64Encode } from './processSocket.js'
import { ControlClient, withQuery } from './transport.js'

export type VolumeFileType = 'file' | 'directory' | 'symlink' | string
export type VolumeReadFormat = 'text' | 'bytes' | 'blob' | 'stream'
export type VolumeWriteData = string | Uint8Array | ArrayBuffer | Blob

/** Control-plane metadata for a persistent Watasu volume. */
export interface VolumeInfo {
  volumeId: string
  id: string
  name: string
  state?: string
  token?: string
  sizeMb?: number
  sizeBytes?: number
  node?: string
  metadata: Record<string, string>
  createdAt?: string
  updatedAt?: string
  raw: Record<string, unknown>
}

/** File or directory metadata returned by volume content operations. */
export interface VolumeEntryStat {
  path: string
  name: string
  type: VolumeFileType
  size?: number
  mode?: number
  uid?: number
  gid?: number
  atime?: unknown
  mtime?: unknown
  ctime?: unknown
  raw: Record<string, unknown>
}

export interface VolumeApiParams extends ConnectionOpts {
  team?: string
}

export interface VolumeConnectionConfig extends ConnectionOpts {}

export interface VolumeListOpts extends ConnectionOpts {
  team?: string
}

export interface VolumeListFilesOpts extends ConnectionOpts {
  depth?: number
}

export interface VolumeReadFileOpts extends ConnectionOpts {
  format?: VolumeReadFormat
}

export interface VolumeWriteFileOpts extends ConnectionOpts {
  uid?: number
  gid?: number
  mode?: number | string
  force?: boolean
}

export interface VolumeMetadataOpts extends ConnectionOpts {
  uid?: number
  gid?: number
  mode?: number | string
}

/** Persistent volume that can be mounted into sandboxes and edited while detached. */
export class Volume {
  readonly volumeId: string
  readonly id: string
  readonly name: string
  readonly token?: string

  private readonly config: ConnectionConfig
  private readonly control: ControlClient

  constructor(opts: {
    volumeId: string
    name?: string
    token?: string
    connectionConfig: ConnectionConfig
    control?: ControlClient
  }) {
    this.volumeId = String(opts.volumeId)
    this.id = this.volumeId
    this.name = opts.name ?? this.volumeId
    this.token = opts.token
    this.config = opts.connectionConfig
    this.control = opts.control ?? new ControlClient(this.config)
  }

  /** Create a persistent volume and return a connected SDK object. */
  static async create(name: string, opts: VolumeApiParams = {}): Promise<Volume> {
    const config = new ConnectionConfig(opts)
    const control = new ControlClient(config)
    const payload = await control.post('/volumes', {
      json: compactRecord({ name, team: opts.team }),
      requestTimeoutMs: opts.requestTimeoutMs,
      signal: opts.signal,
    })
    return volumeFromPayload(payload, config, control)
  }

  /** Connect to an existing volume by id or name. */
  static async connect(volumeId: string, opts: VolumeConnectionConfig = {}): Promise<Volume> {
    const config = new ConnectionConfig(opts)
    const control = new ControlClient(config)
    const payload = await control.get(`/volumes/${encodeURIComponent(volumeId)}`, {
      requestTimeoutMs: opts.requestTimeoutMs,
      signal: opts.signal,
    })
    return volumeFromPayload(payload, config, control)
  }

  /** Fetch metadata for an existing volume by id or name. */
  static async getInfo(volumeId: string, opts: VolumeConnectionConfig = {}): Promise<VolumeInfo> {
    const config = new ConnectionConfig(opts)
    const control = new ControlClient(config)
    const payload = await control.get(`/volumes/${encodeURIComponent(volumeId)}`, {
      requestTimeoutMs: opts.requestTimeoutMs,
      signal: opts.signal,
    })
    return volumeInfo(record(payload.volume ?? payload))
  }

  /** List volumes visible to the configured API key. */
  static async list(opts: VolumeListOpts = {}): Promise<VolumeInfo[]> {
    const config = new ConnectionConfig(opts)
    const control = new ControlClient(config)
    const path = withQuery('/volumes', { team: opts.team })
    const payload = await control.get(path, {
      requestTimeoutMs: opts.requestTimeoutMs,
      signal: opts.signal,
    })
    const volumes = Array.isArray(payload.volumes) ? payload.volumes : []
    return volumes.map((item) => volumeInfo(record(item)))
  }

  /** Destroy a volume by id or name. Returns false when it does not exist. */
  static async destroy(volumeId: string, opts: VolumeConnectionConfig = {}): Promise<boolean> {
    const config = new ConnectionConfig(opts)
    const control = new ControlClient(config)
    try {
      await control.delete(`/volumes/${encodeURIComponent(volumeId)}`, {
        requestTimeoutMs: opts.requestTimeoutMs,
        signal: opts.signal,
      })
      return true
    } catch (error) {
      if (error instanceof NotFoundError) return false
      throw error
    }
  }

  /** Fetch this volume's latest metadata. */
  async getInfo(): Promise<VolumeInfo>
  /** Fetch metadata for a path inside this volume. */
  async getInfo(path: string, opts?: ConnectionOpts): Promise<VolumeEntryStat>
  async getInfo(path?: string, opts: ConnectionOpts = {}): Promise<VolumeInfo | VolumeEntryStat> {
    if (path === undefined) {
      return Volume.getInfo(this.volumeId, this.configOptions(opts))
    }

    const payload = await this.control.get(withQuery(`/volumes/${this.volumeId}/path`, { path }), {
      requestTimeoutMs: opts.requestTimeoutMs,
      signal: opts.signal,
    })
    return volumeEntry(record(payload.file ?? payload))
  }

  /** List files and directories under `path`. */
  async list(path = '/', opts: VolumeListFilesOpts = {}): Promise<VolumeEntryStat[]> {
    const payload = await this.control.get(withQuery(`/volumes/${this.volumeId}/directories`, {
      path,
      depth: opts.depth,
    }), {
      requestTimeoutMs: opts.requestTimeoutMs,
      signal: opts.signal,
    })
    const entries = Array.isArray(payload.entries) ? payload.entries : []
    return entries.map((item) => volumeEntry(record(item)))
  }

  /** Create a directory inside the detached volume. */
  async makeDir(path: string, opts: VolumeWriteFileOpts = {}): Promise<VolumeEntryStat> {
    const payload = await this.control.post(`/volumes/${this.volumeId}/directories`, {
      json: compactRecord({ path, ...metadataPayload(opts) }),
      requestTimeoutMs: opts.requestTimeoutMs,
      signal: opts.signal,
    })
    return volumeEntry(record(payload.file ?? payload))
  }

  /** Return whether a path exists inside the detached volume. */
  async exists(path: string, opts: ConnectionOpts = {}): Promise<boolean> {
    try {
      await this.getInfo(path, opts)
      return true
    } catch (error) {
      if (error instanceof NotFoundError) return false
      throw error
    }
  }

  /** Update ownership or mode metadata for a path. */
  async updateMetadata(path: string, opts: VolumeMetadataOpts = {}): Promise<VolumeEntryStat> {
    const payload = await this.control.patch(`/volumes/${this.volumeId}/path`, {
      json: compactRecord({ path, ...metadataPayload(opts) }),
      requestTimeoutMs: opts.requestTimeoutMs,
      signal: opts.signal,
    })
    return volumeEntry(record(payload.file ?? payload))
  }

  /** Read a file from the detached volume. */
  async readFile(path: string, opts: VolumeReadFileOpts & { format: 'bytes' }): Promise<Uint8Array>
  async readFile(path: string, opts: VolumeReadFileOpts & { format: 'blob' }): Promise<Blob>
  async readFile(path: string, opts: VolumeReadFileOpts & { format: 'stream' }): Promise<ReadableStream<Uint8Array>>
  async readFile(path: string, opts?: VolumeReadFileOpts): Promise<string>
  async readFile(path: string, opts: VolumeReadFileOpts = {}): Promise<string | Uint8Array | Blob | ReadableStream<Uint8Array>> {
    const payload = await this.control.get(withQuery(`/volumes/${this.volumeId}/files`, { path }), {
      requestTimeoutMs: opts.requestTimeoutMs,
      signal: opts.signal,
    })
    const file = record(payload.file ?? payload)
    const content = file.content_b64 ?? file.contentBase64 ?? file.content ?? ''

    switch (opts.format ?? 'text') {
      case 'bytes':
        return base64DecodeBytes(content)
      case 'blob':
        return new Blob([base64DecodeBytes(content)])
      case 'stream':
        return new Blob([base64DecodeBytes(content)]).stream() as unknown as ReadableStream<Uint8Array>
      case 'text':
        return file.content_b64 || file.contentBase64 ? base64DecodeText(content) : String(content)
      default:
        throw new SandboxError(`unsupported volume read format: ${String(opts.format)}`)
    }
  }

  /** Write a file into the detached volume. */
  async writeFile(path: string, data: VolumeWriteData, opts: VolumeWriteFileOpts = {}): Promise<VolumeEntryStat> {
    const bytes = await bytesFromWriteData(data)
    const payload = await this.control.put(`/volumes/${this.volumeId}/files`, {
      json: compactRecord({
        path,
        content_b64: base64Encode(bytes),
        ...metadataPayload(opts),
        force: opts.force,
      }),
      requestTimeoutMs: opts.requestTimeoutMs,
      signal: opts.signal,
    })
    return volumeEntry(record(payload.file ?? payload))
  }

  /** Remove a file or directory from the detached volume. */
  async remove(path: string, opts: ConnectionOpts = {}): Promise<boolean> {
    await this.control.delete(withQuery(`/volumes/${this.volumeId}/path`, { path }), {
      requestTimeoutMs: opts.requestTimeoutMs,
      signal: opts.signal,
    })
    return true
  }

  /** Destroy this volume. Returns false when it no longer exists. */
  async destroy(opts: ConnectionOpts = {}): Promise<boolean> {
    return Volume.destroy(this.volumeId, this.configOptions(opts))
  }

  private configOptions(opts: ConnectionOpts = {}): ConnectionOpts {
    return {
      apiKey: this.config.apiKey,
      apiUrl: this.config.apiUrl,
      dataPlaneDomain: this.config.dataPlaneDomain,
      requestTimeoutMs: this.config.requestTimeoutMs,
      headers: this.config.headers,
      apiHeaders: this.config.apiHeaders,
      debug: this.config.debug,
      signal: this.config.signal,
      proxy: this.config.proxy,
      ...opts,
    }
  }
}

function volumeFromPayload(payload: Record<string, unknown>, config: ConnectionConfig, control: ControlClient): Volume {
  const info = volumeInfo(record(payload.volume ?? payload))
  return new Volume({
    volumeId: info.volumeId,
    name: info.name,
    token: info.token,
    connectionConfig: config,
    control,
  })
}

function volumeInfo(payload: Record<string, unknown>): VolumeInfo {
  const id = payload.volume_id ?? payload.volumeId ?? payload.id
  if (id === undefined) throw new SandboxError('volume response did not include id')

  return {
    volumeId: String(id),
    id: String(id),
    name: String(payload.name ?? id),
    state: stringValue(payload.state),
    token: stringValue(payload.token),
    sizeMb: numberValue(payload.size_mb ?? payload.sizeMb),
    sizeBytes: numberValue(payload.size_bytes ?? payload.sizeBytes),
    node: stringValue(payload.node ?? payload.node_name ?? payload.nodeName),
    metadata: recordOfStrings(payload.metadata),
    createdAt: stringValue(payload.created_at ?? payload.createdAt),
    updatedAt: stringValue(payload.updated_at ?? payload.updatedAt),
    raw: payload,
  }
}

function volumeEntry(payload: Record<string, unknown>): VolumeEntryStat {
  return {
    path: String(payload.path ?? ''),
    name: String(payload.name ?? ''),
    type: String(payload.type ?? 'file'),
    size: numberValue(payload.size ?? payload.bytes),
    mode: numberValue(payload.mode),
    uid: numberValue(payload.uid),
    gid: numberValue(payload.gid),
    atime: payload.atime,
    mtime: payload.mtime,
    ctime: payload.ctime,
    raw: payload,
  }
}

async function bytesFromWriteData(data: VolumeWriteData): Promise<Uint8Array> {
  if (typeof data === 'string') return new TextEncoder().encode(data)
  if (data instanceof Uint8Array) return data
  if (data instanceof ArrayBuffer) return new Uint8Array(data)
  if (data instanceof Blob) return new Uint8Array(await data.arrayBuffer())
  throw new SandboxError('unsupported volume write data')
}

function metadataPayload(opts: VolumeWriteFileOpts | VolumeMetadataOpts): Record<string, unknown> {
  return compactRecord({
    uid: opts.uid,
    gid: opts.gid,
    mode: opts.mode,
  })
}

function compactRecord(payload: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(payload).filter(([, value]) => value !== undefined))
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {}
}

function recordOfStrings(value: unknown): Record<string, string> {
  if (!value || typeof value !== 'object') return {}
  return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([key, item]) => [key, String(item)]))
}

function stringValue(value: unknown): string | undefined {
  if (typeof value === 'string') return value
  if (typeof value === 'number') return String(value)
  return undefined
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' ? value : undefined
}
