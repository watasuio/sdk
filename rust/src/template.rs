use std::collections::BTreeMap;
use std::time::Duration;

use serde::Deserialize;
use serde_json::{json, Value};

use crate::config::ConnectionConfig;
use crate::error::{Error, Result};
use crate::transport::ControlClient;
use crate::ConnectionOptions;

/// Chainable package-spec template builder.
#[derive(Clone, Debug, Default)]
pub struct TemplateBuilder {
    base: Option<String>,
    from_image: Option<String>,
    packages: BTreeMap<String, Vec<String>>,
    setup: Vec<String>,
    env: BTreeMap<String, String>,
    current_workdir: Option<String>,
    current_user: Option<String>,
    start_cmd: Option<String>,
    ready_cmd: Option<String>,
    skip_cache: bool,
}

impl TemplateBuilder {
    /// Create an empty template builder.
    pub fn new() -> Self {
        Self::default()
    }

    /// Start from the Watasu platform base template.
    pub fn from_base_image(mut self) -> Self {
        self.base = Some("base".to_string());
        self.from_image = None;
        self
    }

    /// Request a Debian public base image.
    ///
    /// The Watasu API fails closed until OCI image import is enabled.
    pub fn from_debian_image(self, variant: impl Into<String>) -> Self {
        self.from_image(format!("debian:{}", variant.into()))
    }

    /// Request an Ubuntu public base image.
    ///
    /// The Watasu API fails closed until OCI image import is enabled.
    pub fn from_ubuntu_image(self, variant: impl Into<String>) -> Self {
        self.from_image(format!("ubuntu:{}", variant.into()))
    }

    /// Start from a ready Watasu template slug, tag, or version id.
    pub fn from_template(mut self, template: impl Into<String>) -> Self {
        self.base = Some(template.into());
        self.from_image = None;
        self
    }

    /// Request a Python public base image.
    ///
    /// The Watasu API fails closed until OCI image import is enabled.
    pub fn from_python_image(self, version: impl Into<String>) -> Self {
        self.from_image(format!("python:{}", version.into()))
    }

    /// Request a Node.js public base image.
    ///
    /// The Watasu API fails closed until OCI image import is enabled.
    pub fn from_node_image(self, variant: impl Into<String>) -> Self {
        self.from_image(format!("node:{}", variant.into()))
    }

    /// Request a Bun public base image.
    ///
    /// The Watasu API fails closed until OCI image import is enabled.
    pub fn from_bun_image(self, variant: impl Into<String>) -> Self {
        self.from_image(format!("oven/bun:{}", variant.into()))
    }

    /// Request a public container image base.
    ///
    /// The Watasu API fails closed until OCI image import is enabled.
    pub fn from_image(mut self, image: impl Into<String>) -> Self {
        self.from_image = Some(image.into());
        self.base = None;
        self
    }

    /// Add apt packages to the template build.
    pub fn apt_install<I, S>(mut self, packages: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        self.add_packages("apt", packages);
        self
    }

    /// Install MCP servers using an `mcp-gateway` template base.
    pub fn add_mcp_server<I, S>(mut self, servers: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        let servers = servers
            .into_iter()
            .map(Into::into)
            .collect::<Vec<String>>()
            .join(" ");
        let command = self.command_with_context(
            format!("mcp-gateway pull {servers}"),
            Some("root".to_string()),
        );
        self.setup.push(command);
        self
    }

    /// Add pip packages to the template build.
    pub fn pip_install<I, S>(mut self, packages: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        self.add_packages("pip", packages);
        self
    }

    /// Add global npm packages to the template build.
    pub fn npm_install<I, S>(mut self, packages: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        self.add_packages("npm", packages);
        self
    }

    /// Run a shell command during template build.
    pub fn run_cmd(mut self, command: impl Into<String>) -> Self {
        let command = self.command_with_context(command.into(), None);
        self.setup.push(command);
        self
    }

    /// Set the working directory for subsequent setup commands.
    pub fn set_workdir(mut self, workdir: impl Into<String>) -> Self {
        self.current_workdir = Some(workdir.into());
        self
    }

    /// Set the user for subsequent setup commands.
    pub fn set_user(mut self, user: impl Into<String>) -> Self {
        self.current_user = Some(user.into());
        self
    }

    /// Set environment variables available during template build.
    pub fn set_envs(mut self, env: BTreeMap<String, String>) -> Self {
        self.env.extend(env);
        self
    }

    /// Set start and ready-check command metadata on the template.
    pub fn set_start_cmd(
        mut self,
        start_cmd: impl Into<String>,
        ready_cmd: impl Into<String>,
    ) -> Self {
        self.start_cmd = Some(start_cmd.into());
        self.ready_cmd = Some(ready_cmd.into());
        self
    }

