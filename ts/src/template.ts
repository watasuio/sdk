import fs from 'node:fs'
import path from 'node:path'
import { ConnectionConfig, ConnectionOpts } from './connectionConfig.js'
import { InvalidArgumentError, NotFoundError, SandboxError, TemplateError } from './errors.js'
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

export type LogEntryLevel = 'debug' | 'info' | 'warn' | 'error'
export type Logger = (logEntry: LogEntry) => void

export class LogEntry {
  constructor(
    readonly timestamp: Date = new Date(),
    readonly level: LogEntryLevel = 'info',
    readonly message = ''
  ) {}

  toString(): string {
    return `[${this.timestamp.toISOString()}] ${this.level}: ${this.message}`
  }
}

export class LogEntryStart extends LogEntry {
  constructor(timestamp: Date = new Date(), message = 'Build started') {
    super(timestamp, 'info', message)
  }
}

export class LogEntryEnd extends LogEntry {
  constructor(timestamp: Date = new Date(), message = 'Build finished') {
    super(timestamp, 'info', message)
  }
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
  fileContextPath?: string
  fileIgnorePatterns?: string[]
}

export interface CopyOptions {
  forceUpload?: true
  user?: string
  mode?: number
  resolveSymlinks?: boolean
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
export type CopyItem = {
  src: string | string[]
  dest: string
  forceUpload?: true
  user?: string
  mode?: number
  resolveSymlinks?: boolean
}
export type TemplateBuilder = TemplateBase
export type TemplateFinal = TemplateBase
export type TemplateFromImage = TemplateBase
export type ReadyCommand = string | ReadyCmd

export function defaultBuildLogger(options: { minLevel?: LogEntryLevel } = {}): Logger {
  const order: Record<LogEntryLevel, number> = { debug: 10, info: 20, warn: 30, error: 40 }
  const minLevel = options.minLevel ?? 'info'
  return (entry: LogEntry) => {
    if (order[entry.level] < order[minLevel]) return
    if (entry.level === 'error') console.error(entry.toString())
    else if (entry.level === 'warn') console.warn(entry.toString())
    else console.log(entry.toString())
  }
}

interface TemplateFileSpec {
  path: string
  content_b64: string
  source_path?: string
  mode?: number
  user?: string
}

interface BuildSpec {
  base?: string
  from_template?: string
  from_image?: string
  from_image_registry?: Record<string, unknown>
  requested_from_image?: string
  packages?: Record<string, string[]>
  files?: TemplateFileSpec[]
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

/** Ready-check command wrapper accepted by template builders. */
export class ReadyCmd {
  constructor(private readonly cmd: string) {}

  /** Return the shell command used as the ready check. */
  getCmd(): string {
    return this.cmd
  }
}

/** Return a ready check that waits for a TCP port to listen. */
export function waitForPort(port: number): ReadyCmd {
  return new ReadyCmd(`ss -tuln | grep :${port}`)
}

/** Return a ready check that waits for a URL to return an HTTP status code. */
export function waitForURL(url: string, statusCode = 200): ReadyCmd {
  return new ReadyCmd(`curl -s -o /dev/null -w "%{http_code}" ${url} | grep -q "${statusCode}"`)
}

/** Return a ready check that waits for a process name. */
export function waitForProcess(processName: string): ReadyCmd {
  return new ReadyCmd(`pgrep ${processName} > /dev/null`)
}

/** Return a ready check that waits for a file to exist. */
export function waitForFile(filename: string): ReadyCmd {
  return new ReadyCmd(`[ -f ${filename} ]`)
}

/** Return a ready check that waits for a fixed duration in milliseconds. */
export function waitForTimeout(timeout: number): ReadyCmd {
  const seconds = Math.max(1, Math.floor(timeout / 1000))
  return new ReadyCmd(`sleep ${seconds}`)
}

/** Chainable template builder for Watasu package-spec template builds. */
export class TemplateBase {
  private base: string | undefined
  private fromImageReference: string | undefined
  private fromImageRegistry: Record<string, unknown> | undefined
  private packages: Record<string, string[]> = {}
  private files: TemplateFileSpec[] = []
  private setup: string[] = []
  private env: Record<string, string> = {}
  private currentWorkdir: string | undefined
  private currentUser: string | undefined
  private startCmd: string | undefined
  private readyCmd: string | undefined
  private force = false
  private readonly fileContextPath: string
  private readonly fileIgnorePatterns: string[]

