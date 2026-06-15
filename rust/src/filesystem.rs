use serde_json::Value;

use crate::error::{Error, Result};
use crate::process_socket::ProcessSocket;
use crate::transport::DataPlaneClient;

/// File type returned by sandbox filesystem metadata.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum FileType {
    /// Regular file.
    File,
    /// Directory.
    Dir,
    /// Symbolic link.
    Symlink,
    /// Any file type not recognized by this SDK version.
    Other(String),
}

/// Metadata for one sandbox filesystem entry.
#[derive(Clone, Debug, Default)]
pub struct EntryInfo {
    /// Basename of the entry.
    pub name: String,
    /// Absolute sandbox path.
    pub path: String,
    /// File type, when returned by the runtime.
    pub file_type: Option<FileType>,
    /// Size in bytes, when known.
    pub size: Option<u64>,
    /// Runtime metadata map.
    pub metadata: serde_json::Map<String, Value>,
}

/// Metadata returned by write operations.
pub type WriteInfo = EntryInfo;

/// Filesystem event returned by a watch stream.
#[derive(Clone, Debug, Default)]
pub struct FilesystemEvent {
    /// Event type such as `create`, `write`, `remove`, or `rename`.
    pub event_type: String,
    /// Absolute sandbox path.
    pub path: String,
    /// Basename of the changed path.
    pub name: String,
    /// Entry metadata when the runtime can still stat the changed path.
    pub entry: Option<EntryInfo>,
    /// Full raw event payload.
    pub raw: Value,
}

/// Options for `Filesystem::watch_dir`.
#[derive(Clone, Debug, Default)]
pub struct WatchOptions {
    /// Whether to watch recursively.
    pub recursive: bool,
    /// Whether to include entry metadata when available.
    pub include_entry: bool,
}

/// Live filesystem watch stream.
pub struct WatchHandle {
    socket: ProcessSocket,
}

impl WatchHandle {
    /// Read the next batch of filesystem events.
    pub async fn next_events(&mut self) -> Result<Option<Vec<FilesystemEvent>>> {
        while let Some(frame) = self.socket.next_frame().await? {
            if frame.get("type").and_then(Value::as_str) != Some("events") {
                continue;
            }
            let events = frame
                .get("events")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default()
                .into_iter()
                .map(filesystem_event)
                .collect();
            return Ok(Some(events));
        }
        Ok(None)
    }

    /// Stop watching the directory.
    pub async fn stop(&mut self) -> Result<()> {
        self.socket.close().await
    }
}

/// Filesystem helper for a sandbox data-plane session.
#[derive(Clone)]
pub struct Filesystem {
    data_plane: DataPlaneClient,
}

impl Filesystem {
    pub(crate) fn new(data_plane: DataPlaneClient) -> Self {
        Self { data_plane }
    }

    /// Read a file as UTF-8 text.
    pub async fn read_text(&self, path: &str) -> Result<String> {
        let bytes = self.read_bytes(path).await?;
        Ok(String::from_utf8_lossy(&bytes).into_owned())
    }

    /// Read a file as raw bytes.
    pub async fn read_bytes(&self, path: &str) -> Result<Vec<u8>> {
        self.data_plane
            .get_bytes(&format!("/runtime/v1/files?path={}", urlencoding(path)))
            .await
    }

    /// Write bytes or text to a file.
    pub async fn write(&self, path: &str, data: impl AsRef<[u8]>) -> Result<WriteInfo> {
        let payload = self
            .data_plane
            .put_bytes(
                &format!("/runtime/v1/files?path={}", urlencoding(path)),
                data.as_ref().to_vec(),
            )
            .await?;
        Ok(entry_info(payload.get("file").unwrap_or(&payload)))
    }

    /// List directory entries below `path`.
    pub async fn list(&self, path: &str) -> Result<Vec<EntryInfo>> {
        let payload = self
            .data_plane
            .get_json(&format!(
                "/runtime/v1/directories?path={}",
                urlencoding(path)
            ))
            .await?;
        Ok(payload
            .get("entries")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .map(|value| entry_info(&value))
            .collect())
    }

    /// Return whether a file or directory exists at `path`.
    pub async fn exists(&self, path: &str) -> Result<bool> {
        match self.get_info(path).await {
            Ok(_) => Ok(true),
            Err(Error::FileNotFound(_)) | Err(Error::NotFound(_)) => Ok(false),
            Err(error) => Err(error),
        }
    }

    /// Return stat metadata for `path`.
    pub async fn get_info(&self, path: &str) -> Result<EntryInfo> {
        let payload = self
            .data_plane
            .get_json(&format!(
                "/runtime/v1/files/stat?path={}",
                urlencoding(path)
            ))
            .await?;
        Ok(entry_info(
            payload
                .get("file")
                .or_else(|| payload.get("entry"))
                .unwrap_or(&payload),
        ))
    }

    /// Remove a file at `path`.
    pub async fn remove(&self, path: &str) -> Result<()> {
        self.data_plane
            .delete_json(&format!("/runtime/v1/files?path={}", urlencoding(path)))
            .await?;
        Ok(())
    }

    /// Move or rename a file.
    pub async fn rename(&self, old_path: &str, new_path: &str) -> Result<EntryInfo> {
        let payload = self
            .data_plane
            .post_json(
                "/runtime/v1/files/move",
                serde_json::json!({"from_path": old_path, "to_path": new_path}),
            )
            .await?;
        Ok(entry_info(payload.get("file").unwrap_or(&payload)))
    }

    /// Create a directory.
    pub async fn make_dir(&self, path: &str) -> Result<bool> {
        self.data_plane
            .post_json(
                &format!("/runtime/v1/directories?path={}", urlencoding(path)),
                serde_json::json!({}),
            )
            .await?;
        Ok(true)
    }

    /// Start watching a directory for filesystem events.
    pub async fn watch_dir(&self, path: &str, opts: WatchOptions) -> Result<WatchHandle> {
        let query = format!(
            "path={}&recursive={}&include_entry={}",
            urlencoding(path),
            opts.recursive,
            opts.include_entry
        );
        let socket = ProcessSocket::connect(
            &self.data_plane.base_url,
            &self.data_plane.token,
            &format!("/runtime/v1/files/watch?{query}"),
        )
        .await?;
        Ok(WatchHandle { socket })
    }
}

fn entry_info(value: &Value) -> EntryInfo {
    EntryInfo {
        name: value
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        path: value
            .get("path")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        file_type: value.get("type").and_then(Value::as_str).map(file_type),
        size: value
            .get("bytes")
            .or_else(|| value.get("size"))
            .and_then(Value::as_u64),
        metadata: value
            .get("metadata")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default(),
    }
}

fn file_type(value: &str) -> FileType {
    match value {
        "file" => FileType::File,
        "dir" | "directory" => FileType::Dir,
        "symlink" => FileType::Symlink,
        other => FileType::Other(other.to_string()),
    }
}

fn filesystem_event(value: Value) -> FilesystemEvent {
    let path = value
        .get("path")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let event_type = match value
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or("modify")
    {
        "delete" => "remove",
        "modify" => "write",
        other => other,
    }
    .to_string();
    let entry = value.get("file").map(entry_info);
    FilesystemEvent {
        name: path
            .trim_end_matches('/')
            .rsplit('/')
            .next()
            .unwrap_or_default()
            .to_string(),
        path,
        event_type,
        entry,
        raw: value,
    }
}

fn urlencoding(value: &str) -> String {
    url::form_urlencoded::byte_serialize(value.as_bytes()).collect()
}
