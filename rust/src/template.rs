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

    /// Start from a Debian public base image.
    pub fn from_debian_image(self, variant: impl Into<String>) -> Self {
        self.from_image(format!("debian:{}", variant.into()))
    }

    /// Start from an Ubuntu public base image.
    pub fn from_ubuntu_image(self, variant: impl Into<String>) -> Self {
        self.from_image(format!("ubuntu:{}", variant.into()))
    }

    /// Start from a named Watasu template base.
    pub fn from_template(mut self, template: impl Into<String>) -> Self {
        self.base = Some(template.into());
        self.from_image = None;
        self
    }

    /// Start from a Python public base image.
    pub fn from_python_image(self, version: impl Into<String>) -> Self {
        self.from_image(format!("python:{}", version.into()))
    }

    /// Start from a Node.js public base image.
    pub fn from_node_image(self, variant: impl Into<String>) -> Self {
        self.from_image(format!("node:{}", variant.into()))
    }

    /// Start from a Bun public base image.
    pub fn from_bun_image(self, variant: impl Into<String>) -> Self {
        self.from_image(format!("oven/bun:{}", variant.into()))
    }

    /// Start from a public container image reference.
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
}
