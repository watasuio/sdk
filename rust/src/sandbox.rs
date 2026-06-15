use serde_json::Value;

use crate::commands::Commands;
use crate::config::{ConnectionConfig, ConnectionOptions};
use crate::error::{Error, Result};
use crate::filesystem::Filesystem;
use crate::git::Git;
use crate::pty::Pty;
use crate::transport::{ControlClient, DataPlaneClient};

/// Options for `Sandbox::create`.
#[derive(Clone, Debug)]
pub struct CreateOptions {
    /// Connection options used for control-plane and data-plane requests.
    pub connection: ConnectionOptions,
    /// Template slug. Defaults to `base`.
    pub template: String,
    /// Sandbox lifetime in seconds.
    pub timeout_seconds: u64,
    /// User metadata stored with the sandbox.
    pub metadata: serde_json::Map<String, Value>,
    /// Environment variables injected into commands started through this SDK.
    pub envs: serde_json::Map<String, Value>,
    /// Whether the sandbox may access the public internet.
    pub allow_internet_access: bool,
    /// Explicit template version id. Encoded as a `template_id` pin on the wire.
    pub template_version_id: Option<u64>,
    /// Team slug to create the sandbox under.
    pub team: Option<String>,
    /// Requested vCPU count.
    pub cpu: Option<u64>,
    /// Requested memory in MiB.
    pub memory_mb: Option<u64>,
    /// Runtime network class.
    pub network_class: Option<String>,
    /// Runtime network policy.
    pub network: Option<NetworkUpdateOptions>,
    /// Whether package registry egress should be allowed.
    pub allow_package_registry_access: Option<bool>,
    /// Raw exposed-port declarations for Watasu-specific callers.
    pub exposed_ports: Option<Value>,
}

impl Default for CreateOptions {
    fn default() -> Self {
        Self {
            connection: ConnectionOptions::default(),
            template: "base".to_string(),
            timeout_seconds: 300,
            metadata: Default::default(),
            envs: Default::default(),
            allow_internet_access: true,
            template_version_id: None,
            team: None,
            cpu: None,
            memory_mb: None,
            network_class: None,
            network: None,
            allow_package_registry_access: None,
            exposed_ports: None,
        }
    }
}

/// Control-plane metadata for a sandbox.
#[derive(Clone, Debug, Default)]
pub struct SandboxInfo {
    /// Sandbox id.
    pub sandbox_id: String,
    /// Template slug or id returned by the API.
    pub template_id: Option<String>,
    /// Template version id, when returned by the API.
    pub template_version_id: Option<u64>,
    /// Current sandbox lifecycle state.
    pub state: Option<String>,
    /// User metadata.
    pub metadata: serde_json::Map<String, Value>,
    /// Creation timestamp.
    pub started_at: Option<String>,
    /// Deadline timestamp.
    pub end_at: Option<String>,
}

/// Runtime metrics returned for a sandbox.
#[derive(Clone, Debug, Default)]
pub struct SandboxMetrics {
    /// Sandbox id.
    pub sandbox_id: Option<String>,
    /// Current sandbox lifecycle state.
    pub state: Option<String>,
    /// Runtime node name.
    pub node: Option<String>,
    /// Runtime backend name.
    pub backend: Option<String>,
    /// vCPU count, when returned by the API.
    pub cpu_count: Option<u64>,
    /// Memory in MiB, when returned by the API.
    pub memory_mb: Option<u64>,
    /// Full raw metrics payload.
    pub raw: Value,
}

/// Watasu checkpoint metadata exposed with snapshot naming.
#[derive(Clone, Debug, Default)]
pub struct SnapshotInfo {
    /// Snapshot/checkpoint id.
    pub snapshot_id: String,
    /// Source sandbox id.
    pub sandbox_id: Option<String>,
    /// Snapshot/checkpoint name.
    pub name: Option<String>,
    /// Snapshot/checkpoint status.
    pub status: Option<String>,
    /// Snapshot/checkpoint size in bytes.
    pub size_bytes: Option<u64>,
    /// Creation timestamp.
    pub created_at: Option<String>,
    /// Expiration timestamp.
    pub expires_at: Option<String>,
    /// Full raw snapshot payload.
    pub raw: Value,
}