  constructor(options: TemplateOptions = {}) {
    this.fileContextPath = path.resolve(options.fileContextPath ?? process.cwd())
    this.fileIgnorePatterns = options.fileIgnorePatterns ?? []
  }

  static async build(template: TemplateClass, name: string, options?: Omit<BuildOptions, 'alias'>): Promise<BuildInfo>
  static async build(template: TemplateClass, options: BuildOptions): Promise<BuildInfo>
  static async build(
    template: TemplateClass,
    nameOrOptions: string | BuildOptions,
    options: Omit<BuildOptions, 'alias'> = {}
  ): Promise<BuildInfo> {
    const { name, buildOptions } = normalizeBuildArguments(nameOrOptions, options)
    buildOptions.onBuildLogs?.(new LogEntryStart())
    const data = await TemplateBase.buildInBackground(template, name, buildOptions)
    await waitForBuildFinish(data, buildOptions)
    buildOptions.onBuildLogs?.(new LogEntryEnd())
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
    return (template as TemplateBase).toJSON()
  }

  static toDockerfile(template: TemplateClass): string {
    return (template as TemplateBase).toDockerfile()
  }

  /** Request a Debian public base image. The Watasu API fails closed until OCI image import is enabled. */
  fromDebianImage(_variant = 'stable'): TemplateBuilder {
    return this.fromImage(`debian:${_variant}`)
  }

  /** Request an Ubuntu public base image. The Watasu API fails closed until OCI image import is enabled. */
  fromUbuntuImage(_variant = 'latest'): TemplateBuilder {
    return this.fromImage(`ubuntu:${_variant}`)
  }

  /** Request a Python public base image. The Watasu API fails closed until OCI image import is enabled. */
  fromPythonImage(_version = '3'): TemplateBuilder {
    return this.fromImage(`python:${_version}`)
  }

  /** Request a Node.js public base image. The Watasu API fails closed until OCI image import is enabled. */
  fromNodeImage(_variant = 'lts'): TemplateBuilder {
    return this.fromImage(`node:${_variant}`)
  }

  /** Request a Bun public base image. The Watasu API fails closed until OCI image import is enabled. */
  fromBunImage(_variant = 'latest'): TemplateBuilder {
    return this.fromImage(`oven/bun:${_variant}`)
  }

  /** Start from the Watasu platform base template. */
  fromBaseImage(): TemplateBuilder {
    return this.fromTemplate('base')
  }

  /** Request a public container image base. The Watasu API fails closed until OCI image import is enabled. */
  fromImage(baseImage: string, credentials?: { username: string; password: string }): TemplateBuilder {
    if (credentials && (!credentials.username || !credentials.password)) {
      throw new InvalidArgumentError('Both username and password are required when providing registry credentials')
    }
    this.fromImageReference = baseImage
    this.base = undefined
    this.fromImageRegistry = credentials
      ? {
          type: 'registry',
          username: credentials.username,
          password: credentials.password,
        }
      : undefined
    return this
  }

  /** Request an AWS registry image base. The Watasu API fails closed until registry image import is enabled. */
  fromAWSRegistry(image: string, credentials: { accessKeyId: string; secretAccessKey: string; region: string }): TemplateBuilder {
    this.fromImageReference = image
    this.base = undefined
    this.fromImageRegistry = {
      type: 'aws',
      aws_access_key_id: credentials.accessKeyId,
      aws_secret_access_key: credentials.secretAccessKey,
      aws_region: credentials.region,
    }
    return this
  }