    /// Force the build to skip cache where the platform supports it.
    pub fn skip_cache(mut self) -> Self {
        self.skip_cache = true;
        self
    }

    /// Return the snake_case build spec sent to the Watasu API.
    pub fn build_spec(&self) -> Value {
        let mut spec = serde_json::Map::new();
        if let Some(base) = &self.base {
            spec.insert("from_template".to_string(), json!(base));
        }
        if let Some(image) = &self.from_image {
            spec.insert("from_image".to_string(), json!(image));
        }
        if !self.packages.is_empty() {
            spec.insert("packages".to_string(), json!(self.packages));
        }
        if !self.setup.is_empty() {
            spec.insert("setup".to_string(), json!(self.setup));
        }
        if !self.env.is_empty() {
            spec.insert("env".to_string(), json!(self.env));
        }
        if let Some(start_cmd) = &self.start_cmd {
            spec.insert("start_cmd".to_string(), json!(start_cmd));
        }
        if let Some(ready_cmd) = &self.ready_cmd {
            spec.insert("ready_cmd".to_string(), json!(ready_cmd));
        }
        Value::Object(spec)
    }

    /// Return the template package spec as formatted JSON.
    pub fn to_json(&self) -> String {
        serde_json::to_string_pretty(&self.build_spec())
            .expect("template build spec should always serialize")
    }

    /// Return a Dockerfile-shaped preview of the supported package spec.
    pub fn to_dockerfile(&self) -> String {
        let mut lines = vec![format!(
            "FROM {}",
            self.from_image
                .as_deref()
                .or(self.base.as_deref())
                .unwrap_or("base")
        )];
        for package in self.packages.get("apt").into_iter().flatten() {
            lines.push(format!(
                "RUN apt-get update && apt-get install -y {package}"
            ));
        }
        for package in self.packages.get("pip").into_iter().flatten() {
            lines.push(format!("RUN python3 -m pip install {package}"));
        }
        for package in self.packages.get("npm").into_iter().flatten() {
            lines.push(format!("RUN npm install -g {package}"));
        }
        for command in &self.setup {
            lines.push(format!("RUN {command}"));
        }
        format!("{}\n", lines.join("\n"))
    }

    fn add_packages<I, S>(&mut self, manager: &str, packages: I)
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        self.packages
            .entry(manager.to_string())
            .or_default()
            .extend(packages.into_iter().map(Into::into));
    }

    fn command_with_context(&self, command: String, user: Option<String>) -> String {
        let command = if let Some(workdir) = &self.current_workdir {
            format!("cd {} && {command}", shell_quote(workdir))
        } else {
            command
        };
        let user = user.or_else(|| self.current_user.clone());
        match user.as_deref() {
            Some(user) if user != "root" => {
                format!(
                    "su -s /bin/bash -c {} {}",
                    shell_quote(&command),
                    shell_quote(user)
                )
            }
            _ => command,
        }
    }
}

/// Options for starting a template build.
#[derive(Clone, Debug, Default)]
pub struct TemplateBuildOptions {
    /// Connection options.
    pub connection: ConnectionOptions,
    /// Tags assigned to this build.
    pub tags: Vec<String>,
    /// Default vCPU count for sandboxes started from this template.
    pub cpu_count: Option<u32>,
    /// Default memory in MiB for sandboxes started from this template.
    pub memory_mb: Option<u32>,
    /// Force cache bypass where supported.
    pub skip_cache: bool,
    /// Optional team name.
    pub team: Option<String>,
}

/// Information returned when a template build starts.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BuildInfo {
    /// Template identifier.
    pub template_id: String,
    /// Build identifier.
    pub build_id: String,
    /// Template name.
    pub name: String,
    /// First template alias.
    pub alias: String,
    /// Tags assigned to the build.
    pub tags: Vec<String>,
}

/// Options for querying build status.
#[derive(Clone, Debug, Default)]
pub struct TemplateBuildStatusOptions {
    /// Connection options.
    pub connection: ConnectionOptions,
    /// Log offset for incremental log fetches.
    pub logs_offset: Option<usize>,
}

/// Template build status.
#[derive(Clone, Debug, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TemplateBuildStatus {
    /// Build is running.
    Building,
    /// Build is waiting for capacity or another prerequisite.
    Waiting,
    /// Build is ready.
    Ready,
    /// Build failed.
    Error,
}

/// Build log entry.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LogEntry {
    /// RFC3339 timestamp when available.
    pub timestamp: Option<String>,
    /// Log level.
    pub level: String,
    /// Message.
    pub message: String,
}

/// Reason for a build status, usually populated for errors.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BuildStatusReason {
    /// Human-readable message.
    pub message: String,
    /// Failed step when available.
    pub step: Option<String>,
    /// Related log entries.
    pub log_entries: Vec<LogEntry>,
}

