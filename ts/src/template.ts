import { ConnectionConfig, ConnectionOpts } from './connectionConfig.js'
import { InvalidArgumentError, NotFoundError, SandboxError, unsupported } from './errors.js'
import { ControlClient, withQuery } from './transport.js'

export type TemplateBuildStatus = 'building' | 'waiting' | 'ready' | 'error'

export interface BuildInfo {
  /** First template alias. Prefer `name` for new code. */
  alias: string
  /** Template name passed to the build call. */
  name: string
  /** Tags assigned to this build. */
  tags: string[]
  /** Template identifier. */
  templateId: string
  /** Build identifier. */
  buildId: string
}

export interface LogEntry {
  timestamp?: Date
  level: string
  message: string
}

export interface BuildStatusReason {
  message: string
  step?: string
  logEntries: LogEntry[]
}

export interface TemplateBuildStatusResponse {
  buildID: string
  templateID: string
  status: TemplateBuildStatus
  logEntries: LogEntry[]
  logs: string[]
  reason?: BuildStatusReason
}

export interface TemplateTagInfo {
  buildId: string
  tags: string[]
}

export interface TemplateTag {
  tag: string
  buildId: string
  createdAt: Date
}

export interface TemplateOptions {
  fileContextPath?: unknown
  fileIgnorePatterns?: string[]
}

export interface BasicBuildOptions {
  alias?: string
  tags?: string[]
  cpuCount?: number
  memoryMB?: number
  skipCache?: boolean
  onBuildLogs?: (entry: LogEntry) => void
  team?: string
}

export type BuildOptions = ConnectionOpts & BasicBuildOptions
export type GetBuildStatusOptions = ConnectionOpts & { logsOffset?: number }
export type TemplateClass = TemplateBase
export type CopyItem = Record<string, unknown>
export type TemplateBuilder = TemplateBase
export type TemplateFinal = TemplateBase
export type TemplateFromImage = TemplateBase

interface BuildSpec {
  base?: string
  packages?: Record<string, string[]>
  setup?: string[]
  env?: Record<string, string>
  start_cmd?: string
  ready_cmd?: string
}

interface BuildPayload {
  name: string
  tags?: string[]
  cpu_count: number
  memory_mb: number
  skip_cache: boolean
  build_spec: BuildSpec
  team?: string
}

/** Chainable template builder for Watasu package-spec template builds. */
export class TemplateBase {
  private base: string | undefined
  private packages: Record<string, string[]> = {}
  private setup: string[] = []
  private env: Record<string, string> = {}
  private currentWorkdir: string | undefined
  private currentUser: string | undefined
  private startCmd: string | undefined
  private readyCmd: string | undefined
  private force = false

  constructor(_options: TemplateOptions = {}) {}

  static async build(template: TemplateClass, name: string, options?: Omit<BuildOptions, 'alias'>): Promise<BuildInfo>
  static async build(template: TemplateClass, options: BuildOptions): Promise<BuildInfo>
  static async build(
    template: TemplateClass,
    nameOrOptions: string | BuildOptions,
    options: Omit<BuildOptions, 'alias'> = {}
  ): Promise<BuildInfo> {
    const { name, buildOptions } = normalizeBuildArguments(nameOrOptions, options)
    buildOptions.onBuildLogs?.({ timestamp: new Date(), level: 'info', message: 'Build started' })
    const data = await TemplateBase.buildInBackground(template, name, buildOptions)
    await waitForBuildFinish(data, buildOptions)
    buildOptions.onBuildLogs?.({ timestamp: new Date(), level: 'info', message: 'Build finished' })
    return data
  }