/// Signed file URL metadata.
#[derive(Clone, Debug, Default)]
pub struct FileUrlInfo {
    /// HTTP method accepted by the signed URL.
    pub method: String,
    /// Sandbox file path.
    pub path: String,
    /// Signed URL.
    pub url: String,
    /// Expiration timestamp.
    pub expires_at: Option<String>,
    /// Full raw file URL payload.
    pub raw: Value,
}

/// Options for atomically replacing a sandbox network policy.
#[derive(Clone, Debug, Default)]
pub struct NetworkUpdateOptions {
    /// Whether the sandbox may access the public internet.
    pub allow_internet_access: Option<bool>,
    /// Whether package registry egress should be allowed.
    pub allow_package_registry_access: Option<bool>,
    /// Whether public traffic to exposed sandbox URLs is allowed.
    pub allow_public_traffic: Option<bool>,
    /// Additional allowed outbound hosts or CIDRs, optionally with `:port`.
    pub allow_out: Vec<String>,
    /// Denied outbound hosts or CIDRs, optionally with `:port`.
    pub deny_out: Vec<String>,
    /// Single egress profile name.
    pub egress_profile: Option<String>,
    /// Egress profile names.
    pub egress_profiles: Vec<String>,
    /// Runtime network class.
    pub network_class: Option<String>,
}

/// Options for signed file URL creation.
#[derive(Clone, Debug, Default)]
pub struct FileUrlOptions {
    /// Optional sandbox user.
    pub user: Option<String>,
    /// Signature expiration in seconds.
    pub use_signature_expiration: Option<u64>,
    /// URL expiration in seconds.
    pub expires_in_seconds: Option<u64>,
}

/// Options for creating a Watasu checkpoint.
#[derive(Clone, Debug, Default)]
pub struct CreateSnapshotOptions {
    /// Optional checkpoint name.
    pub name: Option<String>,
    /// Optional metadata stored with the checkpoint.
    pub metadata: serde_json::Map<String, Value>,
    /// Optional expiration timestamp.
    pub expires_at: Option<String>,
    /// Optional quiesce mode.
    pub quiesce_mode: Option<String>,
}

/// Options for restoring a checkpoint.
#[derive(Clone, Debug)]
pub struct RestoreOptions {
    /// Checkpoint/snapshot id to restore.
    pub checkpoint_id: String,
    /// Optional restored sandbox lifetime in seconds.
    pub timeout_seconds: Option<u64>,
}

/// Running Watasu sandbox with ready `commands` and `files` helpers.
pub struct Sandbox {
    /// Sandbox id.
    pub sandbox_id: String,
    /// Command runner for this sandbox.
    pub commands: Commands,
    /// Filesystem helper for this sandbox.
    pub files: Filesystem,
    /// PTY helper for this sandbox.
    pub pty: Pty,
    /// Git helper for this sandbox.
    pub git: Git,
    config: ConnectionConfig,
    control: ControlClient,
    sandbox: Value,
    envs: serde_json::Map<String, Value>,
}

impl Sandbox {
    /// Create a sandbox and return it only after the API supplies a data-plane session.
    pub async fn create(opts: CreateOptions) -> Result<Self> {
        let config = ConnectionConfig::new(opts.connection.clone());
        let control = ControlClient::new(config.clone())?;
        let mut sandbox = serde_json::Map::new();
        let template_id = match opts.template_version_id {
            Some(version_id) => format!("{}:{version_id}", opts.template),
            None => opts.template,
        };
        sandbox.insert("template_id".into(), Value::String(template_id));
        sandbox.insert("timeout".into(), Value::from(opts.timeout_seconds));
        sandbox.insert("metadata".into(), Value::Object(opts.metadata));
        sandbox.insert("env_vars".into(), Value::Object(opts.envs.clone()));
        sandbox.insert(
            "allow_internet_access".into(),
            Value::Bool(opts.allow_internet_access),
        );
        put_if_some(&mut sandbox, "cpu_count", opts.cpu);
        put_if_some(&mut sandbox, "memory_mb", opts.memory_mb);
        put_if_some_string(&mut sandbox, "network_class", opts.network_class);
        if let Some(network) = opts.network {
            merge_object(&mut sandbox, network_payload(network));
        }
        put_if_some_bool(
            &mut sandbox,
            "allow_package_registry_access",
            opts.allow_package_registry_access,
        );
        if let Some(exposed_ports) = opts.exposed_ports {
            sandbox.insert("exposed_ports".into(), exposed_ports);
        }
        if let Some(team) = opts.team {
            sandbox.insert("team".into(), Value::String(team));
        }

        let response = control.post("/sandboxes", Value::Object(sandbox)).await?;
        Self::from_response(config, control, response, opts.envs)
    }

