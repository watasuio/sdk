import { DataPlaneClient } from './transport.js'

export interface GitCommandResult {
  path?: string
  url?: string
  ref?: string
  branch?: string
  remote?: string
  name?: string
  value?: string
  branches?: string[]
  currentBranch?: string
  stdout: string
  stderr: string
  command?: Record<string, unknown>
  raw: Record<string, unknown>
}

export interface GitAuthOpts {
  username?: string
  password?: string
  envs?: Record<string, string>
  user?: string
  cwd?: string
  timeoutMs?: number
  requestTimeoutMs?: number
  signal?: AbortSignal
}

export interface GitCloneOpts extends GitAuthOpts {
  path?: string
  branch?: string
  depth?: number
  recursive?: boolean
  dangerouslyStoreCredentials?: boolean
}

export interface GitRequestOpts extends GitAuthOpts {}

export interface GitInitOpts extends GitRequestOpts {
  bare?: boolean
  initialBranch?: string
}

export interface GitPullOpts extends GitAuthOpts {
  branch?: string
  remote?: string
}

export interface GitPushOpts extends GitAuthOpts {
  branch?: string
  remote?: string
  setUpstream?: boolean
}

export interface GitCredentialOpts extends GitRequestOpts {
  host?: string
  protocol?: string
}

export type GitDangerouslyAuthenticateOpts = GitCredentialOpts

export interface GitConfigureUserOpts extends GitRequestOpts {
  scope?: GitConfigScope
  path?: string
}

export interface GitBranchOpts extends GitRequestOpts {
  force?: boolean
}

export type GitDeleteBranchOpts = GitBranchOpts

export interface GitAddOpts extends GitRequestOpts {
  files?: string[]
  all?: boolean
}

export interface GitCommitOpts extends GitRequestOpts {
  authorName?: string
  authorEmail?: string
  allowEmpty?: boolean
}

export type GitResetMode = 'soft' | 'mixed' | 'hard' | 'merge' | 'keep'

export interface GitResetOpts extends GitRequestOpts {
  mode?: GitResetMode
  target?: string
  paths?: string[]
}

export interface GitRestoreOpts extends GitRequestOpts {
  paths: string[]
  staged?: boolean
  worktree?: boolean
  source?: string
}

export interface GitRemoteAddOpts extends GitRequestOpts {
  fetch?: boolean
  overwrite?: boolean
}

export interface GitConfigOpts extends GitRequestOpts {
  scope?: GitConfigScope
  path?: string
}

export type GitConfigScope = 'global' | 'local' | 'system'

export interface GitBranches {
  path?: string
  branches: string[]
  currentBranch?: string
  result: GitCommandResult
}

export interface GitFileStatus {
  name: string
  status: string
  indexStatus: string
  workingTreeStatus: string
  staged: boolean
  renamedFrom?: string
}

export interface GitStatus {
  currentBranch?: string
  upstream?: string
  ahead: number
  behind: number
  detached: boolean
  fileStatus: GitFileStatus[]
  isClean: boolean
  hasChanges: boolean
  hasStaged: boolean
  hasUntracked: boolean
  hasConflicts: boolean
  totalCount: number
  stagedCount: number
  unstagedCount: number
  untrackedCount: number
  conflictCount: number
  result: GitCommandResult
}

/** Git helper backed by sandbox data-plane routes. */
export class Git {
  constructor(private readonly dataPlane: DataPlaneClient) {}

  /** Clone a repository into the sandbox. */
  async clone(url: string, opts: GitCloneOpts = {}): Promise<GitCommandResult> {
    return this.run('/runtime/v1/git/clone', {
      url,
      ...gitOpts(opts),
      ...pick(opts, ['path', 'branch', 'depth', 'recursive', 'username', 'password']),
      dangerously_store_credentials: opts.dangerouslyStoreCredentials,
    }, opts)
  }

  /** Store Git credentials in the sandbox credential helper. */
  async dangerouslyAuthenticate(opts: GitCredentialOpts & { username: string; password: string }): Promise<GitCommandResult> {
    return this.run('/runtime/v1/git/dangerously_authenticate', {
      ...gitOpts(opts),
      username: opts.username,
      password: opts.password,
      host: opts.host,
      protocol: opts.protocol,
    }, opts)
  }

  /** Configure Git author identity globally or for one repository. */
  async configureUser(name: string, email: string, opts: GitConfigureUserOpts = {}): Promise<GitCommandResult> {
    return this.run('/runtime/v1/git/configure_user', {
      ...gitOpts(opts),
      name,
      email,
      scope: opts.scope,
      path: opts.path,
    }, opts)
  }

  /** Initialize a Git repository. */
  async init(path: string, opts: GitInitOpts = {}): Promise<GitCommandResult> {
    return this.run('/runtime/v1/git/init', {
      path,
      bare: opts.bare,
      initial_branch: opts.initialBranch,
      ...gitOpts(opts),
    }, opts)
  }

