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
    /// Explicit template version id. Sent as `template_version_id` on the wire.
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
    /// Timeout lifecycle policy.
    pub lifecycle: Option<SandboxLifecycle>,
    /// Whether package registry egress should be allowed.
    pub allow_package_registry_access: Option<bool>,
    /// Persistent volumes to mount into the sandbox.
    pub volume_mounts: Vec<VolumeMount>,
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
            lifecycle: None,
            allow_package_registry_access: None,
            volume_mounts: Vec::new(),
            exposed_ports: None,
        }
    }
}

/// A persistent volume mounted at a guest path when a sandbox starts.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct VolumeMount {
    /// Volume name.
    pub name: String,
    /// Absolute path inside the guest.
    pub path: String,
}

impl VolumeMount {
    /// Create a volume mount from a guest path and volume name.
    pub fn new(path: impl Into<String>, name: impl Into<String>) -> Self {
        Self {
            path: path.into(),
            name: name.into(),
        }
    }
}

/// Timeout lifecycle policy used when creating a sandbox.
#[derive(Clone, Debug)]
pub struct SandboxLifecycle {
    /// Action to take when the sandbox timeout expires: `kill` or `pause`.
    pub on_timeout: String,
    /// Whether data-plane access should resume a paused sandbox automatically.
    pub auto_resume: bool,
}

impl SandboxLifecycle {
    /// Kill the sandbox and release all resources when the timeout expires.
    pub fn kill() -> Self {
        Self {
            on_timeout: "kill".to_string(),
            auto_resume: false,
        }
    }

    /// Pause the sandbox and retain its disk when the timeout expires.
    pub fn pause(auto_resume: bool) -> Self {
        Self {
            on_timeout: "pause".to_string(),
            auto_resume,
        }
    }
}

/// Query filters for listing sandboxes.
#[derive(Clone, Debug, Default)]
pub struct SandboxListQuery {
    /// Metadata key-value pairs that must be present on the sandbox.
    pub metadata: serde_json::Map<String, Value>,
    /// Lifecycle states to include. The API accepts `running` and `paused`
    /// aliases as well as Watasu-native states.
    pub state: Vec<String>,
}

/// Options for `Sandbox::list`.
#[derive(Clone, Debug, Default)]
pub struct ListOptions {
    /// Connection options used for the control-plane request.
    pub connection: ConnectionOptions,
    /// Optional filters.
    pub query: Option<SandboxListQuery>,
    /// Maximum number of sandboxes to return.
    pub limit: Option<u64>,
    /// Pagination cursor returned by the previous page.
    pub next_token: Option<String>,
    /// Team slug to list within.
    pub team: Option<String>,
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
    /// Timeout lifecycle policy returned by the API.
    pub lifecycle: Option<SandboxInfoLifecycle>,
    /// Persistent volumes mounted into the sandbox.
    pub volume_mounts: Vec<VolumeMount>,
    /// User metadata.
    pub metadata: serde_json::Map<String, Value>,
    /// Creation timestamp.
    pub started_at: Option<String>,
    /// Deadline timestamp.
    pub end_at: Option<String>,
}

/// Timeout lifecycle policy returned by sandbox info.
#[derive(Clone, Debug, Default)]
pub struct SandboxInfoLifecycle {
    /// Action taken when the sandbox timeout expires.
    pub on_timeout: Option<String>,
    /// Whether data-plane access resumes a paused sandbox automatically.
    pub auto_resume: bool,
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

/// One page of sandbox list results.
#[derive(Clone, Debug, Default)]
pub struct SandboxListPage {
    /// Sandboxes returned on this page.
    pub sandboxes: Vec<SandboxInfo>,
    /// Cursor for the next page, when more results exist.
    pub next_token: Option<String>,
}

/// Options for listing snapshots.
#[derive(Clone, Debug, Default)]
pub struct SnapshotListOptions {
    /// Connection options used for the control-plane request.
    pub connection: ConnectionOptions,
    /// Optional source sandbox id filter.
    pub sandbox_id: Option<String>,
    /// Maximum number of snapshots to return.
    pub limit: Option<u64>,
    /// Pagination cursor returned by the previous page.
    pub next_token: Option<String>,
}

/// One page of snapshot list results.
#[derive(Clone, Debug, Default)]
pub struct SnapshotListPage {
    /// Snapshots returned on this page.
    pub snapshots: Vec<SnapshotInfo>,
    /// Cursor for the next page, when more results exist.
    pub next_token: Option<String>,
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
    mcp_token: Option<String>,
}

impl Sandbox {
    /// Conventional MCP gateway port.
    pub const MCP_PORT: u16 = 50005;

