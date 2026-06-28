export class SandboxError extends Error {
  constructor(message = 'Sandbox error') {
    super(message)
    this.name = 'SandboxError'
  }
}

export class AuthenticationError extends SandboxError {
  constructor(message = 'Authentication failed') {
    super(message)
    this.name = 'AuthenticationError'
  }
}

export class NotFoundError extends SandboxError {
  constructor(message = 'Not found') {
    super(message)
    this.name = 'NotFoundError'
  }
}

export class ConflictError extends SandboxError {
  constructor(message = 'Conflict') {
    super(message)
    this.name = 'ConflictError'
  }
}

export class TimeoutError extends SandboxError {
  constructor(message = 'Request timed out') {
    super(message)
    this.name = 'TimeoutError'
  }
}

export class InvalidArgumentError extends SandboxError {
  constructor(message = 'Invalid argument') {
    super(message)
    this.name = 'InvalidArgumentError'
  }
}

export class RateLimitError extends SandboxError {
  constructor(message = 'Rate limit exceeded') {
    super(message)
    this.name = 'RateLimitError'
  }
}

export class NotEnoughSpaceError extends SandboxError {
  constructor(message = 'Not enough space') {
    super(message)
    this.name = 'NotEnoughSpaceError'
  }
}

export class FileNotFoundError extends NotFoundError {
  constructor(message = 'File not found') {
    super(message)
    this.name = 'FileNotFoundError'
  }
}

export class SandboxNotFoundError extends NotFoundError {
  constructor(message = 'Sandbox not found') {
    super(message)
    this.name = 'SandboxNotFoundError'
  }
}

export class GitAuthError extends AuthenticationError {
  constructor(message = 'Git authentication failed') {
    super(message)
    this.name = 'GitAuthError'
  }
}

export class GitUpstreamError extends SandboxError {
  constructor(message = 'Git upstream tracking is missing') {
    super(message)
    this.name = 'GitUpstreamError'
  }
}

export class TemplateError extends SandboxError {
  constructor(message = 'Template error') {
    super(message)
    this.name = 'TemplateError'
  }
}

export class BuildError extends Error {
  constructor(message = 'Build error') {
    super(message)
    this.name = 'BuildError'
  }
}

export class FileUploadError extends BuildError {
  constructor(message = 'File upload failed') {
    super(message)
    this.name = 'FileUploadError'
  }
}

export class VolumeError extends Error {
  constructor(message = 'Volume error') {
    super(message)
    this.name = 'VolumeError'
  }
}

export class ApiError extends SandboxError {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export class SandboxOverloadedError extends ApiError {
  constructor(message = 'Sandbox is overloaded') {
    super(message, 503, 'sandbox_overloaded')
    this.name = 'SandboxOverloadedError'
  }
}

export function errorFromResponse(status: number, payload: unknown): Error {
  const body = asRecord(payload)
  const reason = asRecord(body.reason)
  const code = stringValue(body.error)
  const message =
    stringValue(body.message) ||
    listMessage(body.errors) ||
    stringValue(reason.message) ||
    listMessage(reason.errors) ||
    stringValue(body.reason) ||
    code ||
    `Request failed with status ${status}`

  if (code === 'not_enough_space') return new NotEnoughSpaceError(message)
  if (code === 'file_not_found') return new FileNotFoundError(message)
  if (code === 'sandbox_not_found') return new SandboxNotFoundError(message)
  if (code === 'sandbox_overloaded') return new SandboxOverloadedError(message)
  if (code === 'git_auth') return new GitAuthError(message)
  if (code === 'git_upstream') return new GitUpstreamError(message)
  if (code === 'template_error') return new TemplateError(message)
  if (status === 401 || status === 403) return new AuthenticationError(message)
  if (status === 404) return new NotFoundError(message)
  if (status === 409) return new ConflictError(message)
  if (status === 408 || status === 504) return new TimeoutError(message)
  if (status === 422 || status === 400) return new InvalidArgumentError(message)
  if (status === 429) return new RateLimitError(message)
  return new ApiError(message, status, code)
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

function listMessage(value: unknown): string | undefined {
  return Array.isArray(value) ? value.map(String).join(', ') : undefined
}