  /** Return parsed repository status for `path`. */
  async status(path: string, opts: GitRequestOpts = {}): Promise<GitStatus> {
    const result = await this.run('/runtime/v1/git/status', { path, ...gitOpts(opts) }, opts)
    return parseGitStatus(result)
  }

  /** Return branches and the current branch for `path`. */
  async branches(path: string, opts: GitRequestOpts = {}): Promise<GitBranches> {
    const result = await this.run('/runtime/v1/git/branches', { path, ...gitOpts(opts) }, opts)
    return {
      path: result.path,
      branches: Array.isArray(result.raw.branches) ? result.raw.branches.map(String) : result.branches ?? [],
      currentBranch: stringValue(result.raw.current_branch) ?? result.currentBranch,
      result,
    }
  }

  /** Create and check out a new branch. */
  async createBranch(path: string, branch: string, opts: GitRequestOpts = {}): Promise<GitCommandResult> {
    return this.run('/runtime/v1/git/create_branch', { path, branch, ...gitOpts(opts) }, opts)
  }

  /** Delete a branch. */
  async deleteBranch(path: string, branch: string, opts: GitBranchOpts = {}): Promise<GitCommandResult> {
    return this.run('/runtime/v1/git/delete_branch', { path, branch, force: opts.force, ...gitOpts(opts) }, opts)
  }

  /** Stage files. Defaults to all files. */
  async add(path: string, opts: GitAddOpts = {}): Promise<GitCommandResult> {
    return this.run('/runtime/v1/git/add', { path, files: opts.files, all: opts.all, ...gitOpts(opts) }, opts)
  }

  /** Commit staged files. */
  async commit(path: string, message: string, opts: GitCommitOpts = {}): Promise<GitCommandResult> {
    return this.run('/runtime/v1/git/commit', {
      path,
      message,
      author_name: opts.authorName,
      author_email: opts.authorEmail,
      allow_empty: opts.allowEmpty,
      ...gitOpts(opts),
    }, opts)
  }

  /** Reset the current HEAD to a specified state. */
  async reset(path: string, opts: GitResetOpts = {}): Promise<GitCommandResult> {
    return this.run('/runtime/v1/git/reset', {
      path,
      mode: opts.mode,
      target: opts.target,
      paths: opts.paths,
      ...gitOpts(opts),
    }, opts)
  }

  /** Restore working tree files or unstage changes. */
  async restore(path: string, opts: GitRestoreOpts): Promise<GitCommandResult> {
    return this.run('/runtime/v1/git/restore', {
      path,
      paths: opts.paths,
      staged: opts.staged,
      worktree: opts.worktree,
      source: opts.source,
      ...gitOpts(opts),
    }, opts)
  }

  /** Pull the current branch with a fast-forward-only merge. */
  async pull(path: string, opts: GitPullOpts = {}): Promise<GitCommandResult> {
    return this.run('/runtime/v1/git/pull', { path, ...gitOpts(opts), ...pick(opts, ['remote', 'branch', 'username', 'password']) }, opts)
  }

  /** Push the current branch or a selected branch. */
  async push(path: string, opts: GitPushOpts = {}): Promise<GitCommandResult> {
    return this.run('/runtime/v1/git/push', {
      path,
      ...gitOpts(opts),
      ...pick(opts, ['remote', 'branch', 'username', 'password']),
      set_upstream: opts.setUpstream ?? true,
    }, opts)
  }

  /** Check out an arbitrary ref in a repository. */
  async checkout(path: string, ref: string, opts: GitRequestOpts = {}): Promise<GitCommandResult> {
    return this.run('/runtime/v1/git/checkout', { path, ref, ...gitOpts(opts) }, opts)
  }

  /** Check out an existing branch in a repository. */
  async checkoutBranch(path: string, branch: string, opts: GitRequestOpts = {}): Promise<GitCommandResult> {
    return this.checkout(path, branch, opts)
  }

  /** Add a remote. */
  async remoteAdd(path: string, name: string, url: string, opts: GitRemoteAddOpts = {}): Promise<GitCommandResult> {
    return this.run('/runtime/v1/git/remote_add', {
      path,
      name,
      url,
      fetch: opts.fetch,
      overwrite: opts.overwrite,
      ...gitOpts(opts),
    }, opts)
  }

  /** Return a remote URL, or undefined when the remote does not exist. */
  async remoteGet(path: string, name: string, opts: GitRequestOpts = {}): Promise<string | undefined> {
    const result = await this.run('/runtime/v1/git/remote_get', {
      path,
      name,
      ...gitOpts(opts),
    }, opts)
    return result.value ?? result.url
  }

  /** Set a Git config value. */
  async setConfig(key: string, value: string, opts: GitConfigOpts = {}): Promise<GitCommandResult> {
    return this.run('/runtime/v1/git/set_config', {
      key,
      value,
      scope: opts.scope,
      path: opts.path,
      ...gitOpts(opts),
    }, opts)
  }

