use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::error::{Error, Result};
use crate::process_socket::{decode_runtime_data, ProcessSocket};
use crate::transport::DataPlaneClient;

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
/// Completed command output.
pub struct CommandResult {
    /// Captured stdout decoded as UTF-8.
    pub stdout: String,
    /// Captured stderr decoded as UTF-8.
    pub stderr: String,
    /// Process exit code.
    pub exit_code: i32,
    /// Runtime-provided error string, if any.
    pub error: Option<String>,
}

/// Non-zero command exit information.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CommandExit {
    /// Captured command result.
    pub result: CommandResult,
}

/// Metadata for one sandbox process.
#[derive(Clone, Debug, Default)]
pub struct ProcessInfo {
    /// Process id.
    pub pid: String,
    /// Optional process tag.
    pub tag: Option<String>,
    /// Executable command.
    pub cmd: Option<String>,
    /// Command arguments.
    pub args: Vec<String>,
    /// Process environment variables.
    pub envs: serde_json::Map<String, Value>,
    /// Current working directory.
    pub cwd: Option<String>,
}

/// Options for starting a sandbox command.
#[derive(Clone, Debug, Default)]
pub struct CommandOptions {
    /// Process timeout in milliseconds. Defaults to 60 seconds when omitted.
    pub timeout_ms: Option<u64>,
    /// Whether the process should keep stdin open.
    pub stdin: bool,
}

/// Command runner for a sandbox data-plane session.
#[derive(Clone)]
pub struct Commands {
    data_plane: DataPlaneClient,
    sandbox_envs: serde_json::Map<String, Value>,
}

impl Commands {
    pub(crate) fn new(
        data_plane: DataPlaneClient,
        sandbox_envs: serde_json::Map<String, Value>,
    ) -> Self {
        Self {
            data_plane,
            sandbox_envs,
        }
    }

    /// List processes currently known by the sandbox runtime.
    pub async fn list(&self) -> Result<Vec<ProcessInfo>> {
        let payload = self.data_plane.get_json("/runtime/v1/process").await?;
        Ok(payload
            .get("processes")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .map(process_info)
            .collect())
    }

    /// Send `SIGKILL` to a process by pid.
    pub async fn kill(&self, pid: impl ToString) -> Result<bool> {
        self.data_plane
            .post_json(
                &format!("/runtime/v1/process/{}/signal", pid.to_string()),
                serde_json::json!({"signal": "SIGKILL"}),
            )
            .await?;
        Ok(true)
    }

    /// Run a shell command and wait for it to exit.
    pub async fn run(&self, cmd: &str) -> Result<CommandResult> {
        self.run_with_options(cmd, CommandOptions::default()).await
    }

    /// Run a shell command with explicit command options and wait for it to exit.
    pub async fn run_with_options(&self, cmd: &str, opts: CommandOptions) -> Result<CommandResult> {
        let mut handle = self.start(cmd, opts).await?;
        handle.wait().await
    }

    /// Start a shell command and return a live handle immediately.
    pub async fn run_background(&self, cmd: &str) -> Result<CommandHandle> {
        self.run_background_with_options(cmd, CommandOptions::default())
            .await
    }

    /// Start a shell command with explicit options and return a live handle immediately.
    pub async fn run_background_with_options(
        &self,
        cmd: &str,
        opts: CommandOptions,
    ) -> Result<CommandHandle> {
        self.start(cmd, opts).await
    }

    /// Reconnect to a live process stream by pid.
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
        Ok(CommandHandle::new(actual_pid, socket, self.clone()))
    }

    /// Send stdin bytes to a live process by pid.
    pub async fn send_stdin(&self, pid: impl ToString, data: impl AsRef<[u8]>) -> Result<()> {
        let mut handle = self.connect(pid).await?;
        handle.send_stdin(data).await?;
        let _ = handle.disconnect().await;
        Ok(())
    }

    /// Close stdin for a live process by pid, signalling EOF.
    pub async fn close_stdin(&self, pid: impl ToString) -> Result<()> {
        let mut handle = self.connect(pid).await?;
        handle.close_stdin().await?;
        let _ = handle.disconnect().await;
        Ok(())
    }

    async fn start(&self, cmd: &str, opts: CommandOptions) -> Result<CommandHandle> {
        let mut socket = ProcessSocket::connect(
            &self.data_plane.base_url,
            &self.data_plane.token,
            "/runtime/v1/process",
        )
        .await?;
        socket
            .send_json(&serde_json::json!({
                "type": "start",
                "cmd": "/bin/bash",
                "args": ["-l", "-c", cmd],
                "environment": self.sandbox_envs,
                "envs": self.sandbox_envs,
                "stdin": opts.stdin,
                "timeout_ms": opts.timeout_ms.unwrap_or(60_000)
            }))
            .await?;
        let first = next_started(&mut socket).await?;
        let pid = frame_pid(&first)
            .ok_or_else(|| Error::Sandbox("process started frame did not include pid".into()))?;
        Ok(CommandHandle::new(pid, socket, self.clone()))
    }
}