    /// Create a sandbox and return it only after the API supplies a data-plane session.
    pub async fn create(opts: CreateOptions) -> Result<Self> {
        let config = ConnectionConfig::new(opts.connection.clone());
        let control = ControlClient::new(config.clone())?;
        let envs = opts.envs.clone();
        let response = control.post("/sandboxes", create_payload(&opts)?).await?;
        Self::from_response(config, control, response, envs)
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

    /// List sandboxes visible to the configured API token.
    pub async fn list(opts: ListOptions) -> Result<SandboxListPage> {
        let path = sandbox_list_path(&opts);
        let config = ConnectionConfig::new(opts.connection);
        let control = ControlClient::new(config)?;
        let payload = control.get(&path).await?;
        Ok(SandboxListPage {
            sandboxes: payload
                .get("sandboxes")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default()
                .into_iter()
                .map(|value| sandbox_info(&value))
                .collect(),
            next_token: string_value(&payload, &["next_token", "nextToken"]),
        })
    }

    /// List snapshots visible to the configured API token.
    pub async fn list_snapshots_page(opts: SnapshotListOptions) -> Result<SnapshotListPage> {
        let path = snapshot_list_path(&opts);
        let config = ConnectionConfig::new(opts.connection);
        let control = ControlClient::new(config)?;
        let payload = control.get(&path).await?;
        Ok(SnapshotListPage {
            snapshots: payload
                .get("snapshots")
                .or_else(|| payload.get("sandbox_checkpoints"))
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default()
                .into_iter()
                .map(|value| snapshot_info(&value))
                .collect(),
            next_token: string_value(&payload, &["next_token", "nextToken"]),
        })
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

    /// Pause a sandbox by id.
    pub async fn pause_by_id(
        sandbox_id: impl ToString,
        connection: ConnectionOptions,
    ) -> Result<bool> {
        Self::beta_pause_by_id(sandbox_id, connection).await
    }

    /// Pause this sandbox.
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
                &sandbox_network_path(&self.sandbox_id),
                network_payload(opts),
            )
            .await?;
        if let Some(sandbox) = payload.get("sandbox") {
            self.sandbox = sandbox.clone();
        }
        Ok(())
    }