    /// Connect to an existing sandbox and return it with a fresh data-plane session.
    pub async fn connect(sandbox_id: impl ToString, connection: ConnectionOptions) -> Result<Self> {
        let sandbox_id = sandbox_id.to_string();
        let config = ConnectionConfig::new(connection);
        let control = ControlClient::new(config.clone())?;
        let info = control.get(&format!("/sandboxes/{sandbox_id}")).await?;
        let response = control
            .post(
                &format!("/sandboxes/{sandbox_id}/resume"),
                serde_json::json!({}),
            )
            .await?;
        let mut sandbox = Self::from_response(config, control, response, Default::default())?;
        if sandbox.sandbox == Value::Null {
            sandbox.sandbox = info.get("sandbox").cloned().unwrap_or(Value::Null);
        }
        Ok(sandbox)
    }

    /// Resume a paused sandbox by id.
    pub async fn resume_by_id(
        sandbox_id: impl ToString,
        connection: ConnectionOptions,
    ) -> Result<bool> {
        Self::connect(sandbox_id, connection).await?;
        Ok(true)
    }

    /// Resume this sandbox and refresh its data-plane session.
    pub async fn resume(&mut self) -> Result<bool> {
        let response = self
            .control
            .post(
                &format!("/sandboxes/{}/resume", self.sandbox_id),
                serde_json::json!({}),
            )
            .await?;
        self.refresh_from_response(response)?;
        Ok(true)
    }

    /// Pause a sandbox by id. Returns `false` if it is already paused.
    pub async fn beta_pause_by_id(
        sandbox_id: impl ToString,
        connection: ConnectionOptions,
    ) -> Result<bool> {
        let sandbox_id = sandbox_id.to_string();
        let config = ConnectionConfig::new(connection);
        let control = ControlClient::new(config)?;
        match control
            .post(
                &format!("/sandboxes/{sandbox_id}/pause"),
                serde_json::json!({}),
            )
            .await
        {
            Ok(_) => Ok(true),
            Err(Error::Conflict(_)) => Ok(false),
            Err(error) => Err(error),
        }
    }

    /// Pause this sandbox. Returns `false` if it is already paused.
    pub async fn beta_pause(&self) -> Result<bool> {
        match self
            .control
            .post(
                &format!("/sandboxes/{}/pause", self.sandbox_id),
                serde_json::json!({}),
            )
            .await
        {
            Ok(_) => Ok(true),
            Err(Error::Conflict(_)) => Ok(false),
            Err(error) => Err(error),
        }
    }

    /// Alias for `beta_pause_by_id`.
    pub async fn pause_by_id(
        sandbox_id: impl ToString,
        connection: ConnectionOptions,
    ) -> Result<bool> {
        Self::beta_pause_by_id(sandbox_id, connection).await
    }

    /// Alias for `beta_pause`.
    pub async fn pause(&self) -> Result<bool> {
        self.beta_pause().await
    }

    /// Destroy this sandbox.
    pub async fn kill(&self) -> Result<bool> {
        self.control
            .delete(&format!("/sandboxes/{}", self.sandbox_id))
            .await?;
        Ok(true)
    }

    /// Fetch sandbox metrics by id.
    pub async fn get_metrics_by_id(
        sandbox_id: impl ToString,
        connection: ConnectionOptions,
    ) -> Result<Vec<SandboxMetrics>> {
        let sandbox_id = sandbox_id.to_string();
        let config = ConnectionConfig::new(connection);
        let control = ControlClient::new(config)?;
        let payload = control
            .get(&format!("/sandboxes/{sandbox_id}/metrics"))
            .await?;
        Ok(metrics_list(payload.get("metrics").unwrap_or(&payload)))
    }