/// Live handle for one sandbox process stream.
pub struct CommandHandle {
    /// Process id.
    pub pid: String,
    socket: ProcessSocket,
    commands: Commands,
    stdout: String,
    stderr: String,
}

impl CommandHandle {
    pub(crate) fn new(pid: String, socket: ProcessSocket, commands: Commands) -> Self {
        Self {
            pid,
            socket,
            commands,
            stdout: String::new(),
            stderr: String::new(),
        }
    }

    /// Wait until the process exits and return captured output.
    pub async fn wait(&mut self) -> Result<CommandResult> {
        while let Some(frame) = self.socket.next_frame().await? {
            match frame.get("type").and_then(Value::as_str) {
                Some("started" | "ready" | "pong") => continue,
                Some("stdout") => self.stdout.push_str(&decode_runtime_data(
                    frame.get("data").and_then(Value::as_str).unwrap_or(""),
                )),
                Some("stderr") => self.stderr.push_str(&decode_runtime_data(
                    frame.get("data").and_then(Value::as_str).unwrap_or(""),
                )),
                Some("pty") => self.stdout.push_str(&decode_runtime_data(
                    frame.get("data").and_then(Value::as_str).unwrap_or(""),
                )),
                Some("exit") => {
                    let result = CommandResult {
                        stdout: self.stdout.clone(),
                        stderr: self.stderr.clone(),
                        exit_code: frame
                            .get("exit_code")
                            .or_else(|| frame.get("exitCode"))
                            .and_then(Value::as_i64)
                            .unwrap_or(0) as i32,
                        error: frame
                            .get("error")
                            .and_then(Value::as_str)
                            .map(ToOwned::to_owned),
                    };
                    if result.exit_code != 0 {
                        let _ = self.socket.close().await;
                        return Err(Error::CommandExit { result });
                    }
                    let _ = self.socket.close().await;
                    return Ok(result);
                }
                Some("error") => {
                    let _ = self.socket.close().await;
                    return Err(Error::Sandbox(
                        frame
                            .get("message")
                            .or_else(|| frame.get("code"))
                            .and_then(Value::as_str)
                            .unwrap_or("process error")
                            .to_string(),
                    ));
                }
                _ => continue,
            }
        }
        Err(Error::Sandbox("Command ended without an exit event".into()))
    }

    /// Kill the process.
    pub async fn kill(&self) -> Result<bool> {
        self.commands.kill(&self.pid).await
    }

    /// Send stdin bytes to the process.
    pub async fn send_stdin(&mut self, data: impl AsRef<[u8]>) -> Result<()> {
        self.socket.send_stdin(data).await
    }

    /// Close stdin and signal EOF to the process.
    pub async fn close_stdin(&mut self) -> Result<()> {
        self.socket.close_stdin().await
    }

    /// Disconnect the local stream without killing the process.
    pub async fn disconnect(&mut self) -> Result<()> {
        self.socket.close().await
    }

    /// Resize the attached PTY stream.
    pub async fn resize(&mut self, cols: u16, rows: u16) -> Result<()> {
        self.socket
            .send_json(&serde_json::json!({"type": "resize", "cols": cols, "rows": rows}))
            .await
    }
}

async fn next_started(socket: &mut ProcessSocket) -> Result<Value> {
    while let Some(frame) = socket.next_frame().await? {
        if frame.get("type").and_then(Value::as_str) == Some("started") {
            return Ok(frame);
        }
    }
    Err(Error::Sandbox("process ended before started frame".into()))
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

fn process_info(value: Value) -> ProcessInfo {
    let item = value.get("process").unwrap_or(&value);
    ProcessInfo {
        pid: frame_pid(item).unwrap_or_default(),
        tag: item
            .get("tag")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned),
        cmd: item
            .get("cmd")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned),
        args: item
            .get("args")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .map(|item| item.as_str().unwrap_or_default().to_string())
                    .collect()
            })
            .unwrap_or_default(),
        envs: item
            .get("envs")
            .or_else(|| item.get("environment"))
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default(),
        cwd: item
            .get("cwd")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned),
    }
}