  static async buildInBackground(template: TemplateClass, name: string, options?: Omit<BuildOptions, 'alias'>): Promise<BuildInfo>
  static async buildInBackground(template: TemplateClass, options: BuildOptions): Promise<BuildInfo>
  static async buildInBackground(
    template: TemplateClass,
    nameOrOptions: string | BuildOptions,
    options: Omit<BuildOptions, 'alias'> = {}
  ): Promise<BuildInfo> {
    const { name, buildOptions } = normalizeBuildArguments(nameOrOptions, options)
    const config = new ConnectionConfig(buildOptions)
    const control = new ControlClient(config)
    const payload: BuildPayload = {
      name,
      tags: buildOptions.tags,
      cpu_count: buildOptions.cpuCount ?? 2,
      memory_mb: buildOptions.memoryMB ?? 1024,
      skip_cache: buildOptions.skipCache ?? (template as TemplateBase).force,
      build_spec: (template as TemplateBase).toBuildSpec(),
    }
    if (buildOptions.team !== undefined) payload.team = buildOptions.team

    const response = await control.post('/templates', { json: payload })
    return buildInfo(record(response.template_build ?? response))
  }

  static async getBuildStatus(
    data: Pick<BuildInfo, 'templateId' | 'buildId'>,
    options: GetBuildStatusOptions = {}
  ): Promise<TemplateBuildStatusResponse> {
    const config = new ConnectionConfig(options)
    const control = new ControlClient(config)
    const payload = await control.get(
      withQuery(`/templates/${encodeURIComponent(data.templateId)}/builds/${encodeURIComponent(data.buildId)}/status`, {
        logs_offset: options.logsOffset,
      })
    )
    return templateBuildStatus(record(payload))
  }

  static async exists(name: string, options?: ConnectionOpts): Promise<boolean> {
    return TemplateBase.aliasExists(name, options)
  }

  static async aliasExists(alias: string, options: ConnectionOpts = {}): Promise<boolean> {
    const config = new ConnectionConfig(options)
    const control = new ControlClient(config)
    try {
      await control.get(`/templates/aliases/${encodeURIComponent(alias)}`)
      return true
    } catch (error) {
      if (error instanceof NotFoundError) return false
      throw error
    }
  }

  static async assignTags(
    targetName: string,
    tags: string | string[],
    options: ConnectionOpts = {}
  ): Promise<TemplateTagInfo> {
    const config = new ConnectionConfig(options)
    const control = new ControlClient(config)
    const response = await control.post('/templates/tags', {
      json: { target: targetName, tags: Array.isArray(tags) ? tags : [tags] },
    })
    return {
      buildId: stringValue(response.build_id) ?? '',
      tags: arrayOfStrings(response.tags),
    }
  }

  static async removeTags(
    name: string,
    tags: string | string[],
    options: ConnectionOpts = {}
  ): Promise<void> {
    const config = new ConnectionConfig(options)
    const control = new ControlClient(config)
    await control.delete('/templates/tags', {
      json: { name, tags: Array.isArray(tags) ? tags : [tags] },
    })
  }

  static async getTags(templateId: string, options: ConnectionOpts = {}): Promise<TemplateTag[]> {
    const config = new ConnectionConfig(options)
    const control = new ControlClient(config)
    const response = await control.get(`/templates/${encodeURIComponent(templateId)}/tags`)
    const tags = Array.isArray(response) ? response : []
    return tags.map((item) => templateTag(record(item)))
  }

  static async toJSON(template: TemplateClass): Promise<string> {
    return JSON.stringify((template as TemplateBase).toBuildSpec())
  }

  static toDockerfile(template: TemplateClass): string {
    return (template as TemplateBase).toDockerfile()
  }

  fromDebianImage(_variant = 'stable'): TemplateBuilder {
    this.base = 'base'
    return this
  }

  fromUbuntuImage(_variant = 'latest'): TemplateBuilder {
    this.base = 'base'
    return this
  }

  fromPythonImage(_version = '3'): TemplateBuilder {
    this.base = this.base ?? 'base'
    return this
  }

  fromNodeImage(_variant = 'lts'): TemplateBuilder {
    this.base = this.base ?? 'base'
    return this
  }

  fromBunImage(_variant = 'latest'): TemplateBuilder {
    this.base = this.base ?? 'base'
    return this
  }

