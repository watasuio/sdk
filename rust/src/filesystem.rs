use serde_json::Value;

use crate::error::{Error, Result};
use crate::process_socket::ProcessSocket;
use crate::transport::DataPlaneClient;
use base64::Engine;

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

/// File path and bytes used by `Filesystem::write_files`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WriteEntry {
    /// Absolute sandbox path to write.
    pub path: String,
    /// File content bytes.
    pub data: Vec<u8>,
}

impl WriteEntry {
    /// Create a file write entry.
    pub fn new(path: impl Into<String>, data: impl AsRef<[u8]>) -> Self {
        Self {
            path: path.into(),
            data: data.as_ref().to_vec(),
        }
    }
}

/// Options for bounded file reads.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct FileReadOptions {
    /// Maximum bytes to read before failing. When omitted, the full file is read.
    pub max_bytes: Option<usize>,
}

/// Options for applying a patch payload inside the sandbox.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ApplyDiffOptions {
    /// Optional working directory used to resolve relative patch paths.
    pub cwd: Option<String>,
}

/// Failed hunk metadata returned by `Filesystem::apply_diff`.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ApplyDiffFailedHunk {
    /// One-based hunk index within the failed file patch.
    pub index: usize,
    /// Original starting line from the failed hunk.
    pub old_start: usize,
}

/// One failed file patch returned by `Filesystem::apply_diff`.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ApplyDiffFailure {
    /// Path that failed to apply.
    pub path: String,
    /// Human-readable failure reason.
    pub error: String,
    /// Failed hunk details when available.
    pub failed_hunk: Option<ApplyDiffFailedHunk>,
}

/// Per-file patch summary returned by `Filesystem::apply_diff`.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ApplyDiffFileSummary {
    /// Target path.
    pub path: String,
    /// Source path for move/rename patches.
    pub source_path: Option<String>,
    /// Patch kind such as `created`, `updated`, or `deleted`.
    pub kind: String,
    /// Added line count.
    pub added: usize,
    /// Removed line count.
    pub removed: usize,
}

/// Aggregate patch summary returned by `Filesystem::apply_diff`.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ApplyDiffSummary {
    /// Number of file patches requested.
    pub requested: usize,
    /// Number of file patches applied.
    pub applied: usize,
    /// Number of file patches that failed.
    pub failed: usize,
}

