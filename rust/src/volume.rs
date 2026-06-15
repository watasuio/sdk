use base64::Engine;
use serde_json::Value;

use crate::config::{ConnectionConfig, ConnectionOptions};
use crate::error::{Error, Result};
use crate::transport::ControlClient;

/// Control-plane metadata for a persistent Watasu volume.
#[derive(Clone, Debug, Default)]
pub struct VolumeInfo {
    /// Volume id.
    pub volume_id: String,
    /// Volume name.
    pub name: String,
    /// Current lifecycle state.
    pub state: Option<String>,
    /// Volume access token returned by the API.
    pub token: Option<String>,
    /// Configured size in MiB.
    pub size_mb: Option<u64>,
    /// Actual size in bytes.
    pub size_bytes: Option<u64>,
    /// Runtime node that owns the local volume image.
    pub node: Option<String>,
    /// User metadata.
    pub metadata: serde_json::Map<String, Value>,
    /// Creation timestamp.
    pub created_at: Option<String>,
    /// Last update timestamp.
    pub updated_at: Option<String>,
    /// Full raw API payload.
    pub raw: Value,
}

/// File or directory metadata returned by volume content operations.
#[derive(Clone, Debug, Default)]
pub struct VolumeEntryStat {
    /// Absolute path inside the volume.
    pub path: String,
    /// Basename.
    pub name: String,
    /// Entry type: `file`, `directory`, `symlink`, or another runtime value.
    pub entry_type: String,
    /// Size in bytes.
    pub size: Option<u64>,
    /// POSIX mode bits.
    pub mode: Option<u64>,
    /// POSIX user id.
    pub uid: Option<u64>,
    /// POSIX group id.
    pub gid: Option<u64>,
    /// Raw API payload.
    pub raw: Value,
}

/// Options for creating a persistent volume.
#[derive(Clone, Debug, Default)]
pub struct VolumeCreateOptions {
    /// Connection options used for the control-plane request.
    pub connection: ConnectionOptions,
    /// Optional team slug.
    pub team: Option<String>,
}

/// Options for listing persistent volumes.
#[derive(Clone, Debug, Default)]
pub struct VolumeListOptions {
    /// Connection options used for the control-plane request.
    pub connection: ConnectionOptions,
    /// Optional team slug.
    pub team: Option<String>,
}

/// Options for writing a file or creating a directory in a detached volume.
#[derive(Clone, Debug, Default)]
pub struct VolumeWriteOptions {
    /// POSIX user id.
    pub uid: Option<u64>,
    /// POSIX group id.
    pub gid: Option<u64>,
    /// POSIX mode string such as `0644`.
    pub mode: Option<String>,
    /// Whether parents should be created when supported.
    pub force: Option<bool>,
}

/// Persistent volume that can be mounted into sandboxes and edited while detached.
#[derive(Clone)]
pub struct Volume {
    /// Volume id.
    pub volume_id: String,
    /// Volume name.
    pub name: String,
    /// Volume access token returned by the API.
    pub token: Option<String>,
    control: ControlClient,
}

impl Volume {
    /// Create a persistent volume and return a connected SDK object.
    pub async fn create(name: impl Into<String>, opts: VolumeCreateOptions) -> Result<Self> {
        let config = ConnectionConfig::new(opts.connection);
        let control = ControlClient::new(config.clone())?;
        let mut body = serde_json::Map::new();
        body.insert("name".to_string(), Value::String(name.into()));
        if let Some(team) = opts.team {
            body.insert("team".to_string(), Value::String(team));
        }
        let payload = control.post("/volumes", Value::Object(body)).await?;
        volume_from_payload(payload, config, control)
    }

    /// Connect to an existing volume by id or name.
    pub async fn connect(volume_id: impl ToString, connection: ConnectionOptions) -> Result<Self> {
        let config = ConnectionConfig::new(connection);
        let control = ControlClient::new(config.clone())?;
        let payload = control
            .get(&format!("/volumes/{}", volume_id.to_string()))
            .await?;
        volume_from_payload(payload, config, control)
    }

    /// Fetch metadata for an existing volume by id or name.
    pub async fn get_info_by_id(
        volume_id: impl ToString,
        connection: ConnectionOptions,
    ) -> Result<VolumeInfo> {
        let config = ConnectionConfig::new(connection);
        let control = ControlClient::new(config)?;
        let payload = control
            .get(&format!("/volumes/{}", volume_id.to_string()))
            .await?;
        volume_info(payload.get("volume").unwrap_or(&payload))
    }

