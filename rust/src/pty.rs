use serde_json::Value;

use crate::commands::{CommandHandle, Commands};
use crate::error::{Error, Result};
use crate::process_socket::ProcessSocket;
use crate::transport::DataPlaneClient;

/// PTY terminal size.
#[derive(Clone, Copy, Debug)]
pub struct PtySize {
    /// Number of terminal columns.
    pub cols: u16,
    /// Number of terminal rows.
    pub rows: u16,
}

/// Options for creating a PTY.
#[derive(Clone, Debug)]
pub struct PtyCreateOptions {
    /// Terminal size.
    pub size: PtySize,
    /// Working directory.
    pub cwd: Option<String>,
    /// Environment variables.
    pub envs: serde_json::Map<String, Value>,
    /// Process timeout in milliseconds.
    pub timeout_ms: Option<u64>,
}

impl Default for PtyCreateOptions {
    fn default() -> Self {
        Self {
            size: PtySize { cols: 80, rows: 24 },
            cwd: None,
            envs: Default::default(),
            timeout_ms: Some(60_000),
        }
    }
}

/// PTY helper backed by the sandbox process WebSocket runtime.
#[derive(Clone)]
pub struct Pty {
    data_plane: DataPlaneClient,
}

impl Pty {
    pub(crate) fn new(data_plane: DataPlaneClient) -> Self {
        Self { data_plane }
    }

    /// Create an interactive shell PTY.
    pub async fn create(&self, opts: PtyCreateOptions) -> Result<CommandHandle> {
        let mut socket = ProcessSocket::connect(
            &self.data_plane.base_url,
            &self.data_plane.token,
            "/runtime/v1/process",
        )
        .await?;
        let mut envs = serde_json::Map::new();
        envs.insert("TERM".into(), Value::String("xterm-256color".into()));
        envs.insert("LANG".into(), Value::String("C.UTF-8".into()));
        envs.insert("LC_ALL".into(), Value::String("C.UTF-8".into()));
        envs.extend(opts.envs);
        socket
            .send_json(&serde_json::json!({
                "type": "start",
                "cmd": "/bin/bash",
                "args": ["-i", "-l"],
                "cwd": opts.cwd,
                "environment": envs.clone(),
                "envs": envs,
                "stdin": true,
                "pty": {"cols": opts.size.cols, "rows": opts.size.rows},
                "timeout_ms": opts.timeout_ms,
            }))
            .await?;
        let first = next_started(&mut socket).await?;
        let pid = frame_pid(&first)
            .ok_or_else(|| Error::Sandbox("PTY started frame did not include pid".into()))?;
        Ok(CommandHandle::new(
            pid,
            socket,
            Commands::new(self.data_plane.clone(), Default::default()),
        ))
    }

    /// Connect to a running PTY by process id.
    pub async fn connect(&self, pid: impl ToString) -> Result<CommandHandle> {
        let pid = pid.to_string();
        let mut socket = ProcessSocket::connect(
            &self.data_plane.base_url,
            &self.data_plane.token,
            &format!("/runtime/v1/process/{pid}/connect?since=0"),
        )
        .await?;
        let first = next_started(&mut socket).await?;
        let actual_pid = frame_pid(&first).unwrap_or(pid);
        Ok(CommandHandle::new(
            actual_pid,
            socket,
            Commands::new(self.data_plane.clone(), Default::default()),
        ))
    }

    /// Send input bytes to a PTY.
    pub async fn send_stdin(&self, pid: impl ToString, data: impl AsRef<[u8]>) -> Result<()> {
        let mut handle = self.connect(pid).await?;
        handle.send_stdin(data).await
    }

    /// Send input bytes to a PTY.
    pub async fn send_input(&self, pid: impl ToString, data: impl AsRef<[u8]>) -> Result<()> {
        self.send_stdin(pid, data).await
    }

    /// Resize a running PTY.
    pub async fn resize(&self, pid: impl ToString, size: PtySize) -> Result<()> {
        let mut handle = self.connect(pid).await?;
        handle.resize(size.cols, size.rows).await
    }

    /// Kill a running PTY.
    pub async fn kill(&self, pid: impl ToString) -> Result<bool> {
        self.data_plane
            .post_json(
                &format!("/runtime/v1/process/{}/signal", pid.to_string()),
                serde_json::json!({"signal": "SIGKILL"}),
            )
            .await?;
        Ok(true)
    }
}

async fn next_started(socket: &mut ProcessSocket) -> Result<Value> {
    while let Some(frame) = socket.next_frame().await? {
        if frame.get("type").and_then(Value::as_str) == Some("started") {
            return Ok(frame);
        }
    }
    Err(Error::Sandbox("PTY ended before started frame".into()))
}

fn frame_pid(frame: &Value) -> Option<String> {
    frame
        .get("pid")
        .or_else(|| frame.pointer("/process/pid"))
        .or_else(|| frame.pointer("/process/id"))
        .map(|value| {
            value
                .as_str()
                .map(ToOwned::to_owned)
                .unwrap_or_else(|| value.to_string())
        })
}