  fromBaseImage(): TemplateBuilder {
    this.base = 'base'
    return this
  }

  fromImage(_baseImage: string, _credentials?: { username: string; password: string }): TemplateBuilder {
    this.base = this.base ?? 'base'
    return this
  }

  fromAWSRegistry(_image: string, _credentials: { accessKeyId: string; secretAccessKey: string; region: string }): TemplateBuilder {
    unsupported('Template.fromAWSRegistry')
  }

  fromGCPRegistry(_image: string, _credentials: { serviceAccountJSON: object | string }): TemplateBuilder {
    unsupported('Template.fromGCPRegistry')
  }

  fromTemplate(template: string): TemplateBuilder {
    this.base = template
    return this
  }

  fromDockerfile(_dockerfileContentOrPath: string): TemplateBuilder {
    unsupported('Template.fromDockerfile')
  }

  copy(_src: unknown, _dest: unknown, _options?: unknown): TemplateBuilder {
    unsupported('Template.copy')
  }

  copyItems(_items: CopyItem[]): TemplateBuilder {
    unsupported('Template.copyItems')
  }

  remove(path: string | string[], options: { force?: boolean; recursive?: boolean; user?: string } = {}): TemplateBuilder {
    const paths = Array.isArray(path) ? path : [path]
    return this.runCmd(`rm ${options.recursive ? '-r ' : ''}${options.force ? '-f ' : ''}${paths.join(' ')}`, {
      user: options.user,
    })
  }

  rename(src: string, dest: string, options: { force?: boolean; user?: string } = {}): TemplateBuilder {
    return this.runCmd(`mv ${src} ${dest}${options.force ? ' -f' : ''}`, { user: options.user })
  }

  makeDir(path: string | string[], options: { mode?: number; user?: string } = {}): TemplateBuilder {
    const paths = Array.isArray(path) ? path : [path]
    const mode = options.mode === undefined ? '' : `-m ${options.mode.toString(8)} `
    return this.runCmd(`mkdir -p ${mode}${paths.join(' ')}`, { user: options.user })
  }

  makeSymlink(src: string, dest: string, options: { force?: boolean; user?: string } = {}): TemplateBuilder {
    return this.runCmd(`ln -s ${options.force ? '-f ' : ''}${src} ${dest}`, { user: options.user })
  }

  runCmd(command: string | string[], options: { user?: string } = {}): TemplateBuilder {
    const commandText = Array.isArray(command) ? command.join(' && ') : command
    this.setup.push(this.commandWithContext(commandText, options.user))
    return this
  }

  setWorkdir(workdir: string): TemplateBuilder {
    this.currentWorkdir = workdir
    return this
  }

  setUser(user: string): TemplateBuilder {
    this.currentUser = user
    return this
  }

  pipInstall(packages?: string | string[], options: { g?: boolean } = {}): TemplateBuilder {
    const packageList = packages === undefined ? [] : arrayOfStrings(packages)
    if (packageList.length > 0 && options.g !== false) {
      this.addPackages('pip', packageList)
    } else {
      const suffix = packageList.length > 0 ? packageList.join(' ') : '.'
      this.runCmd(`python3 -m pip install ${options.g === false ? '--user ' : ''}${suffix}`)
    }
    return this
  }

  npmInstall(packages?: string | string[], options: { g?: boolean; dev?: boolean } = {}): TemplateBuilder {
    const packageList = packages === undefined ? [] : arrayOfStrings(packages)
    if (packageList.length > 0 && options.g) {
      this.addPackages('npm', packageList)
    } else {
      this.runCmd(`npm install ${options.g ? '-g ' : ''}${options.dev ? '--save-dev ' : ''}${packageList.join(' ')}`.trim())
    }
    return this
  }

  bunInstall(packages?: string | string[], options: { g?: boolean; dev?: boolean } = {}): TemplateBuilder {
    const packageList = packages === undefined ? [] : arrayOfStrings(packages)
    this.runCmd(`bun install ${options.g ? '-g ' : ''}${options.dev ? '--dev ' : ''}${packageList.join(' ')}`.trim())
    return this
  }