    /// List volumes visible to the configured API key.
    pub async fn list(opts: VolumeListOptions) -> Result<Vec<VolumeInfo>> {
        let path = volume_list_path(&opts);
        let config = ConnectionConfig::new(opts.connection);
        let control = ControlClient::new(config)?;
        let payload = control.get(&path).await?;
        Ok(payload
            .get("volumes")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|item| volume_info(item).ok())
            .collect())
    }

    /// Destroy a volume by id or name. Returns `false` when it does not exist.
    pub async fn destroy_by_id(
        volume_id: impl ToString,
        connection: ConnectionOptions,
    ) -> Result<bool> {
        let config = ConnectionConfig::new(connection);
        let control = ControlClient::new(config)?;
        match control
            .delete(&format!("/volumes/{}", volume_id.to_string()))
            .await
        {
            Ok(_) => Ok(true),
            Err(Error::NotFound(_)) => Ok(false),
            Err(error) => Err(error),
        }
    }

    /// Fetch this volume metadata.
    pub async fn get_info(&self) -> Result<VolumeInfo> {
        let payload = self
            .control
            .get(&format!("/volumes/{}", self.volume_id))
            .await?;
        volume_info(payload.get("volume").unwrap_or(&payload))
    }

    /// Fetch metadata for a path inside this volume.
    pub async fn get_path_info(&self, path: impl AsRef<str>) -> Result<VolumeEntryStat> {
        let payload = self
            .control
            .get(&format!(
                "/volumes/{}/path?{}",
                self.volume_id,
                query(&[("path", Some(path.as_ref().to_string()))])
            ))
            .await?;
        volume_entry(payload.get("file").unwrap_or(&payload))
    }

    /// List files and directories under `path`.
    pub async fn list_files(
        &self,
        path: impl AsRef<str>,
        depth: Option<u64>,
    ) -> Result<Vec<VolumeEntryStat>> {
        let payload = self
            .control
            .get(&format!(
                "/volumes/{}/directories?{}",
                self.volume_id,
                query(&[
                    ("path", Some(path.as_ref().to_string())),
                    ("depth", depth.map(|value| value.to_string()))
                ])
            ))
            .await?;
        Ok(payload
            .get("entries")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|item| volume_entry(item).ok())
            .collect())
    }

    /// Create a directory inside the detached volume.
    pub async fn make_dir(
        &self,
        path: impl Into<String>,
        opts: VolumeWriteOptions,
    ) -> Result<VolumeEntryStat> {
        let payload = self
            .control
            .post(
                &format!("/volumes/{}/directories", self.volume_id),
                write_body(path.into(), None, opts),
            )
            .await?;
        volume_entry(payload.get("file").unwrap_or(&payload))
    }

    /// Return whether a path exists inside the detached volume.
    pub async fn exists(&self, path: impl AsRef<str>) -> Result<bool> {
        match self.get_path_info(path).await {
            Ok(_) => Ok(true),
            Err(Error::NotFound(_)) => Ok(false),
            Err(error) => Err(error),
        }
    }

    /// Update ownership or mode metadata for a path.
    pub async fn update_metadata(
        &self,
        path: impl Into<String>,
        opts: VolumeWriteOptions,
    ) -> Result<VolumeEntryStat> {
        let payload = self
            .control
            .patch(
                &format!("/volumes/{}/path", self.volume_id),
                write_body(path.into(), None, opts),
            )
            .await?;
        volume_entry(payload.get("file").unwrap_or(&payload))
    }

    /// Read a file from the detached volume as bytes.
    pub async fn read_file(&self, path: impl AsRef<str>) -> Result<Vec<u8>> {
        let payload = self
            .control
            .get(&format!(
                "/volumes/{}/files?{}",
                self.volume_id,
                query(&[("path", Some(path.as_ref().to_string()))])
            ))
            .await?;
        let file = payload.get("file").unwrap_or(&payload);
        let content = file
            .get("content_b64")
            .and_then(Value::as_str)
            .unwrap_or_default();
        base64::engine::general_purpose::STANDARD
            .decode(content)
            .map_err(|error| Error::Sandbox(error.to_string()))
    }

    /// Write a file into the detached volume.
    pub async fn write_file(
        &self,
        path: impl Into<String>,
        data: impl AsRef<[u8]>,
        opts: VolumeWriteOptions,
    ) -> Result<VolumeEntryStat> {
        let content = base64::engine::general_purpose::STANDARD.encode(data.as_ref());
        let payload = self
            .control
            .put(
                &format!("/volumes/{}/files", self.volume_id),
                write_body(path.into(), Some(content), opts),
            )
            .await?;
        volume_entry(payload.get("file").unwrap_or(&payload))
    }

    /// Remove a file or directory from the detached volume.
    pub async fn remove(&self, path: impl AsRef<str>) -> Result<bool> {
        self.control
            .delete(&format!(
                "/volumes/{}/path?{}",
                self.volume_id,
                query(&[("path", Some(path.as_ref().to_string()))])
            ))
            .await?;
        Ok(true)
    }

    /// Destroy this volume.
    pub async fn destroy(&self) -> Result<bool> {
        match self
            .control
            .delete(&format!("/volumes/{}", self.volume_id))
            .await
        {
            Ok(_) => Ok(true),
            Err(Error::NotFound(_)) => Ok(false),
            Err(error) => Err(error),
        }
    }
}