/// Response from querying template build status.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TemplateBuildStatusResponse {
    /// Build identifier.
    pub build_id: String,
    /// Template identifier.
    pub template_id: String,
    /// Current status.
    pub status: TemplateBuildStatus,
    /// New structured log entries.
    pub log_entries: Vec<LogEntry>,
    /// Raw log lines.
    pub logs: Vec<String>,
    /// Optional status reason.
    pub reason: Option<BuildStatusReason>,
}

/// Information about assigned tags.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TemplateTagInfo {
    /// Build identifier.
    pub build_id: String,
    /// Assigned tags.
    pub tags: Vec<String>,
}

/// A single template tag.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TemplateTag {
    /// Tag value.
    pub tag: String,
    /// Build identifier.
    pub build_id: String,
    /// Creation timestamp.
    pub created_at: Option<String>,
}

/// Options for creating a sandbox template through `/sandbox_templates`.
#[derive(Clone, Debug, Default)]
pub struct SandboxTemplateCreateOptions {
    /// Connection options.
    pub connection: ConnectionOptions,
    /// Team slug/name that should own the template.
    pub team: Option<String>,
    /// Stable template slug.
    pub slug: String,
    /// Human-readable template name.
    pub name: String,
    /// Optional template description.
    pub description: Option<String>,
    /// Caller metadata stored with the template.
    pub metadata: serde_json::Map<String, Value>,
    /// Optional idempotency key for retry-safe creates.
    pub idempotency_key: Option<String>,
}

/// Sandbox template metadata returned by `/sandbox_templates`.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct SandboxTemplateInfo {
    /// Template id.
    pub template_id: String,
    /// Stable template slug.
    pub slug: String,
    /// Human-readable template name.
    pub name: String,
    /// Owning team name, when team-owned.
    pub team: Option<String>,
    /// Visibility, such as `provider` or `team`.
    pub visibility: Option<String>,
    /// Template lifecycle status.
    pub status: Option<String>,
    /// Optional template description.
    pub description: Option<String>,
    /// Caller metadata stored with the template.
    pub metadata: serde_json::Map<String, Value>,
    /// Raw API payload for forward-compatible callers.
    pub raw: Value,
}

/// Runtime baseline for a sandbox template version.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SandboxTemplateRuntimeBaseline {
    /// vCPU count.
    pub cpu: Option<u64>,
    /// Memory in MiB.
    pub memory_mb: Option<u64>,
}

/// Options for creating a sandbox template version.
#[derive(Clone, Debug)]
pub struct SandboxTemplateVersionCreateOptions {
    /// Connection options.
    pub connection: ConnectionOptions,
    /// Version string unique within the template.
    pub version: String,
    /// Source kind. Defaults to `package_spec`.
    pub source_kind: String,
    /// CPU/memory baseline. Disk is platform-managed and intentionally not exposed.
    pub runtime_baseline: Option<SandboxTemplateRuntimeBaseline>,
    /// Package-spec build payload.
    pub build_spec: Value,
    /// Optional architecture, such as `x86_64`.
    pub architecture: Option<String>,
    /// Default guest user.
    pub default_user: Option<String>,
    /// Default working directory.
    pub workdir: Option<String>,
    /// Caller metadata stored with the version.
    pub metadata: serde_json::Map<String, Value>,
    /// Optional idempotency key for retry-safe creates.
    pub idempotency_key: Option<String>,
}

impl Default for SandboxTemplateVersionCreateOptions {
    fn default() -> Self {
        Self {
            connection: ConnectionOptions::default(),
            version: String::new(),
            source_kind: "package_spec".to_string(),
            runtime_baseline: None,
            build_spec: Value::Object(Default::default()),
            architecture: None,
            default_user: None,
            workdir: None,
            metadata: Default::default(),
            idempotency_key: None,
        }
    }
}

/// Sandbox template version metadata returned by `/sandbox_templates/:id/versions`.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct SandboxTemplateVersionInfo {
    /// Template version id.
    pub template_version_id: String,
    /// Parent template id.
    pub template_id: String,
    /// Parent template slug.
    pub template_slug: Option<String>,
    /// Version string.
    pub version: String,
    /// Provider status, such as `draft`, `building`, `ready`, or `failed`.
    pub status: String,
    /// Architecture, when returned.
    pub architecture: Option<String>,
    /// Source kind, when returned.
    pub source_kind: Option<String>,
    /// Default guest user.
    pub default_user: Option<String>,
    /// Default working directory.
    pub workdir: Option<String>,
    /// Runtime CPU/memory baseline.
    pub runtime_baseline: Option<SandboxTemplateRuntimeBaseline>,
    /// Redacted build spec returned by the API.
    pub build_spec: Value,
    /// Build log entry count.
    pub build_log_entry_count: usize,
    /// Last build log entries returned with the version payload.
    pub latest_build_log_entries: Vec<Value>,
    /// Build failure summary, if failed.
    pub build_failure: Option<Value>,
    /// Last error message, if any.
    pub last_error_message: Option<String>,
    /// Build completion timestamp, if built.
    pub built_at: Option<String>,
    /// Raw API payload for forward-compatible callers.
    pub raw: Value,
}