    /// Fetch latest sandbox metrics.
    pub async fn get_metrics(&self) -> Result<Vec<SandboxMetrics>> {
        let payload = self
            .control
            .get(&format!("/sandboxes/{}/metrics", self.sandbox_id))
            .await?;
        Ok(metrics_list(payload.get("metrics").unwrap_or(&payload)))
    }

    /// Create a Watasu checkpoint using snapshot naming.
    pub async fn create_snapshot(&self, opts: CreateSnapshotOptions) -> Result<SnapshotInfo> {
        let response = self
            .control
            .post(
                &format!("/sandboxes/{}/snapshots", self.sandbox_id),
                snapshot_payload(opts),
            )
            .await?;
        Ok(snapshot_info(
            response
                .get("sandbox_checkpoint")
                .or_else(|| response.get("snapshot"))
                .unwrap_or(&response),
        ))
    }

    /// List checkpoints for this sandbox using snapshot naming.
    pub async fn list_snapshots(&self) -> Result<Vec<SnapshotInfo>> {
        let payload = self
            .control
            .get(&format!("/sandboxes/{}/checkpoints", self.sandbox_id))
            .await?;
        Ok(payload
            .get("sandbox_checkpoints")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .map(|value| snapshot_info(&value))
            .collect())
    }

    /// Delete a snapshot by id.
    pub async fn delete_snapshot(&self, snapshot_id: impl ToString) -> Result<bool> {
        match self
            .control
            .delete(&format!("/sandbox_snapshots/{}", snapshot_id.to_string()))
            .await
        {
            Ok(_) => Ok(true),
            Err(Error::NotFound(_)) => Ok(false),
            Err(error) => Err(error),
        }
    }

    /// Delete a snapshot by id using explicit connection options.
    pub async fn delete_snapshot_by_id(
        snapshot_id: impl ToString,
        connection: ConnectionOptions,
    ) -> Result<bool> {
        let config = ConnectionConfig::new(connection);
        let control = ControlClient::new(config)?;
        match control
            .delete(&format!("/sandbox_snapshots/{}", snapshot_id.to_string()))
            .await
        {
            Ok(_) => Ok(true),
            Err(Error::NotFound(_)) => Ok(false),
            Err(error) => Err(error),
        }
    }

    /// Get a signed URL for uploading a file with a POST request.
    pub async fn upload_url(&self, path: &str, opts: FileUrlOptions) -> Result<String> {
        Ok(self.upload_url_info(path, opts).await?.url)
    }

    /// Get a signed URL for downloading a file with a GET request.
    pub async fn download_url(&self, path: &str, opts: FileUrlOptions) -> Result<String> {
        Ok(self.download_url_info(path, opts).await?.url)
    }

    /// Get signed upload URL metadata.
    pub async fn upload_url_info(&self, path: &str, opts: FileUrlOptions) -> Result<FileUrlInfo> {
        self.file_url_info("upload_url", path, opts).await
    }

    /// Get signed download URL metadata.
    pub async fn download_url_info(&self, path: &str, opts: FileUrlOptions) -> Result<FileUrlInfo> {
        self.file_url_info("download_url", path, opts).await
    }

    /// Atomically replace this sandbox's network egress policy.
    pub async fn update_network(&mut self, opts: NetworkUpdateOptions) -> Result<()> {
        let payload = self
            .control
            .put(
                &format!("/sandboxes/{}/network", self.sandbox_id),
                network_payload(opts),
            )
            .await?;
        if let Some(sandbox) = payload.get("sandbox") {
            self.sandbox = sandbox.clone();
        }
        Ok(())
    }

    /// Restore a checkpoint into a new sandbox and return its control-plane metadata.
    pub async fn restore(&self, opts: RestoreOptions) -> Result<SandboxInfo> {
        let mut body = serde_json::Map::new();
        body.insert("checkpoint_id".into(), Value::String(opts.checkpoint_id));
        if let Some(timeout_seconds) = opts.timeout_seconds {
            body.insert("timeout_seconds".into(), Value::from(timeout_seconds));
        }
        let payload = self
            .control
            .post(
                &format!("/sandboxes/{}/restore", self.sandbox_id),
                Value::Object(body),
            )
            .await?;
        Ok(sandbox_info(payload.get("sandbox").unwrap_or(&payload)))
    }

