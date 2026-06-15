import { DataPlaneClient, withQuery } from './transport.js'
import { FileNotFoundError } from './errors.js'
import { ProcessFrame, ProcessSocket, base64Encode } from './processSocket.js'

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

export interface WriteEntry {
  path: string
  data: string | Uint8Array
}

export interface FilesystemEvent {
  type: 'create' | 'write' | 'modify' | 'remove' | 'delete' | 'rename' | string
  path: string
  entry?: EntryInfo
  raw: Record<string, unknown>
}

export interface WatchOpts {
  recursive?: boolean
  includeEntry?: boolean
  requestTimeoutMs?: number
  onExit?: (error?: Error) => void | Promise<void>
}

/** Live filesystem watcher. Call `stop()` to close the local watch stream. */
export class WatchHandle {
  private readonly done: Promise<void>

  constructor(
    private readonly socket: ProcessSocket,
    events: AsyncIterable<ProcessFrame>,
    onEvent: (event: FilesystemEvent) => void | Promise<void>,
    onExit?: (error?: Error) => void | Promise<void>
  ) {
    this.done = this.pump(events, onEvent, onExit)
  }

  /** Stop watching the directory. */
  stop(): void {
    this.socket.close()
  }

  /** Alias for `stop`. */
  close(): void {
    this.stop()
  }

  /** Resolves when the watcher stream exits. */
  wait(): Promise<void> {
    return this.done
  }

  private async pump(
    events: AsyncIterable<ProcessFrame>,
    onEvent: (event: FilesystemEvent) => void | Promise<void>,
    onExit?: (error?: Error) => void | Promise<void>
  ): Promise<void> {
    let error: Error | undefined
    try {
      for await (const frame of events) {
        if (frame.type !== 'events' || !Array.isArray(frame.events)) continue
        for (const item of frame.events) {
          await onEvent(filesystemEvent(item))
        }
      }
    } catch (caught) {
      error = caught instanceof Error ? caught : new Error(String(caught))
      throw error
    } finally {
      await onExit?.(error)
    }
  }
}

/** Lazy filesystem watcher. Add listeners, then call `start()`. */
export class FilesystemWatcher {
  private handle?: WatchHandle
  private listeners: Array<(event: FilesystemEvent) => void | Promise<void>> = []

  constructor(
    private readonly dataPlane: DataPlaneClient,
    private readonly path: string,
    private readonly opts: WatchOpts = {}
  ) {}

  async start(opts: WatchOpts = {}): Promise<void> {
    if (this.handle) return
    const nextOpts = { ...this.opts, ...opts }
    const socket = await new ProcessSocket(
      this.dataPlane.baseUrl,
      this.dataPlane.token,
      withQuery('/runtime/v1/files/watch', { path: this.path, recursive: nextOpts.recursive ?? false, include_entry: nextOpts.includeEntry }),
      nextOpts.requestTimeoutMs
    ).connect()
    this.handle = new WatchHandle(socket, socket, async (event) => {
      for (const listener of this.listeners) await listener(event)
    }, nextOpts.onExit)
  }

  async stop(): Promise<void> {
    this.handle?.stop()
  }

  addEventListener(listener: (event: FilesystemEvent) => void | Promise<void>): () => boolean {
    this.listeners.push(listener)
    return () => {
      const index = this.listeners.indexOf(listener)
      if (index === -1) return false
      this.listeners.splice(index, 1)
      return true
    }
  }

  wait(): Promise<void> {
    return this.handle?.wait() ?? Promise.resolve()
  }
}

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

  /** Read a file as raw bytes. */
  async readBytes(path: string, opts: { requestTimeoutMs?: number; gzip?: boolean } = {}): Promise<Uint8Array> {
    return this.read(path, { ...opts, format: 'bytes' }) as Promise<Uint8Array>
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

  /** Write raw bytes to a file. */
  async writeBytes(path: string, data: Uint8Array, opts: { requestTimeoutMs?: number; gzip?: boolean; metadata?: Record<string, string> } = {}): Promise<WriteInfo> {
    return this.write(path, data, opts)
  }

  /** Write several files in one runtime API call. */
  async writeFiles(files: WriteEntry[], opts: { requestTimeoutMs?: number } = {}): Promise<WriteInfo[]> {
    if (files.length === 0) return []
    const payload = await this.dataPlane.postJson('/runtime/v1/files/write_files', {
      ...opts,
      json: {
        files: files.map((file) => ({
          path: file.path,
          data_base64: base64Encode(typeof file.data === 'string' ? new TextEncoder().encode(file.data) : file.data),
        })),
      },
    })
    const written = Array.isArray(payload.files) ? payload.files : []
    return written.map(entryInfo)
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

  /** Start watching a directory for filesystem events. */
  watchDir(path: string): FilesystemWatcher
  watchDir(
    path: string,
    onEvent: (event: FilesystemEvent) => void | Promise<void>,
    opts?: WatchOpts
  ): Promise<FilesystemWatcher>
  watchDir(
    path: string,
    onEvent?: (event: FilesystemEvent) => void | Promise<void>,
    opts: WatchOpts = {}
  ): FilesystemWatcher | Promise<FilesystemWatcher> {
    const watcher = new FilesystemWatcher(this.dataPlane, path, opts)
    if (!onEvent) return watcher
    watcher.addEventListener(onEvent)
    return watcher.start().then(() => watcher)
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

function filesystemEvent(value: unknown): FilesystemEvent {
  const item = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  return {
    type: normalizeEventType(String(item.type ?? 'modify')),
    path: String(item.path ?? ''),
    entry: item.file && typeof item.file === 'object' ? entryInfo(item.file) : undefined,
    raw: item,
  }
}

function normalizeEventType(value: string): FilesystemEvent['type'] {
  if (value === 'delete') return 'remove'
  if (value === 'modify') return 'write'
  return value
}