  /** Request a GCP registry image base. The Watasu API fails closed until registry image import is enabled. */
  fromGCPRegistry(image: string, credentials: { serviceAccountJSON: object | string }): TemplateBuilder {
    this.fromImageReference = image
    this.base = undefined
    this.fromImageRegistry = {
      type: 'gcp',
      service_account_json: credentials.serviceAccountJSON,
    }
    return this
  }

  /** Start from a ready Watasu template slug, tag, or version id. */
  fromTemplate(template: string): TemplateBuilder {
    this.base = template
    this.fromImageReference = undefined
    this.fromImageRegistry = undefined
    return this
  }

  fromDockerfile(dockerfileContentOrPath: string): TemplateBuilder {
    const candidate = path.isAbsolute(dockerfileContentOrPath)
      ? dockerfileContentOrPath
      : path.resolve(this.fileContextPath, dockerfileContentOrPath)
    const content = fs.existsSync(candidate) && fs.statSync(candidate).isFile()
      ? fs.readFileSync(candidate, 'utf8')
      : dockerfileContentOrPath
    parseDockerfileIntoTemplate(content, this)
    return this
  }

  copy(src: string | string[], dest: string, options: CopyOptions = {}): TemplateBuilder {
    const sources = Array.isArray(src) ? src : [src]
    for (const source of sources) this.addCopySource(source, dest, options, sources.length > 1)
    return this
  }

  copyItems(items: CopyItem[]): TemplateBuilder {
    for (const item of items) {
      this.copy(item.src, item.dest, {
        forceUpload: item.forceUpload,
        user: item.user,
        mode: item.mode,
        resolveSymlinks: item.resolveSymlinks,
      })
    }
    return this
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

  setStartCmd(startCommand: string, readyCommand: ReadyCommand): TemplateFinal {
    this.startCmd = startCommand
    this.readyCmd = readyCommandText(readyCommand)
    return this
  }

  setReadyCmd(readyCommand: ReadyCommand): TemplateFinal {
    this.readyCmd = readyCommandText(readyCommand)
    return this
  }

  betaDevContainerPrebuild(devcontainerDirectory: string): TemplateBuilder {
    this.requireDevContainerTemplate('betaDevContainerPrebuild')
    return this.runCmd(`devcontainer build --workspace-folder ${devcontainerDirectory}`, { user: 'root' })
  }

  betaSetDevContainerStart(devcontainerDirectory: string): TemplateFinal {
    this.requireDevContainerTemplate('betaSetDevContainerStart')
    return this.setStartCmd(
      `sudo devcontainer up --workspace-folder ${devcontainerDirectory} && sudo /prepare-exec.sh ${devcontainerDirectory} | sudo tee /devcontainer.sh > /dev/null && sudo chmod +x /devcontainer.sh && sudo touch /devcontainer.up`,
      waitForFile('/devcontainer.up')
    )
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
    if (this.base) spec.from_template = this.base
    if (this.fromImageReference) spec.from_image = this.fromImageReference
    if (this.fromImageRegistry) spec.from_image_registry = this.fromImageRegistry
    if (Object.keys(this.packages).length > 0) spec.packages = this.packages
    if (this.files.length > 0) spec.files = this.files
    if (this.setup.length > 0) spec.setup = this.setup
    if (Object.keys(this.env).length > 0) spec.env = this.env
    if (this.startCmd) spec.start_cmd = this.startCmd
    if (this.readyCmd) spec.ready_cmd = this.readyCmd
    return spec
  }

  private async toJSON(_computeHashes = true): Promise<string> {
    return JSON.stringify(this.serialize())
  }

  private serialize(_steps?: unknown): BuildSpec {
    return this.toBuildSpec()
  }

  private addPackages(manager: string, packages: string[]) {
    this.packages[manager] = [...(this.packages[manager] ?? []), ...packages]
  }

  private requireDevContainerTemplate(method: string) {
    if (this.base !== 'devcontainer') {
      throw new SandboxError(`${method} can only be used with the devcontainer template`)
    }
  }

  private addCopySource(source: string, dest: string, options: CopyOptions, multipleSources: boolean) {
    const sourcePath = this.resolveContextPath(source)
    const stat = fs.statSync(sourcePath)
    if (stat.isDirectory()) {
      for (const filePath of walkFiles(sourcePath, options.resolveSymlinks ?? true)) {
        const relativePath = toPosixPath(path.relative(sourcePath, filePath))
        if (this.ignored(relativePath) || this.ignored(toPosixPath(path.relative(this.fileContextPath, filePath)))) continue
        this.addFileSpec(
          filePath,
          posixJoin(dest, relativePath),
          toPosixPath(path.relative(this.fileContextPath, filePath)),
          options
        )
      }
      return
    }

    if (!stat.isFile()) {
      throw new InvalidArgumentError(`copy source is not a file or directory: ${source}`)
    }

    const destPath = multipleSources || dest.endsWith('/') ? posixJoin(dest, path.basename(source)) : dest
    this.addFileSpec(sourcePath, destPath, toPosixPath(path.relative(this.fileContextPath, sourcePath)), options)
  }

  private addFileSpec(filePath: string, destPath: string, sourcePath: string, options: CopyOptions) {
    const file: TemplateFileSpec = {
      path: normalizeSandboxPath(destPath),
      source_path: sourcePath,
      content_b64: fs.readFileSync(filePath).toString('base64'),
    }
    if (options.mode !== undefined) file.mode = options.mode
    if (options.user !== undefined) file.user = options.user
    this.files.push(file)
  }

  private resolveContextPath(source: string): string {
    if (path.isAbsolute(source)) {
      throw new InvalidArgumentError('copy source must be relative to the template file context')
    }
    const resolved = path.resolve(this.fileContextPath, source)
    const relative = path.relative(this.fileContextPath, resolved)
    if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
      throw new InvalidArgumentError('copy source must stay inside the template file context')
    }
    return resolved
  }