/// Structured result returned by `Filesystem::apply_diff`.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ApplyDiffReport {
    /// Overall status: `applied`, `partial`, or `failed`.
    pub status: String,
    /// Number of parsed diff blocks.
    pub parsed_diff_blocks: usize,
    /// Number of parsed file patches.
    pub patches: usize,
    /// Per-file patch summaries.
    pub files: Vec<ApplyDiffFileSummary>,
    /// Aggregate patch summary.
    pub summary: ApplyDiffSummary,
    /// Paths successfully applied.
    pub applied: Vec<String>,
    /// Per-file failures.
    pub failed: Vec<ApplyDiffFailure>,
    /// Paths touched by the operation.
    pub touched: Vec<String>,
    /// Raw runtime response.
    pub raw: Value,
}

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
        self.read_bytes_with_options(path, FileReadOptions::default())
            .await
    }

    /// Read a file as raw bytes with an optional SDK-side byte cap.
    pub async fn read_bytes_with_options(
        &self,
        path: &str,
        opts: FileReadOptions,
    ) -> Result<Vec<u8>> {
        self.data_plane
            .get_bytes_with_limit(
                &format!("/runtime/v1/files?path={}", urlencoding(path)),
                opts.max_bytes,
            )
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

    /// Write several files in one runtime API call.
    pub async fn write_files(&self, files: Vec<WriteEntry>) -> Result<Vec<WriteInfo>> {
        if files.is_empty() {
            return Ok(vec![]);
        }
        let payload = self
            .data_plane
            .post_json("/runtime/v1/files/write_files", write_files_payload(&files))
            .await?;
        Ok(payload
            .get("files")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .map(|value| entry_info(&value))
            .collect())
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

    /// Apply a git-style unified diff or Codex apply_patch payload inside the sandbox.
    pub async fn apply_diff(&self, diff: &str, opts: ApplyDiffOptions) -> Result<ApplyDiffReport> {
        let payload = self
            .data_plane
            .post_json(
                "/runtime/v1/files/apply_diff",
                apply_diff_payload(diff, &opts),
            )
            .await?;
        Ok(apply_diff_report(payload))
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

fn apply_diff_report(value: Value) -> ApplyDiffReport {
    ApplyDiffReport {
        status: value
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        parsed_diff_blocks: usize_value(value.get("parsed_diff_blocks")),
        patches: usize_value(value.get("patches")),
        files: value
            .get("files")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .map(apply_diff_file_summary)
            .collect(),
        summary: apply_diff_summary(value.get("summary").unwrap_or(&Value::Null)),
        applied: string_vec(value.get("applied")),
        failed: value
            .get("failed")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .map(apply_diff_failure)
            .collect(),
        touched: string_vec(value.get("touched")),
        raw: value,
    }
}

fn apply_diff_summary(value: &Value) -> ApplyDiffSummary {
    ApplyDiffSummary {
        requested: usize_value(value.get("requested")),
        applied: usize_value(value.get("applied")),
        failed: usize_value(value.get("failed")),
    }
}

fn apply_diff_file_summary(value: Value) -> ApplyDiffFileSummary {
    ApplyDiffFileSummary {
        path: value
            .get("path")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        source_path: value
            .get("source_path")
            .and_then(Value::as_str)
            .map(str::to_string),
        kind: value
            .get("kind")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        added: usize_value(value.get("added")),
        removed: usize_value(value.get("removed")),
    }
}

fn apply_diff_failure(value: Value) -> ApplyDiffFailure {
    let failed_hunk = value.get("failed_hunk").and_then(|hunk| {
        hunk.as_object().map(|_| ApplyDiffFailedHunk {
            index: usize_value(hunk.get("index")),
            old_start: usize_value(hunk.get("old_start")),
        })
    });
    ApplyDiffFailure {
        path: value
            .get("path")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        error: value
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        failed_hunk,
    }
}

fn usize_value(value: Option<&Value>) -> usize {
    value.and_then(Value::as_u64).unwrap_or(0) as usize
}

fn string_vec(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .filter_map(|value| value.as_str().map(str::to_string))
        .collect()
}

fn urlencoding(value: &str) -> String {
    url::form_urlencoded::byte_serialize(value.as_bytes()).collect()
}

fn write_files_payload(files: &[WriteEntry]) -> Value {
    serde_json::json!({
        "files": files
            .iter()
            .map(|file| {
                serde_json::json!({
                    "path": file.path.as_str(),
                    "data_base64": base64::engine::general_purpose::STANDARD.encode(&file.data),
                })
            })
            .collect::<Vec<_>>()
    })
}

fn apply_diff_payload(diff: &str, opts: &ApplyDiffOptions) -> Value {
    let mut payload = serde_json::json!({ "diff": diff });
    if let Some(cwd) = opts.cwd.as_deref().filter(|cwd| !cwd.is_empty()) {
        payload["cwd"] = Value::String(cwd.to_string());
    }
    payload
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{apply_diff_payload, write_files_payload, ApplyDiffOptions, WriteEntry};

    #[test]
    fn write_files_payload_uses_snake_case_base64_entries() {
        assert_eq!(
            write_files_payload(&[
                WriteEntry::new("/tmp/a.txt", "abc"),
                WriteEntry::new("/tmp/b.bin", [0, 1, 2]),
            ]),
            json!({
                "files": [
                    {"path": "/tmp/a.txt", "data_base64": "YWJj"},
                    {"path": "/tmp/b.bin", "data_base64": "AAEC"}
                ]
            })
        );
    }

    #[test]
    fn apply_diff_payload_uses_snake_case_route_body() {
        assert_eq!(
            apply_diff_payload(
                "diff --git a/a.txt b/a.txt",
                &ApplyDiffOptions {
                    cwd: Some("/workspace/app".to_string()),
                }
            ),
            json!({
                "diff": "diff --git a/a.txt b/a.txt",
                "cwd": "/workspace/app"
            })
        );
    }
}