/// Build logs for a sandbox template version.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct SandboxTemplateVersionBuildLogs {
    /// Template version id.
    pub template_version_id: String,
    /// Provider status.
    pub status: String,
    /// Persisted build log entries.
    pub entries: Vec<Value>,
    /// Raw API payload for forward-compatible callers.
    pub raw: Value,
}

/// Template build API.
pub struct Template;

impl Template {
    /// Start a template build without waiting for completion.
    pub async fn build_in_background(
        template: TemplateBuilder,
        name: impl Into<String>,
        opts: TemplateBuildOptions,
    ) -> Result<BuildInfo> {
        let config = ConnectionConfig::new(opts.connection);
        let control = ControlClient::new(config)?;
        let mut body = json!({
            "name": name.into(),
            "tags": opts.tags,
            "cpu_count": opts.cpu_count.unwrap_or(2),
            "memory_mb": opts.memory_mb.unwrap_or(1024),
            "skip_cache": opts.skip_cache || template.skip_cache,
            "build_spec": template.build_spec(),
        });
        if let Some(team) = opts.team {
            body["team"] = json!(team);
        }
        let response = control.post("/templates", body).await?;
        build_info(response.get("template_build").unwrap_or(&response))
    }

    /// Build a template and wait until it is ready.
    pub async fn build(
        template: TemplateBuilder,
        name: impl Into<String>,
        opts: TemplateBuildOptions,
    ) -> Result<BuildInfo> {
        let build_info = Self::build_in_background(template, name, opts.clone()).await?;
        loop {
            let status = Self::get_build_status(
                &build_info,
                TemplateBuildStatusOptions {
                    connection: opts.connection.clone(),
                    ..TemplateBuildStatusOptions::default()
                },
            )
            .await?;
            match status.status {
                TemplateBuildStatus::Ready => return Ok(build_info),
                TemplateBuildStatus::Error => {
                    return Err(Error::Sandbox(
                        status
                            .reason
                            .map(|reason| reason.message)
                            .unwrap_or_else(|| "template build failed".to_string()),
                    ));
                }
                TemplateBuildStatus::Building | TemplateBuildStatus::Waiting => {
                    tokio::time::sleep(Duration::from_millis(200)).await;
                }
            }
        }
    }

    /// Return the current status of a template build.
    pub async fn get_build_status(
        build_info: &BuildInfo,
        opts: TemplateBuildStatusOptions,
    ) -> Result<TemplateBuildStatusResponse> {
        let config = ConnectionConfig::new(opts.connection);
        let control = ControlClient::new(config)?;
        let path = match opts.logs_offset {
            Some(offset) => format!(
                "/templates/{}/builds/{}/status?logs_offset={offset}",
                encode_path(&build_info.template_id),
                encode_path(&build_info.build_id)
            ),
            None => format!(
                "/templates/{}/builds/{}/status",
                encode_path(&build_info.template_id),
                encode_path(&build_info.build_id)
            ),
        };
        template_build_status(&control.get(&path).await?)
    }

    /// Return whether a template name exists.
    pub async fn exists(name: impl AsRef<str>, connection: ConnectionOptions) -> Result<bool> {
        Self::alias_exists(name, connection).await
    }

    /// Return whether a template alias exists.
    pub async fn alias_exists(
        alias: impl AsRef<str>,
        connection: ConnectionOptions,
    ) -> Result<bool> {
        let config = ConnectionConfig::new(connection);
        let control = ControlClient::new(config)?;
        match control
            .get(&format!(
                "/templates/aliases/{}",
                encode_path(alias.as_ref())
            ))
            .await
        {
            Ok(_) => Ok(true),
            Err(Error::NotFound(_)) => Ok(false),
            Err(error) => Err(error),
        }
    }

    /// Assign tags to an existing template build.
    pub async fn assign_tags(
        target_name: impl Into<String>,
        tags: Vec<String>,
        connection: ConnectionOptions,
    ) -> Result<TemplateTagInfo> {
        let config = ConnectionConfig::new(connection);
        let control = ControlClient::new(config)?;
        let response = control
            .post(
                "/templates/tags",
                json!({"target": target_name.into(), "tags": tags}),
            )
            .await?;
        Ok(TemplateTagInfo {
            build_id: string_field(&response, "build_id"),
            tags: string_vec(response.get("tags")),
        })
    }