  private ignored(relativePath: string): boolean {
    return this.fileIgnorePatterns.some((pattern) => matchesIgnorePattern(relativePath, pattern))
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
    const lines = [`FROM ${this.fromImageReference ?? this.base ?? 'base'}`]
    for (const packageName of this.packages.apt ?? []) lines.push(`RUN apt-get update && apt-get install -y ${packageName}`)
    for (const packageName of this.packages.pip ?? []) lines.push(`RUN python3 -m pip install ${packageName}`)
    for (const packageName of this.packages.npm ?? []) lines.push(`RUN npm install -g ${packageName}`)
    for (const file of this.files) lines.push(`COPY ${file.source_path ?? file.path} ${file.path}`)
    for (const command of this.setup) lines.push(`RUN ${command}`)
    return `${lines.join('\n')}\n`
  }
}

function readyCommandText(command: ReadyCommand): string {
  return command instanceof ReadyCmd ? command.getCmd() : command
}

function walkFiles(root: string, resolveSymlinks: boolean): string[] {
  const entries = fs.readdirSync(root, { withFileTypes: true })
  const files: string[] = []
  for (const entry of entries) {
    const entryPath = path.join(root, entry.name)
    const stat = resolveSymlinks ? fs.statSync(entryPath) : fs.lstatSync(entryPath)
    if (stat.isDirectory()) files.push(...walkFiles(entryPath, resolveSymlinks))
    if (stat.isFile()) files.push(entryPath)
  }
  return files.sort()
}

function normalizeSandboxPath(value: string): string {
  return toPosixPath(value).replace(/\/+/g, '/')
}

function posixJoin(base: string, relativePath: string): string {
  const normalizedBase = normalizeSandboxPath(base)
  return normalizeSandboxPath(path.posix.join(normalizedBase, relativePath))
}

function toPosixPath(value: string): string {
  return value.split(path.sep).join('/')
}

function matchesIgnorePattern(relativePath: string, pattern: string): boolean {
  if (!pattern) return false
  const normalizedPattern = toPosixPath(pattern)
  if (normalizedPattern.endsWith('/')) return relativePath.startsWith(normalizedPattern)
  if (!normalizedPattern.includes('*')) return relativePath === normalizedPattern || relativePath.startsWith(`${normalizedPattern}/`)
  const escaped = normalizedPattern.split('*').map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('.*')
  return new RegExp(`^${escaped}$`).test(relativePath)
}

function parseDockerfileIntoTemplate(dockerfileContentOrPath: string, template: TemplateBase) {
  for (const instruction of dockerfileInstructions(dockerfileContentOrPath)) {
    const keyword = instruction.keyword.toUpperCase()
    const value = instruction.value
    if (keyword === 'FROM') {
      template.fromImage(value.split(/\s+/)[0] || 'base')
    } else if (keyword === 'RUN') {
      template.runCmd(value)
    } else if (keyword === 'WORKDIR') {
      template.setWorkdir(value)
    } else if (keyword === 'USER') {
      template.setUser(value)
    } else if (keyword === 'ENV') {
      template.setEnvs(parseEnvInstruction(value))
    } else if (keyword === 'COPY' || keyword === 'ADD') {
      const args = shellWords(value).filter((word) => !word.startsWith('--'))
      if (args.length < 2) throw new InvalidArgumentError(`${keyword} requires source and destination`)
      const dest = args[args.length - 1]
      template.copy(args.slice(0, -1), dest)
    } else if (keyword === 'CMD' || keyword === 'ENTRYPOINT') {
      template.setStartCmd(value, waitForTimeout(20_000))
    }
  }
}

function dockerfileInstructions(content: string): Array<{ keyword: string; value: string }> {
  const logicalLines: string[] = []
  let current = ''
  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) continue
    if (line.endsWith('\\')) {
      current += `${line.slice(0, -1)} `
      continue
    }
    logicalLines.push(`${current}${line}`)
    current = ''
  }
  if (current.trim()) logicalLines.push(current.trim())