  aptInstall(packages: string | string[], _options: { noInstallRecommends?: boolean; fixMissing?: boolean } = {}): TemplateBuilder {
    this.addPackages('apt', arrayOfStrings(packages))
    return this
  }

  addMcpServer(servers: string | string[]): TemplateBuilder {
    if (this.base !== 'mcp-gateway') {
      throw new SandboxError('MCP servers can only be added to mcp-gateway template')
    }
    return this.runCmd(`mcp-gateway pull ${arrayOfStrings(servers).join(' ')}`, { user: 'root' })
  }

  gitClone(url: string, path?: string, options: { branch?: string; depth?: number; user?: string } = {}): TemplateBuilder {
    const args = ['git clone']
    if (options.branch) args.push(`--branch ${options.branch}`, '--single-branch')
    if (options.depth) args.push(`--depth ${options.depth}`)
    args.push(url)
    if (path) args.push(path)
    return this.runCmd(args.join(' '), { user: options.user })
  }

  setStartCmd(startCommand: string, readyCommand: string): TemplateFinal {
    this.startCmd = startCommand
    this.readyCmd = readyCommand
    return this
  }

  setReadyCmd(readyCommand: string): TemplateFinal {
    this.readyCmd = readyCommand
    return this
  }

  setEnvs(envs: Record<string, string>): TemplateBuilder {
    Object.assign(this.env, envs)
    return this
  }

  skipCache(): TemplateBuilder {
    this.force = true
    return this
  }

  toBuildSpec(): BuildSpec {
    const spec: BuildSpec = {}
    if (this.base) spec.base = this.base
    if (Object.keys(this.packages).length > 0) spec.packages = this.packages
    if (this.setup.length > 0) spec.setup = this.setup
    if (Object.keys(this.env).length > 0) spec.env = this.env
    if (this.startCmd) spec.start_cmd = this.startCmd
    if (this.readyCmd) spec.ready_cmd = this.readyCmd
    return spec
  }

  private addPackages(manager: string, packages: string[]) {
    this.packages[manager] = [...(this.packages[manager] ?? []), ...packages]
  }

  private commandWithContext(command: string, user?: string): string {
    const workdir = this.currentWorkdir ? `cd ${shellQuote(this.currentWorkdir)} && ` : ''
    const commandText = `${workdir}${command}`
    const commandUser = user ?? this.currentUser
    return commandUser && commandUser !== 'root'
      ? `su -s /bin/bash -c ${shellQuote(commandText)} ${shellQuote(commandUser)}`
      : commandText
  }

  private toDockerfile(): string {
    const lines = ['FROM base']
    for (const packageName of this.packages.apt ?? []) lines.push(`RUN apt-get update && apt-get install -y ${packageName}`)
    for (const packageName of this.packages.pip ?? []) lines.push(`RUN python3 -m pip install ${packageName}`)
    for (const packageName of this.packages.npm ?? []) lines.push(`RUN npm install -g ${packageName}`)
    for (const command of this.setup) lines.push(`RUN ${command}`)
    return `${lines.join('\n')}\n`
  }
}

export interface TemplateFactory {
  (options?: TemplateOptions): TemplateFromImage
  build: typeof TemplateBase.build
  buildInBackground: typeof TemplateBase.buildInBackground
  getBuildStatus: typeof TemplateBase.getBuildStatus
  exists: typeof TemplateBase.exists
  aliasExists: typeof TemplateBase.aliasExists
  assignTags: typeof TemplateBase.assignTags
  removeTags: typeof TemplateBase.removeTags
  getTags: typeof TemplateBase.getTags
  toJSON: typeof TemplateBase.toJSON
  toDockerfile: typeof TemplateBase.toDockerfile
}