fn volume_from_payload(
    payload: Value,
    _config: ConnectionConfig,
    control: ControlClient,
) -> Result<Volume> {
    let info = volume_info(payload.get("volume").unwrap_or(&payload))?;
    Ok(Volume {
        volume_id: info.volume_id,
        name: info.name,
        token: info.token,
        control,
    })
}

fn volume_info(payload: &Value) -> Result<VolumeInfo> {
    let id = payload
        .get("volume_id")
        .or_else(|| payload.get("id"))
        .and_then(Value::as_i64)
        .map(|id| id.to_string())
        .or_else(|| {
            payload
                .get("id")
                .and_then(Value::as_str)
                .map(ToOwned::to_owned)
        })
        .ok_or_else(|| Error::InvalidArgument("volume response did not include id".to_string()))?;

    Ok(VolumeInfo {
        volume_id: id.clone(),
        name: string(payload, "name").unwrap_or(id),
        state: string(payload, "state"),
        token: string(payload, "token"),
        size_mb: payload.get("size_mb").and_then(Value::as_u64),
        size_bytes: payload.get("size_bytes").and_then(Value::as_u64),
        node: string(payload, "node").or_else(|| string(payload, "node_name")),
        metadata: payload
            .get("metadata")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default(),
        created_at: string(payload, "created_at"),
        updated_at: string(payload, "updated_at"),
        raw: payload.clone(),
    })
}

fn volume_entry(payload: &Value) -> Result<VolumeEntryStat> {
    Ok(VolumeEntryStat {
        path: string(payload, "path").unwrap_or_default(),
        name: string(payload, "name").unwrap_or_default(),
        entry_type: string(payload, "type").unwrap_or_else(|| "file".to_string()),
        size: payload
            .get("size")
            .or_else(|| payload.get("bytes"))
            .and_then(Value::as_u64),
        mode: payload.get("mode").and_then(Value::as_u64),
        uid: payload.get("uid").and_then(Value::as_u64),
        gid: payload.get("gid").and_then(Value::as_u64),
        raw: payload.clone(),
    })
}

fn write_body(path: String, content_b64: Option<String>, opts: VolumeWriteOptions) -> Value {
    let mut body = serde_json::Map::new();
    body.insert("path".to_string(), Value::String(path));
    if let Some(content_b64) = content_b64 {
        body.insert("content_b64".to_string(), Value::String(content_b64));
    }
    if let Some(uid) = opts.uid {
        body.insert("uid".to_string(), Value::from(uid));
    }
    if let Some(gid) = opts.gid {
        body.insert("gid".to_string(), Value::from(gid));
    }
    if let Some(mode) = opts.mode {
        body.insert("mode".to_string(), Value::String(mode));
    }
    if let Some(force) = opts.force {
        body.insert("force".to_string(), Value::Bool(force));
    }
    Value::Object(body)
}

fn volume_list_path(opts: &VolumeListOptions) -> String {
    let query = query(&[("team", opts.team.clone())]);
    if query.is_empty() {
        "/volumes".to_string()
    } else {
        format!("/volumes?{query}")
    }
}

fn query(items: &[(&str, Option<String>)]) -> String {
    let mut serializer = url::form_urlencoded::Serializer::new(String::new());
    for (key, value) in items {
        if let Some(value) = value {
            serializer.append_pair(key, value);
        }
    }
    serializer.finish()
}

fn string(payload: &Value, key: &str) -> Option<String> {
    payload
        .get(key)
        .and_then(Value::as_str)
        .map(ToOwned::to_owned)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_volume_list_query_path() {
        assert_eq!(
            volume_list_path(&VolumeListOptions {
                team: Some("core".to_string()),
                ..VolumeListOptions::default()
            }),
            "/volumes?team=core"
        );
    }

    #[test]
    fn parses_volume_payloads() {
        let info = volume_info(&serde_json::json!({
            "id": 42,
            "name": "cache",
            "token": "wvol_secret",
            "size_mb": 10240,
            "metadata": {"purpose": "tests"}
        }))
        .unwrap();
        assert_eq!(info.volume_id, "42");
        assert_eq!(info.name, "cache");
        assert_eq!(info.token.as_deref(), Some("wvol_secret"));

        let entry = volume_entry(&serde_json::json!({
            "path": "/workspace/a.txt",
            "name": "a.txt",
            "type": "file",
            "bytes": 5
        }))
        .unwrap();
        assert_eq!(entry.path, "/workspace/a.txt");
        assert_eq!(entry.size, Some(5));
    }
}
