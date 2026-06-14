import { DataPlaneClient, withQuery } from './transport.js'
import { FileNotFoundError, unsupported } from './errors.js'

export enum FileType {
  /** Regular file. */
  FILE = 'file',
  /** Directory. */
  DIR = 'dir',
  /** Symbolic link. */
  SYMLINK = 'symlink',
}

/** Metadata for one sandbox filesystem entry. */
export interface EntryInfo {
  name: string
  type: FileType | string
  path: string
  size?: number
  mode?: number
  uid?: number
  gid?: number
  mtime?: number
  metadata?: Record<string, string>
}

export type WriteInfo = EntryInfo

/** Filesystem helper for a sandbox data-plane session. */
export class Filesystem {
  constructor(private readonly dataPlane: DataPlaneClient) {}

  /** Read a file as UTF-8 text, bytes, or a one-chunk async byte stream. */
  async read(
    path: string,
    opts: { format?: 'text' | 'bytes' | 'stream'; requestTimeoutMs?: number; gzip?: boolean } = {}
  ): Promise<string | Uint8Array | AsyncIterable<Uint8Array>> {
    const bytes = await this.dataPlane.getBytes(withQuery('/runtime/v1/files', { path, gzip: opts.gzip }), opts)
    if (opts.format === 'bytes') return bytes
    if (opts.format === 'stream') return (async function* () { yield bytes })()
    return new TextDecoder().decode(bytes)
  }

  /** Write UTF-8 text or bytes to a file. */
  async write(
    path: string,
    data: string | Uint8Array,
    opts: { requestTimeoutMs?: number; gzip?: boolean; metadata?: Record<string, string> } = {}
  ): Promise<WriteInfo> {
    const body = typeof data === 'string' ? new TextEncoder().encode(data) : data
    const payload = await this.dataPlane.putJson(withQuery('/runtime/v1/files', { path, gzip: opts.gzip }), body, opts)
    return entryInfo(payload.file ?? payload)
  }

  /** List directory entries below `path`. */
  async list(path: string, opts: { requestTimeoutMs?: number; depth?: number } = {}): Promise<EntryInfo[]> {
    const payload = await this.dataPlane.getJson(withQuery('/runtime/v1/directories', { path, depth: opts.depth }), opts)
    const entries = Array.isArray(payload.entries) ? payload.entries : []
    return entries.map(entryInfo)
  }

  /** Return whether a file or directory exists at `path`. */
  async exists(path: string, opts: { requestTimeoutMs?: number } = {}): Promise<boolean> {
    try {
      await this.getInfo(path, opts)
      return true
    } catch (error) {
      if (error instanceof FileNotFoundError) return false
      throw error
    }
  }

  /** Return stat metadata for `path`. */
  async getInfo(path: string, opts: { requestTimeoutMs?: number } = {}): Promise<EntryInfo> {
    const payload = await this.dataPlane.getJson(withQuery('/runtime/v1/files/stat', { path }), opts)
    return entryInfo(payload.file ?? payload.entry ?? payload)
  }

  /** Remove a file at `path`. */
  async remove(path: string, opts: { requestTimeoutMs?: number } = {}): Promise<void> {
    await this.dataPlane.deleteJson(withQuery('/runtime/v1/files', { path }), opts)
  }

  /** Move or rename a file. */
  async rename(oldPath: string, newPath: string, opts: { requestTimeoutMs?: number } = {}): Promise<EntryInfo> {
    const payload = await this.dataPlane.postJson('/runtime/v1/files/move', {
      ...opts,
      json: { from_path: oldPath, to_path: newPath },
    })
    return entryInfo(payload.file ?? payload)
  }

  /** Create a directory. */
  async makeDir(path: string, opts: { requestTimeoutMs?: number } = {}): Promise<boolean> {
    await this.dataPlane.postJson(withQuery('/runtime/v1/directories', { path }), opts)
    return true
  }

  watchDir(): never {
    unsupported('sandbox.files.watchDir')
  }
}

function entryInfo(value: unknown): EntryInfo {
  const item = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  return {
    name: String(item.name ?? ''),
    type: String(item.type ?? FileType.FILE),
    path: String(item.path ?? ''),
    size: numberValue(item.bytes ?? item.size),
    mode: numberValue(item.mode),
    uid: numberValue(item.uid),
    gid: numberValue(item.gid),
    mtime: numberValue(item.mtime),
    metadata: recordOfStrings(item.metadata),
  }
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' ? value : undefined
}

function recordOfStrings(value: unknown): Record<string, string> | undefined {
  if (!value || typeof value !== 'object') return undefined
  return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([key, item]) => [key, String(item)]))
}