    /// Set this sandbox's lifetime in seconds.
    pub async fn set_timeout(&self, timeout_seconds: u64) -> Result<()> {
        self.control
            .post(
                &format!("/sandboxes/{}/timeout", self.sandbox_id),
                serde_json::json!({"timeout": timeout_seconds}),
            )
            .await?;
        Ok(())
    }

    /// Fetch latest control-plane metadata for this sandbox.
    pub async fn get_info(&self) -> Result<SandboxInfo> {
        let payload = self
            .control
            .get(&format!("/sandboxes/{}", self.sandbox_id))
            .await?;
        Ok(sandbox_info(payload.get("sandbox").unwrap_or(&payload)))
    }

    /// Return the public hostname for an exposed sandbox port.
    pub async fn get_host(&self, port: u16) -> Result<String> {
        let payload = self
            .control
            .get(&format!("/sandboxes/{}/ports/{port}", self.sandbox_id))
            .await?;
        let item = payload
            .get("sandbox_port")
            .or_else(|| payload.get("port"))
            .unwrap_or(&payload);
        if let Some(value) = item
            .get("host")
            .or_else(|| item.get("url"))
            .and_then(Value::as_str)
        {
            return Ok(host_only(value));
        }
        let token = self
            .sandbox
            .get("route_token")
            .and_then(Value::as_str)
            .ok_or_else(|| Error::Sandbox("port response did not include host or url".into()))?;
        Ok(format!(
            "p{port}-{token}.sandbox.{}",
            self.config.data_plane_domain
        ))
    }

    fn from_response(
        config: ConnectionConfig,
        control: ControlClient,
        response: Value,
        envs: serde_json::Map<String, Value>,
    ) -> Result<Self> {
        let sandbox = response
            .get("sandbox")
            .cloned()
            .unwrap_or_else(|| response.clone());
        let sandbox_id = sandbox
            .get("id")
            .or_else(|| sandbox.get("sandbox_id"))
            .map(|value| {
                value
                    .as_str()
                    .map(ToOwned::to_owned)
                    .unwrap_or_else(|| value.to_string())
            })
            .ok_or_else(|| Error::Sandbox("create response did not include sandbox id".into()))?;
        let data_plane = data_plane_from_session(response.get("session"), &config)?;
        Ok(Self {
            sandbox_id,
            files: Filesystem::new(data_plane.clone()),
            commands: Commands::new(data_plane.clone(), envs.clone()),
            pty: Pty::new(data_plane.clone()),
            git: Git::new(data_plane),
            config,
            control,
            sandbox,
            envs,
        })
    }

    fn refresh_from_response(&mut self, response: Value) -> Result<()> {
        let sandbox = response
            .get("sandbox")
            .cloned()
            .unwrap_or_else(|| response.clone());
        let data_plane = data_plane_from_session(response.get("session"), &self.config)?;
        self.files = Filesystem::new(data_plane.clone());
        self.commands = Commands::new(data_plane.clone(), self.envs.clone());
        self.pty = Pty::new(data_plane.clone());
        self.git = Git::new(data_plane);
        self.sandbox = sandbox;
        Ok(())
    }

    async fn file_url_info(
        &self,
        route: &str,
        path: &str,
        opts: FileUrlOptions,
    ) -> Result<FileUrlInfo> {
        let mut body = serde_json::Map::new();
        body.insert("path".into(), Value::String(path.to_string()));
        put_if_some_string(&mut body, "user", opts.user);
        put_if_some(
            &mut body,
            "use_signature_expiration",
            opts.use_signature_expiration,
        );
        put_if_some(&mut body, "expires_in_seconds", opts.expires_in_seconds);
        let payload = self
            .control
            .post(
                &format!("/sandboxes/{}/files/{route}", self.sandbox_id),
                Value::Object(body),
            )
            .await?;
        Ok(file_url_info(payload.get("file_url").unwrap_or(&payload)))
    }
}

fn data_plane_from_session(
    session: Option<&Value>,
    config: &ConnectionConfig,
) -> Result<DataPlaneClient> {
    let session = session.ok_or_else(|| {
        Error::Sandbox("sandbox session is required for data-plane operations".into())
    })?;
    let token = session
        .get("token")
        .or_else(|| session.get("access_token"))
        .and_then(Value::as_str)
        .ok_or_else(|| {
            Error::Sandbox("sandbox session did not include data_plane_url and token".into())
        })?;
    let url = session
        .get("data_plane_url")
        .and_then(Value::as_str)
        .ok_or_else(|| {
            Error::Sandbox("sandbox session did not include data_plane_url and token".into())
        })?;
    DataPlaneClient::new(url.to_string(), token.to_string(), config)
}