    /// Remove tags from a template.
    pub async fn remove_tags(
        name: impl Into<String>,
        tags: Vec<String>,
        connection: ConnectionOptions,
    ) -> Result<()> {
        let config = ConnectionConfig::new(connection);
        let control = ControlClient::new(config)?;
        control
            .delete_with_body(
                "/templates/tags",
                json!({"name": name.into(), "tags": tags}),
            )
            .await?;
        Ok(())
    }

    /// List tags for a template.
    pub async fn get_tags(
        template_id: impl AsRef<str>,
        connection: ConnectionOptions,
    ) -> Result<Vec<TemplateTag>> {
        let config = ConnectionConfig::new(connection);
        let control = ControlClient::new(config)?;
        let response = control
            .get(&format!(
                "/templates/{}/tags",
                encode_path(template_id.as_ref())
            ))
            .await?;
        let tags = response
            .as_array()
            .map(|items| items.iter().map(template_tag).collect())
            .unwrap_or_default();
        Ok(tags)
    }

    /// List accessible sandbox templates using the `/sandbox_templates` API.
    pub async fn list_sandbox_templates(
        connection: ConnectionOptions,
    ) -> Result<Vec<SandboxTemplateInfo>> {
        let config = ConnectionConfig::new(connection);
        let control = ControlClient::new(config)?;
        let response = control.get("/sandbox_templates").await?;
        Ok(response
            .get("sandbox_templates")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .map(|value| sandbox_template_info(&value))
            .collect())
    }

    /// Find an accessible sandbox template by slug.
    pub async fn find_sandbox_template_by_slug(
        slug: impl AsRef<str>,
        connection: ConnectionOptions,
    ) -> Result<Option<SandboxTemplateInfo>> {
        let slug = slug.as_ref();
        Ok(Self::list_sandbox_templates(connection)
            .await?
            .into_iter()
            .find(|template| template.slug == slug))
    }

    /// Create a team-owned sandbox template using the `/sandbox_templates` API.
    pub async fn create_sandbox_template(
        opts: SandboxTemplateCreateOptions,
    ) -> Result<SandboxTemplateInfo> {
        let config = ConnectionConfig::new(opts.connection.clone());
        let control = ControlClient::new(config)?;
        let response = control
            .post_idempotent(
                "/sandbox_templates",
                sandbox_template_create_payload(&opts),
                opts.idempotency_key.as_deref(),
            )
            .await?;
        Ok(sandbox_template_info(
            response.get("sandbox_template").unwrap_or(&response),
        ))
    }

    /// List versions for an accessible sandbox template.
    pub async fn list_sandbox_template_versions(
        template_id: impl AsRef<str>,
        connection: ConnectionOptions,
    ) -> Result<Vec<SandboxTemplateVersionInfo>> {
        let config = ConnectionConfig::new(connection);
        let control = ControlClient::new(config)?;
        let response = control
            .get(&format!(
                "/sandbox_templates/{}/versions",
                encode_path(template_id.as_ref())
            ))
            .await?;
        Ok(response
            .get("sandbox_template_versions")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .map(|value| sandbox_template_version_info(&value))
            .collect())
    }

    /// Create a sandbox template version using the `/sandbox_templates/:id/versions` API.
    pub async fn create_sandbox_template_version(
        template_id: impl AsRef<str>,
        opts: SandboxTemplateVersionCreateOptions,
    ) -> Result<SandboxTemplateVersionInfo> {
        let config = ConnectionConfig::new(opts.connection.clone());
        let control = ControlClient::new(config)?;
        let response = control
            .post_idempotent(
                &format!(
                    "/sandbox_templates/{}/versions",
                    encode_path(template_id.as_ref())
                ),
                sandbox_template_version_create_payload(&opts),
                opts.idempotency_key.as_deref(),
            )
            .await?;
        Ok(sandbox_template_version_info(
            response
                .get("sandbox_template_version")
                .unwrap_or(&response),
        ))
    }

    /// Get a sandbox template version by template id and version id.
    pub async fn get_sandbox_template_version(
        template_id: impl AsRef<str>,
        template_version_id: impl AsRef<str>,
        connection: ConnectionOptions,
    ) -> Result<SandboxTemplateVersionInfo> {
        let config = ConnectionConfig::new(connection);
        let control = ControlClient::new(config)?;
        let response = control
            .get(&format!(
                "/sandbox_templates/{}/versions/{}",
                encode_path(template_id.as_ref()),
                encode_path(template_version_id.as_ref())
            ))
            .await?;
        Ok(sandbox_template_version_info(
            response
                .get("sandbox_template_version")
                .unwrap_or(&response),
        ))
    }

