use serde_json::Value;

use crate::commands::Commands;
use crate::config::{ConnectionConfig, ConnectionOptions};
use crate::error::{Error, Result};
use crate::filesystem::Filesystem;
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

/// Running Watasu sandbox with ready `commands` and `files` helpers.
pub struct Sandbox {
    /// Sandbox id.
    pub sandbox_id: String,
    /// Command runner for this sandbox.
    pub commands: Commands,
    /// Filesystem helper for this sandbox.
    pub files: Filesystem,
    config: ConnectionConfig,
    control: ControlClient,
    sandbox: Value,
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
                &format!("/sandboxes/{sandbox_id}/connect"),
                serde_json::json!({}),
            )
            .await?;
        let mut sandbox = Self::from_response(config, control, response, Default::default())?;
        if sandbox.sandbox == Value::Null {
            sandbox.sandbox = info.get("sandbox").cloned().unwrap_or(Value::Null);
        }
        Ok(sandbox)
    }

    /// Destroy this sandbox.
    pub async fn kill(&self) -> Result<bool> {
        self.control
            .delete(&format!("/sandboxes/{}", self.sandbox_id))
            .await?;
        Ok(true)
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
            commands: Commands::new(data_plane, envs),
            config,
            control,
            sandbox,
        })
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

fn host_only(value: &str) -> String {
    url::Url::parse(value)
        .map(|url| url.host_str().unwrap_or(value).to_string())
        .unwrap_or_else(|_| value.split('/').next().unwrap_or(value).to_string())
}

#[cfg(test)]
mod tests {
    use crate::process_socket::{decode_runtime_data, encode_runtime_data};

    #[test]
    fn runtime_base64_helpers_match_protocol() {
        assert_eq!(decode_runtime_data("NAo="), "4\n");
        assert_eq!(encode_runtime_data("hi\n"), "aGkK");
    }
}