    /// Atomically replace a sandbox network egress policy by id.
    pub async fn update_network_by_id(
        sandbox_id: impl ToString,
        opts: NetworkUpdateOptions,
        connection: ConnectionOptions,
    ) -> Result<()> {
        let sandbox_id = sandbox_id.to_string();
        let config = ConnectionConfig::new(connection);
        let control = ControlClient::new(config)?;
        control
            .put(&sandbox_network_path(&sandbox_id), network_payload(opts))
            .await?;
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

    /// Return the conventional MCP URL for this sandbox.
    pub async fn get_mcp_url(&self) -> Result<String> {
        Ok(format!(
            "https://{}/mcp",
            self.get_host(Self::MCP_PORT).await?
        ))
    }

    /// Return the MCP gateway token when the sandbox contains one.
    pub async fn get_mcp_token(&mut self) -> Result<Option<String>> {
        if self.mcp_token.is_some() {
            return Ok(self.mcp_token.clone());
        }
        match self.files.read_text("/etc/mcp-gateway/.token").await {
            Ok(token) => {
                let token = token.trim().to_string();
                if token.is_empty() {
                    Ok(None)
                } else {
                    self.mcp_token = Some(token.clone());
                    Ok(Some(token))
                }
            }
            Err(Error::FileNotFound(_)) | Err(Error::NotFound(_)) => Ok(None),
            Err(error) => Err(error),
        }
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
            mcp_token: None,
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

fn sandbox_list_path(opts: &ListOptions) -> String {
    let mut serializer = url::form_urlencoded::Serializer::new(String::new());
    if let Some(team) = &opts.team {
        serializer.append_pair("team", team);
    }
    if let Some(limit) = opts.limit {
        serializer.append_pair("limit", &limit.to_string());
    }
    if let Some(next_token) = &opts.next_token {
        serializer.append_pair("next_token", next_token);
    }
    if let Some(query) = &opts.query {
        for (key, value) in &query.metadata {
            serializer.append_pair(&format!("query[metadata][{key}]"), &query_value(value));
        }
        for state in &query.state {
            serializer.append_pair("query[state][]", state);
        }
    }
    let query = serializer.finish();
    if query.is_empty() {
        "/sandboxes".to_string()
    } else {
        format!("/sandboxes?{query}")
    }
}

fn query_value(value: &Value) -> String {
    value
        .as_str()
        .map(ToOwned::to_owned)
        .unwrap_or_else(|| value.to_string())
}

fn create_payload(opts: &CreateOptions) -> Result<Value> {
    let mut sandbox = serde_json::Map::new();
    if let Some(version_id) = opts.template_version_id {
        sandbox.insert("template_version_id".into(), Value::from(version_id));
    } else {
        sandbox.insert("template".into(), Value::String(opts.template.clone()));
    }
    sandbox.insert("timeout".into(), Value::from(opts.timeout_seconds));
    sandbox.insert("metadata".into(), Value::Object(opts.metadata.clone()));
    sandbox.insert("envs".into(), Value::Object(opts.envs.clone()));
    sandbox.insert(
        "allow_internet_access".into(),
        Value::Bool(opts.allow_internet_access),
    );
    put_if_some(&mut sandbox, "cpu_count", opts.cpu);
    put_if_some(&mut sandbox, "memory_mb", opts.memory_mb);
    put_if_some_string(&mut sandbox, "network_class", opts.network_class.clone());
    if let Some(network) = opts.network.clone() {
        merge_object(&mut sandbox, network_payload(network));
    }
    if let Some(lifecycle) = opts.lifecycle.clone() {
        sandbox.insert("lifecycle".into(), lifecycle_payload(lifecycle)?);
    }
    put_if_non_empty_volume_mounts(&mut sandbox, opts.volume_mounts.clone());
    put_if_some_bool(
        &mut sandbox,
        "allow_package_registry_access",
        opts.allow_package_registry_access,
    );
    if let Some(exposed_ports) = opts.exposed_ports.clone() {
        sandbox.insert("exposed_ports".into(), exposed_ports);
    }
    if let Some(team) = opts.team.clone() {
        sandbox.insert("team".into(), Value::String(team));
    }

    Ok(Value::Object(sandbox))
}

fn snapshot_list_path(opts: &SnapshotListOptions) -> String {
    let mut serializer = url::form_urlencoded::Serializer::new(String::new());
    if let Some(sandbox_id) = &opts.sandbox_id {
        serializer.append_pair("sandbox_id", sandbox_id);
    }
    if let Some(limit) = opts.limit {
        serializer.append_pair("limit", &limit.to_string());
    }
    if let Some(next_token) = &opts.next_token {
        serializer.append_pair("next_token", next_token);
    }
    let query = serializer.finish();
    if query.is_empty() {
        "/sandbox_snapshots".to_string()
    } else {
        format!("/sandbox_snapshots?{query}")
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
    let session_url = session
        .get("data_plane_url")
        .and_then(Value::as_str)
        .map(str::to_string);
    let url = config.sandbox_url.clone().or(session_url).ok_or_else(|| {
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
        lifecycle: sandbox_lifecycle_info(value.get("lifecycle")),
        volume_mounts: sandbox_volume_mounts_info(value),
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

fn lifecycle_payload(lifecycle: SandboxLifecycle) -> Result<Value> {
    if lifecycle.auto_resume && lifecycle.on_timeout != "pause" {
        return Err(Error::InvalidArgument(
            "lifecycle.auto_resume can only be true when lifecycle.on_timeout is 'pause'".into(),
        ));
    }

    Ok(serde_json::json!({
        "on_timeout": lifecycle.on_timeout,
        "auto_resume": lifecycle.auto_resume,
    }))
}

fn sandbox_volume_mounts_info(value: &Value) -> Vec<VolumeMount> {
    value
        .get("volume_mounts")
        .or_else(|| value.get("volumeMounts"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|entry| {
            let name = entry.get("name").and_then(Value::as_str)?;
            let path = entry.get("path").and_then(Value::as_str)?;
            Some(VolumeMount::new(path, name))
        })
        .collect()
}

fn sandbox_lifecycle_info(value: Option<&Value>) -> Option<SandboxInfoLifecycle> {
    let lifecycle = value.and_then(Value::as_object)?;
    let on_timeout = lifecycle
        .get("on_timeout")
        .or_else(|| lifecycle.get("onTimeout"))
        .and_then(Value::as_str)
        .map(ToOwned::to_owned);
    let auto_resume = lifecycle
        .get("auto_resume")
        .or_else(|| lifecycle.get("autoResume"))
        .and_then(Value::as_bool)
        .unwrap_or(false);

    if on_timeout.is_none() && !auto_resume {
        None
    } else {
        Some(SandboxInfoLifecycle {
            on_timeout,
            auto_resume,
        })
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

fn sandbox_network_path(sandbox_id: &str) -> String {
    format!("/sandboxes/{sandbox_id}/network")
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

fn put_if_non_empty_volume_mounts(
    map: &mut serde_json::Map<String, Value>,
    volume_mounts: Vec<VolumeMount>,
) {
    if volume_mounts.is_empty() {
        return;
    }

    let mounts = volume_mounts
        .into_iter()
        .map(|mount| {
            serde_json::json!({
                "name": mount.name,
                "path": mount.path,
            })
        })
        .collect();

    map.insert("volume_mounts".to_string(), Value::Array(mounts));
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
    use serde_json::{json, Value};

    use crate::config::{ConnectionConfig, ConnectionOptions};
    use crate::process_socket::{decode_runtime_data, encode_runtime_data};

    use super::{
        create_payload, data_plane_from_session, metrics_list, network_payload,
        put_if_non_empty_volume_mounts, sandbox_info, sandbox_list_path, sandbox_network_path,
        snapshot_info, snapshot_list_path, CreateOptions, ListOptions, NetworkUpdateOptions,
        SandboxListQuery, SnapshotListOptions, VolumeMount,
    };

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

    #[test]
    fn sandbox_create_uses_template_slug_payload_by_default() {
        let payload = create_payload(&CreateOptions {
            template: "python".to_string(),
            timeout_seconds: 1_200,
            ..CreateOptions::default()
        })
        .expect("create payload");

        assert_eq!(
            payload,
            json!({
                "template": "python",
                "timeout": 1200,
                "metadata": {},
                "envs": {},
                "allow_internet_access": true
            })
        );
    }

    #[test]
    fn sandbox_create_sends_template_version_id_as_field() {
        let payload = create_payload(&CreateOptions {
            template: "base".to_string(),
            template_version_id: Some(19),
            timeout_seconds: 1_200,
            ..CreateOptions::default()
        })
        .expect("create payload");

        assert_eq!(payload["template_version_id"], json!(19));
        assert!(payload.get("template").is_none());
        assert_eq!(payload["timeout"], json!(1_200));
    }

    #[test]
    fn maps_volume_mounts_to_snake_case_create_payload() {
        let mut payload = serde_json::Map::new();
        put_if_non_empty_volume_mounts(
            &mut payload,
            vec![
                VolumeMount::new("/workspace/cache", "cache"),
                VolumeMount::new("/data/models", "models"),
            ],
        );

        assert_eq!(
            Value::Object(payload),
            json!({
                "volume_mounts": [
                    {"name": "cache", "path": "/workspace/cache"},
                    {"name": "models", "path": "/data/models"}
                ]
            })
        );
    }

    #[test]
    fn maps_volume_mounts_from_sandbox_info() {
        let info = sandbox_info(&json!({
            "id": "sandbox-1",
            "volume_mounts": [
                {"name": "cache", "path": "/workspace/cache"},
                {"name": "models", "path": "/data/models"}
            ]
        }));

        assert_eq!(
            info.volume_mounts,
            vec![
                VolumeMount::new("/workspace/cache", "cache"),
                VolumeMount::new("/data/models", "models")
            ]
        );
    }

    #[test]
    fn builds_sandbox_network_update_path() {
        assert_eq!(
            sandbox_network_path("network-sandbox"),
            "/sandboxes/network-sandbox/network"
        );
    }

    #[test]
    fn builds_sandbox_list_query_path() {
        let mut metadata = serde_json::Map::new();
        metadata.insert("purpose".to_string(), Value::String("ci".to_string()));

        let path = sandbox_list_path(&ListOptions {
            team: Some("watasu".to_string()),
            limit: Some(1),
            next_token: Some("2".to_string()),
            query: Some(SandboxListQuery {
                metadata,
                state: vec!["running".to_string()],
            }),
            ..Default::default()
        });

        assert_eq!(
            path,
            "/sandboxes?team=watasu&limit=1&next_token=2&query%5Bmetadata%5D%5Bpurpose%5D=ci&query%5Bstate%5D%5B%5D=running"
        );
    }

    #[test]
    fn sandbox_url_override_replaces_session_data_plane_url() {
        let config = ConnectionConfig::new(ConnectionOptions {
            sandbox_url: Some("http://localhost:49983".to_string()),
            ..Default::default()
        });
        let client = data_plane_from_session(
            Some(&json!({
                "data_plane_url": "https://token.sandbox.watasuhost.com",
                "token": "data"
            })),
            &config,
        )
        .expect("data-plane client");

        assert_eq!(client.base_url, "http://localhost:49983");
    }

    #[test]
    fn builds_snapshot_list_query_path() {
        let path = snapshot_list_path(&SnapshotListOptions {
            sandbox_id: Some("sandbox-1".to_string()),
            limit: Some(1),
            next_token: Some("2".to_string()),
            ..Default::default()
        });

        assert_eq!(
            path,
            "/sandbox_snapshots?sandbox_id=sandbox-1&limit=1&next_token=2"
        );
    }
}