    /// Get persisted build logs for a sandbox template version.
    pub async fn get_sandbox_template_version_build_logs(
        template_id: impl AsRef<str>,
        template_version_id: impl AsRef<str>,
        connection: ConnectionOptions,
    ) -> Result<SandboxTemplateVersionBuildLogs> {
        let config = ConnectionConfig::new(connection);
        let control = ControlClient::new(config)?;
        let response = control
            .get(&format!(
                "/sandbox_templates/{}/versions/{}/build_logs",
                encode_path(template_id.as_ref()),
                encode_path(template_version_id.as_ref())
            ))
            .await?;
        Ok(sandbox_template_build_logs(
            response
                .get("sandbox_template_version_build_logs")
                .unwrap_or(&response),
        ))
    }

    /// Delete a sandbox template version. Returns `false` when it is already missing.
    pub async fn delete_sandbox_template_version(
        template_id: impl AsRef<str>,
        template_version_id: impl AsRef<str>,
        connection: ConnectionOptions,
    ) -> Result<bool> {
        let config = ConnectionConfig::new(connection);
        let control = ControlClient::new(config)?;
        match control
            .delete(&format!(
                "/sandbox_templates/{}/versions/{}",
                encode_path(template_id.as_ref()),
                encode_path(template_version_id.as_ref())
            ))
            .await
        {
            Ok(_) => Ok(true),
            Err(Error::NotFound(_)) => Ok(false),
            Err(error) => Err(error),
        }
    }
}

fn build_info(value: &Value) -> Result<BuildInfo> {
    let template_id = string_field(value, "template_id");
    let build_id = string_field(value, "build_id");
    if template_id.is_empty() || build_id.is_empty() {
        return Err(Error::Sandbox(
            "template build response did not include identifiers".to_string(),
        ));
    }
    Ok(BuildInfo {
        template_id,
        build_id,
        name: string_field(value, "name"),
        alias: string_field(value, "alias"),
        tags: string_vec(value.get("tags")),
    })
}

fn template_build_status(value: &Value) -> Result<TemplateBuildStatusResponse> {
    let status = serde_json::from_value(
        value
            .get("status")
            .cloned()
            .unwrap_or_else(|| json!("building")),
    )
    .map_err(|error| Error::Sandbox(error.to_string()))?;
    Ok(TemplateBuildStatusResponse {
        build_id: string_field(value, "build_id"),
        template_id: string_field(value, "template_id"),
        status,
        log_entries: value
            .get("log_entries")
            .and_then(Value::as_array)
            .map(|items| items.iter().map(log_entry).collect())
            .unwrap_or_default(),
        logs: string_vec(value.get("logs")),
        reason: value.get("reason").and_then(build_status_reason),
    })
}

fn build_status_reason(value: &Value) -> Option<BuildStatusReason> {
    Some(BuildStatusReason {
        message: string_field(value, "message"),
        step: value
            .get("step")
            .and_then(Value::as_str)
            .map(str::to_string),
        log_entries: value
            .get("log_entries")
            .and_then(Value::as_array)
            .map(|items| items.iter().map(log_entry).collect())
            .unwrap_or_default(),
    })
}

fn log_entry(value: &Value) -> LogEntry {
    LogEntry {
        timestamp: value
            .get("timestamp")
            .and_then(Value::as_str)
            .map(str::to_string),
        level: string_field(value, "level"),
        message: string_field(value, "message"),
    }
}

fn template_tag(value: &Value) -> TemplateTag {
    TemplateTag {
        tag: string_field(value, "tag"),
        build_id: string_field(value, "build_id"),
        created_at: value
            .get("created_at")
            .and_then(Value::as_str)
            .map(str::to_string),
    }
}

fn sandbox_template_create_payload(opts: &SandboxTemplateCreateOptions) -> Value {
    let mut template = serde_json::Map::new();
    template.insert("slug".into(), json!(opts.slug));
    template.insert("name".into(), json!(opts.name));
    if let Some(team) = &opts.team {
        template.insert("team".into(), json!(team));
    }
    if let Some(description) = &opts.description {
        template.insert("description".into(), json!(description));
    }
    if !opts.metadata.is_empty() {
        template.insert("metadata".into(), Value::Object(opts.metadata.clone()));
    }
    json!({"sandbox_template": template})
}

fn sandbox_template_version_create_payload(opts: &SandboxTemplateVersionCreateOptions) -> Value {
    let mut version = serde_json::Map::new();
    version.insert("version".into(), json!(opts.version));
    version.insert("source_kind".into(), json!(opts.source_kind));
    version.insert("build_spec".into(), opts.build_spec.clone());
    if let Some(baseline) = &opts.runtime_baseline {
        version.insert(
            "runtime_baseline".into(),
            runtime_baseline_payload(baseline),
        );
    }
    if let Some(architecture) = &opts.architecture {
        version.insert("architecture".into(), json!(architecture));
    }
    if let Some(default_user) = &opts.default_user {
        version.insert("default_user".into(), json!(default_user));
    }
    if let Some(workdir) = &opts.workdir {
        version.insert("workdir".into(), json!(workdir));
    }
    if !opts.metadata.is_empty() {
        version.insert("metadata".into(), Value::Object(opts.metadata.clone()));
    }
    json!({"sandbox_template_version": version})
}