fn sandbox_info(value: &Value) -> SandboxInfo {
    SandboxInfo {
        sandbox_id: value
            .get("id")
            .or_else(|| value.get("sandbox_id"))
            .map(|item| {
                item.as_str()
                    .map(ToOwned::to_owned)
                    .unwrap_or_else(|| item.to_string())
            })
            .unwrap_or_default(),
        template_id: value
            .get("template_id")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned)
            .or_else(|| {
                value
                    .get("template")
                    .and_then(|template| template.get("slug"))
                    .and_then(Value::as_str)
                    .map(ToOwned::to_owned)
            }),
        template_version_id: value.get("template_version_id").and_then(Value::as_u64),
        state: value
            .get("state")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned),
        metadata: value
            .get("metadata")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default(),
        started_at: value
            .get("started_at")
            .or_else(|| value.get("created_at"))
            .and_then(Value::as_str)
            .map(ToOwned::to_owned),
        end_at: value
            .get("end_at")
            .or_else(|| value.get("deadline_at"))
            .and_then(Value::as_str)
            .map(ToOwned::to_owned),
    }
}

fn metrics_list(value: &Value) -> Vec<SandboxMetrics> {
    if let Some(items) = value.as_array() {
        items.iter().map(metrics_info).collect()
    } else {
        vec![metrics_info(value)]
    }
}

fn metrics_info(value: &Value) -> SandboxMetrics {
    SandboxMetrics {
        sandbox_id: string_value(value, &["sandbox_id", "sandboxId"]),
        state: string_value(value, &["state"]),
        node: string_value(value, &["node"]),
        backend: string_value(value, &["backend"]),
        cpu_count: u64_value(value, &["cpu_count", "cpuCount"]),
        memory_mb: u64_value(value, &["memory_mb", "memoryMb"]),
        raw: value.clone(),
    }
}

fn snapshot_payload(opts: CreateSnapshotOptions) -> Value {
    let mut body = serde_json::Map::new();
    if let Some(name) = opts.name {
        body.insert("name".into(), Value::String(name));
    }
    if !opts.metadata.is_empty() {
        body.insert("metadata".into(), Value::Object(opts.metadata));
    }
    if let Some(expires_at) = opts.expires_at {
        body.insert("expires_at".into(), Value::String(expires_at));
    }
    if let Some(quiesce_mode) = opts.quiesce_mode {
        body.insert("quiesce_mode".into(), Value::String(quiesce_mode));
    }
    Value::Object(body)
}

fn network_payload(opts: NetworkUpdateOptions) -> Value {
    let mut body = serde_json::Map::new();
    put_if_some_bool(
        &mut body,
        "allow_internet_access",
        opts.allow_internet_access,
    );
    put_if_some_bool(
        &mut body,
        "allow_package_registry_access",
        opts.allow_package_registry_access,
    );
    put_if_some_bool(&mut body, "allow_public_traffic", opts.allow_public_traffic);
    put_if_some_string(&mut body, "egress_profile", opts.egress_profile);
    put_if_some_string(&mut body, "network_class", opts.network_class);
    put_if_non_empty_strings(&mut body, "allow_out", opts.allow_out);
    put_if_non_empty_strings(&mut body, "deny_out", opts.deny_out);
    put_if_non_empty_strings(&mut body, "egress_profiles", opts.egress_profiles);
    Value::Object(body)
}

fn snapshot_info(value: &Value) -> SnapshotInfo {
    SnapshotInfo {
        snapshot_id: string_value(
            value,
            &[
                "snapshot_id",
                "snapshotId",
                "checkpoint_id",
                "checkpointId",
                "id",
            ],
        )
        .unwrap_or_default(),
        sandbox_id: string_value(value, &["sandbox_id", "sandboxId"]),
        name: string_value(value, &["name"]),
        status: string_value(value, &["status"]),
        size_bytes: u64_value(value, &["size_bytes", "sizeBytes"]),
        created_at: string_value(value, &["created_at", "createdAt"]),
        expires_at: string_value(value, &["expires_at", "expiresAt"]),
        raw: value.clone(),
    }
}