  /** Read a Git config value. */
  async getConfig(key: string, opts: GitConfigOpts = {}): Promise<string> {
    const result = await this.run('/runtime/v1/git/get_config', {
      key,
      scope: opts.scope,
      path: opts.path,
      ...gitOpts(opts),
    }, opts)
    return String(result.value ?? '')
  }

  private async run(path: string, json: Record<string, unknown>, opts: { requestTimeoutMs?: number; signal?: AbortSignal }): Promise<GitCommandResult> {
    const payload = await this.dataPlane.postJson(path, {
      json: compact(json),
      requestTimeoutMs: opts.requestTimeoutMs,
      signal: opts.signal,
    })
    return gitResult(payload.git ?? payload)
  }
}

function gitOpts(opts: GitRequestOpts): Record<string, unknown> {
  return {
    env_vars: opts.envs,
    user: opts.user,
    cwd: opts.cwd,
    timeout_seconds: opts.timeoutMs === undefined ? undefined : Math.ceil(opts.timeoutMs / 1000),
  }
}

function pick(source: object, keys: string[]): Record<string, unknown> {
  const record = source as Record<string, unknown>
  return Object.fromEntries(keys.map((key) => [key, record[key]]))
}

function compact(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined))
}

function gitResult(value: unknown): GitCommandResult {
  const item = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  return {
    path: stringValue(item.path),
    url: stringValue(item.url),
    ref: stringValue(item.ref),
    branch: stringValue(item.branch),
    remote: stringValue(item.remote),
    name: stringValue(item.name),
    value: stringValue(item.value),
    branches: Array.isArray(item.branches) ? item.branches.map(String) : undefined,
    currentBranch: stringValue(item.current_branch),
    stdout: String(item.stdout ?? ''),
    stderr: String(item.stderr ?? ''),
    command: item.command && typeof item.command === 'object' ? item.command as Record<string, unknown> : undefined,
    raw: item,
  }
}

function parseGitStatus(result: GitCommandResult): GitStatus {
  const fileStatus: GitFileStatus[] = []
  let currentBranch: string | undefined
  let upstream: string | undefined
  let ahead = 0
  let behind = 0
  let detached = false

  for (const line of result.stdout.split(/\r?\n/).filter(Boolean)) {
    if (line.startsWith('## ')) {
      const branchLine = line.slice(3)
      detached = branchLine.includes('HEAD') && branchLine.includes('no branch')
      const [branchPart, trackingPart] = branchLine.split('...')
      currentBranch = branchPart?.replace(/\s+\[.*\]$/, '') || undefined
      if (trackingPart) {
        const match = trackingPart.match(/^([^\s[]+)(?:\s+\[(.*)\])?/)
        upstream = match?.[1]
        const details = match?.[2] ?? ''
        ahead = numberFrom(details, /ahead\s+(\d+)/)
        behind = numberFrom(details, /behind\s+(\d+)/)
      }
      continue
    }

    const indexStatus = line[0] ?? ' '
    const workingTreeStatus = line[1] ?? ' '
    const path = line.slice(3)
    const [name, renamedFrom] = path.includes(' -> ') ? path.split(' -> ').reverse() : [path, undefined]
    const status = statusName(indexStatus, workingTreeStatus)
    fileStatus.push({ name, status, indexStatus, workingTreeStatus, staged: indexStatus !== ' ' && indexStatus !== '?', renamedFrom })
  }

  const stagedCount = fileStatus.filter((item) => item.staged).length
  const untrackedCount = fileStatus.filter((item) => item.status === 'untracked').length
  const conflictCount = fileStatus.filter((item) => item.status === 'conflict').length
  const totalCount = fileStatus.length

  return {
    currentBranch,
    upstream,
    ahead,
    behind,
    detached,
    fileStatus,
    isClean: totalCount === 0,
    hasChanges: totalCount > 0,
    hasStaged: stagedCount > 0,
    hasUntracked: untrackedCount > 0,
    hasConflicts: conflictCount > 0,
    totalCount,
    stagedCount,
    unstagedCount: totalCount - stagedCount,
    untrackedCount,
    conflictCount,
    result,
  }
}

function numberFrom(value: string, pattern: RegExp): number {
  const match = value.match(pattern)
  return match ? Number(match[1]) : 0
}

function statusName(indexStatus: string, workingTreeStatus: string): string {
  if (indexStatus === '?' && workingTreeStatus === '?') return 'untracked'
  if (indexStatus === 'U' || workingTreeStatus === 'U' || indexStatus === 'A' && workingTreeStatus === 'A') return 'conflict'
  if (indexStatus === 'D' || workingTreeStatus === 'D') return 'deleted'
  if (indexStatus === 'R') return 'renamed'
  if (indexStatus === 'A') return 'added'
  if (indexStatus === 'M' || workingTreeStatus === 'M') return 'modified'
  return 'changed'
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}