fn runtime_baseline_payload(baseline: &SandboxTemplateRuntimeBaseline) -> Value {
    let mut payload = serde_json::Map::new();
    if let Some(cpu) = baseline.cpu {
        payload.insert("cpu".into(), json!(cpu));
    }
    if let Some(memory_mb) = baseline.memory_mb {
        payload.insert("memory_mb".into(), json!(memory_mb));
    }
    Value::Object(payload)
}

fn sandbox_template_info(value: &Value) -> SandboxTemplateInfo {
    SandboxTemplateInfo {
        template_id: string_field(value, "id"),
        slug: string_field(value, "slug"),
        name: string_field(value, "name"),
        team: optional_string_field(value, "team"),
        visibility: optional_string_field(value, "visibility"),
        status: optional_string_field(value, "status"),
        description: optional_string_field(value, "description"),
        metadata: map_field(value, "metadata"),
        raw: value.clone(),
    }
}

fn sandbox_template_version_info(value: &Value) -> SandboxTemplateVersionInfo {
    SandboxTemplateVersionInfo {
        template_version_id: string_field(value, "id"),
        template_id: string_field(value, "template_id"),
        template_slug: optional_string_field(value, "template_slug"),
        version: string_field(value, "version"),
        status: string_field(value, "status"),
        architecture: optional_string_field(value, "architecture"),
        source_kind: optional_string_field(value, "source_kind"),
        default_user: optional_string_field(value, "default_user"),
        workdir: optional_string_field(value, "workdir"),
        runtime_baseline: value
            .get("runtime_baseline")
            .and_then(runtime_baseline_info),
        build_spec: value
            .get("build_spec")
            .cloned()
            .unwrap_or_else(|| Value::Object(Default::default())),
        build_log_entry_count: value
            .get("build_log_entry_count")
            .and_then(Value::as_u64)
            .unwrap_or_default() as usize,
        latest_build_log_entries: value
            .get("latest_build_log_entries")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default(),
        build_failure: value.get("build_failure").cloned(),
        last_error_message: optional_string_field(value, "last_error_message"),
        built_at: optional_string_field(value, "built_at"),
        raw: value.clone(),
    }
}

fn runtime_baseline_info(value: &Value) -> Option<SandboxTemplateRuntimeBaseline> {
    let object = value.as_object()?;
    Some(SandboxTemplateRuntimeBaseline {
        cpu: object.get("cpu").and_then(Value::as_u64),
        memory_mb: object.get("memory_mb").and_then(Value::as_u64),
    })
}

fn sandbox_template_build_logs(value: &Value) -> SandboxTemplateVersionBuildLogs {
    SandboxTemplateVersionBuildLogs {
        template_version_id: string_field(value, "template_version_id"),
        status: string_field(value, "status"),
        entries: value
            .get("entries")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default(),
        raw: value.clone(),
    }
}

fn string_field(value: &Value, key: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .map(str::to_string)
        .or_else(|| {
            value
                .get(key)
                .and_then(Value::as_u64)
                .map(|v| v.to_string())
        })
        .unwrap_or_default()
}

fn optional_string_field(value: &Value, key: &str) -> Option<String> {
    value.get(key).and_then(|value| {
        value
            .as_str()
            .map(str::to_string)
            .or_else(|| value.as_u64().map(|v| v.to_string()))
    })
}

fn map_field(value: &Value, key: &str) -> serde_json::Map<String, Value> {
    value
        .get(key)
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default()
}

fn string_vec(value: Option<&Value>) -> Vec<String> {
    match value {
        Some(Value::Array(items)) => items
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect(),
        Some(Value::String(value)) => vec![value.clone()],
        _ => vec![],
    }
}

fn shell_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''"))
}