  return logicalLines.flatMap((line) => {
    const match = line.match(/^([A-Za-z]+)\s+(.*)$/)
    return match ? [{ keyword: match[1], value: match[2].trim() }] : []
  })
}

function parseEnvInstruction(value: string): Record<string, string> {
  const words = shellWords(value)
  if (words.length === 2 && !words[0].includes('=')) return { [words[0]]: words[1] }
  const env: Record<string, string> = {}
  for (const word of words) {
    const index = word.indexOf('=')
    if (index > 0) env[word.slice(0, index)] = word.slice(index + 1)
  }
  return env
}

function shellWords(value: string): string[] {
  const words: string[] = []
  let word = ''
  let quote: '"' | "'" | undefined
  let escaping = false
  for (const char of value) {
    if (escaping) {
      word += char
      escaping = false
    } else if (char === '\\') {
      escaping = true
    } else if (quote) {
      if (char === quote) quote = undefined
      else word += char
    } else if (char === '"' || char === "'") {
      quote = char
    } else if (/\s/.test(char)) {
      if (word) {
        words.push(word)
        word = ''
      }
    } else {
      word += char
    }
  }
  if (word) words.push(word)
  return words
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
    if (status === 'error') throw new TemplateError(buildStatus.reason?.message ?? 'Template build failed')
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
  return new LogEntry(
    timestamp ? new Date(timestamp) : new Date(),
    logEntryLevel(stringValue(payload.level)),
    stringValue(payload.message) ?? ''
  )
}

function logEntryLevel(value: string | undefined): LogEntryLevel {
  if (value === 'debug' || value === 'info' || value === 'warn' || value === 'error') return value
  return 'info'
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