export const Template: TemplateFactory = Object.assign(
  (options?: TemplateOptions) => new TemplateBase(options),
  {
    build: TemplateBase.build,
    buildInBackground: TemplateBase.buildInBackground,
    getBuildStatus: TemplateBase.getBuildStatus,
    exists: TemplateBase.exists,
    aliasExists: TemplateBase.aliasExists,
    assignTags: TemplateBase.assignTags,
    removeTags: TemplateBase.removeTags,
    getTags: TemplateBase.getTags,
    toJSON: TemplateBase.toJSON,
    toDockerfile: TemplateBase.toDockerfile,
  }
)

async function waitForBuildFinish(data: BuildInfo, options: BuildOptions): Promise<void> {
  let logsOffset = 0
  let status: TemplateBuildStatus = 'building'
  while (status === 'building' || status === 'waiting') {
    const buildStatus = await TemplateBase.getBuildStatus(data, { ...options, logsOffset })
    logsOffset += buildStatus.logEntries.length
    buildStatus.logEntries.forEach((entry) => options.onBuildLogs?.(entry))
    status = buildStatus.status
    if (status === 'ready') return
    if (status === 'error') throw new SandboxError(buildStatus.reason?.message ?? 'Template build failed')
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
}

function normalizeBuildArguments(
  nameOrOptions: string | BuildOptions,
  options: Omit<BuildOptions, 'alias'>
): { name: string; buildOptions: BuildOptions } {
  if (typeof nameOrOptions === 'string') return { name: nameOrOptions, buildOptions: options }
  if (!nameOrOptions.alias) throw new InvalidArgumentError('name is required')
  return { name: nameOrOptions.alias, buildOptions: nameOrOptions }
}

function buildInfo(payload: Record<string, unknown>): BuildInfo {
  const templateId = stringValue(payload.template_id ?? payload.templateId)
  const buildId = stringValue(payload.build_id ?? payload.buildId)
  if (!templateId || !buildId) throw new SandboxError('template build response did not include identifiers')
  return {
    alias: stringValue(payload.alias) ?? stringValue(payload.name) ?? '',
    name: stringValue(payload.name) ?? stringValue(payload.alias) ?? '',
    tags: arrayOfStrings(payload.tags),
    templateId,
    buildId,
  }
}

function templateBuildStatus(payload: Record<string, unknown>): TemplateBuildStatusResponse {
  return {
    buildID: stringValue(payload.build_id ?? payload.buildID) ?? '',
    templateID: stringValue(payload.template_id ?? payload.templateID) ?? '',
    status: (stringValue(payload.status) ?? 'building') as TemplateBuildStatus,
    logEntries: Array.isArray(payload.log_entries)
      ? payload.log_entries.map((item) => logEntry(record(item)))
      : [],
    logs: arrayOfStrings(payload.logs),
    reason: payload.reason ? buildStatusReason(record(payload.reason)) : undefined,
  }
}

function buildStatusReason(payload: Record<string, unknown>): BuildStatusReason {
  return {
    message: stringValue(payload.message) ?? 'Template build failed',
    step: stringValue(payload.step),
    logEntries: Array.isArray(payload.log_entries)
      ? payload.log_entries.map((item) => logEntry(record(item)))
      : [],
  }
}

function logEntry(payload: Record<string, unknown>): LogEntry {
  const timestamp = stringValue(payload.timestamp)
  return {
    timestamp: timestamp ? new Date(timestamp) : undefined,
    level: stringValue(payload.level) ?? 'info',
    message: stringValue(payload.message) ?? '',
  }
}

function templateTag(payload: Record<string, unknown>): TemplateTag {
  return {
    tag: stringValue(payload.tag) ?? '',
    buildId: stringValue(payload.build_id ?? payload.buildId) ?? '',
    createdAt: new Date(stringValue(payload.created_at ?? payload.createdAt) ?? 0),
  }
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {}
}

function stringValue(value: unknown): string | undefined {
  if (typeof value === 'string' && value.length > 0) return value
  if (typeof value === 'number') return String(value)
  return undefined
}

function arrayOfStrings(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String)
  if (typeof value === 'string') return [value]
  return []
}

function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`
}