fn encode_path(value: &str) -> String {
    url::form_urlencoded::byte_serialize(value.as_bytes()).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builder_uses_snake_case_package_spec() {
        let spec = TemplateBuilder::new()
            .from_python_image("3.12")
            .apt_install(["git"])
            .pip_install(["pytest"])
            .set_workdir("/workspace")
            .run_cmd("echo ready")
            .build_spec();

        assert_eq!(
            spec,
            json!({
                "from_image": "python:3.12",
                "packages": {"apt": ["git"], "pip": ["pytest"]},
                "setup": ["cd '/workspace' && echo ready"],
            })
        );
    }

    #[test]
    fn builder_serializes_json_dockerfile_and_mcp_helpers() {
        let template = TemplateBuilder::new()
            .from_python_image("3.12")
            .apt_install(["git"])
            .pip_install(["pytest"])
            .run_cmd("echo ready");

        assert_eq!(
            serde_json::from_str::<Value>(&template.to_json()).unwrap(),
            json!({
                "from_image": "python:3.12",
                "packages": {"apt": ["git"], "pip": ["pytest"]},
                "setup": ["echo ready"],
            })
        );
        assert_eq!(
            template.to_dockerfile(),
            "FROM python:3.12\nRUN apt-get update && apt-get install -y git\nRUN python3 -m pip install pytest\nRUN echo ready\n"
        );

        let mcp_template = TemplateBuilder::new()
            .from_template("mcp-gateway")
            .add_mcp_server(["exa", "brave"]);
        assert_eq!(
            mcp_template.build_spec(),
            json!({
                "from_template": "mcp-gateway",
                "setup": ["mcp-gateway pull exa brave"],
            })
        );
    }

    #[test]
    fn sandbox_template_create_payload_wraps_template_attrs() {
        let mut metadata = serde_json::Map::new();
        metadata.insert("bridge_managed".into(), json!(true));

        assert_eq!(
            sandbox_template_create_payload(&SandboxTemplateCreateOptions {
                slug: "python".into(),
                name: "Python".into(),
                team: Some("watasu".into()),
                description: Some("Python runtime".into()),
                metadata,
                ..SandboxTemplateCreateOptions::default()
            }),
            json!({
                "sandbox_template": {
                    "slug": "python",
                    "name": "Python",
                    "team": "watasu",
                    "description": "Python runtime",
                    "metadata": {"bridge_managed": true}
                }
            })
        );
    }

    #[test]
    fn sandbox_template_version_payload_omits_platform_managed_disk() {
        let payload =
            sandbox_template_version_create_payload(&SandboxTemplateVersionCreateOptions {
                version: "2026-06-18".into(),
                runtime_baseline: Some(SandboxTemplateRuntimeBaseline {
                    cpu: Some(2),
                    memory_mb: Some(2048),
                }),
                build_spec: json!({
                    "packages": {"apt": ["git"]},
                    "setup": ["echo ready"]
                }),
                architecture: Some("x86_64".into()),
                default_user: Some("root".into()),
                workdir: Some("/workspace".into()),
                ..SandboxTemplateVersionCreateOptions::default()
            });

        assert_eq!(
            payload,
            json!({
                "sandbox_template_version": {
                    "version": "2026-06-18",
                    "source_kind": "package_spec",
                    "runtime_baseline": {"cpu": 2, "memory_mb": 2048},
                    "build_spec": {
                        "packages": {"apt": ["git"]},
                        "setup": ["echo ready"]
                    },
                    "architecture": "x86_64",
                    "default_user": "root",
                    "workdir": "/workspace"
                }
            })
        );
        assert!(payload
            .pointer("/sandbox_template_version/runtime_baseline/disk_mb")
            .is_none());
    }

    #[test]
    fn sandbox_template_version_info_maps_build_contract() {
        let info = sandbox_template_version_info(&json!({
            "id": 42,
            "template_id": 17,
            "template_slug": "python",
            "version": "v1",
            "status": "failed",
            "architecture": "x86_64",
            "source_kind": "package_spec",
            "default_user": "root",
            "workdir": "/workspace",
            "runtime_baseline": {"cpu": 2, "memory_mb": 2048},
            "build_spec": {"packages": {"pip": ["pytest"]}},
            "build_log_entry_count": 2,
            "latest_build_log_entries": [{"level": "error", "message": "boom"}],
            "build_failure": {"message": "boom"},
            "last_error_message": "boom",
            "built_at": "2026-06-18T00:00:00Z"
        }));

        assert_eq!(info.template_version_id, "42");
        assert_eq!(info.template_id, "17");
        assert_eq!(info.template_slug.as_deref(), Some("python"));
        assert_eq!(info.status, "failed");
        assert_eq!(
            info.runtime_baseline,
            Some(SandboxTemplateRuntimeBaseline {
                cpu: Some(2),
                memory_mb: Some(2048)
            })
        );
        assert_eq!(info.build_log_entry_count, 2);
        assert_eq!(info.latest_build_log_entries.len(), 1);
        assert_eq!(info.last_error_message.as_deref(), Some("boom"));
    }

    #[test]
    fn sandbox_template_build_logs_maps_entries() {
        let logs = sandbox_template_build_logs(&json!({
            "template_version_id": 42,
            "status": "building",
            "entries": [{"level": "info", "message": "start"}]
        }));

        assert_eq!(logs.template_version_id, "42");
        assert_eq!(logs.status, "building");
        assert_eq!(logs.entries.len(), 1);
    }
}