fn file_url_info(value: &Value) -> FileUrlInfo {
    FileUrlInfo {
        method: string_value(value, &["method"]).unwrap_or_default(),
        path: string_value(value, &["path"]).unwrap_or_default(),
        url: string_value(value, &["url"]).unwrap_or_default(),
        expires_at: string_value(value, &["expires_at", "expiresAt"]),
        raw: value.clone(),
    }
}

fn string_value(value: &Value, keys: &[&str]) -> Option<String> {
    keys.iter()
        .find_map(|key| value.get(*key))
        .and_then(|value| {
            value
                .as_str()
                .map(ToOwned::to_owned)
                .or_else(|| value.as_u64().map(|number| number.to_string()))
        })
}

fn u64_value(value: &Value, keys: &[&str]) -> Option<u64> {
    keys.iter()
        .find_map(|key| value.get(*key))
        .and_then(Value::as_u64)
}

fn put_if_some(map: &mut serde_json::Map<String, Value>, key: &str, value: Option<u64>) {
    if let Some(value) = value {
        map.insert(key.to_string(), Value::from(value));
    }
}

fn put_if_some_string(map: &mut serde_json::Map<String, Value>, key: &str, value: Option<String>) {
    if let Some(value) = value {
        map.insert(key.to_string(), Value::String(value));
    }
}

fn put_if_some_bool(map: &mut serde_json::Map<String, Value>, key: &str, value: Option<bool>) {
    if let Some(value) = value {
        map.insert(key.to_string(), Value::Bool(value));
    }
}

fn put_if_non_empty_strings(
    map: &mut serde_json::Map<String, Value>,
    key: &str,
    values: Vec<String>,
) {
    if !values.is_empty() {
        map.insert(
            key.to_string(),
            Value::Array(values.into_iter().map(Value::String).collect()),
        );
    }
}

fn merge_object(target: &mut serde_json::Map<String, Value>, value: Value) {
    if let Value::Object(entries) = value {
        target.extend(entries);
    }
}

fn host_only(value: &str) -> String {
    url::Url::parse(value)
        .map(|url| url.host_str().unwrap_or(value).to_string())
        .unwrap_or_else(|_| value.split('/').next().unwrap_or(value).to_string())
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use crate::process_socket::{decode_runtime_data, encode_runtime_data};

    use super::{metrics_list, network_payload, snapshot_info, NetworkUpdateOptions};

    #[test]
    fn runtime_base64_helpers_match_protocol() {
        assert_eq!(decode_runtime_data("NAo="), "4\n");
        assert_eq!(encode_runtime_data("hi\n"), "aGkK");
    }

    #[test]
    fn maps_metrics_payload() {
        let metrics =
            metrics_list(&json!({"sandbox_id": 42, "state": "ready", "backend": "firecracker"}));

        assert_eq!(metrics[0].sandbox_id.as_deref(), Some("42"));
        assert_eq!(metrics[0].state.as_deref(), Some("ready"));
        assert_eq!(metrics[0].backend.as_deref(), Some("firecracker"));
    }

    #[test]
    fn maps_checkpoint_payload_as_snapshot() {
        let snapshot = snapshot_info(&json!({
            "id": 7,
            "sandbox_id": 42,
            "name": "ready",
            "status": "pending",
            "size_bytes": 123
        }));

        assert_eq!(snapshot.snapshot_id, "7");
        assert_eq!(snapshot.sandbox_id.as_deref(), Some("42"));
        assert_eq!(snapshot.size_bytes, Some(123));
    }

    #[test]
    fn maps_network_update_payload_to_snake_case() {
        let payload = network_payload(NetworkUpdateOptions {
            allow_internet_access: Some(false),
            allow_package_registry_access: Some(true),
            allow_out: vec!["pypi.org:443".to_string()],
            deny_out: vec!["10.0.0.0/8".to_string()],
            ..Default::default()
        });

        assert_eq!(
            payload,
            json!({
                "allow_internet_access": false,
                "allow_package_registry_access": true,
                "allow_out": ["pypi.org:443"],
                "deny_out": ["10.0.0.0/8"]
            })
        );
    }
}
