use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::error::{Error, Result};
use crate::process_socket::{decode_runtime_data_bytes, ProcessSocket};
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

/// Options for starting a typed sandbox process.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProcessStartOptions {
    /// Executable path or command name.
    pub cmd: String,
    /// Command arguments.
    pub args: Vec<String>,
    /// Current working directory.
    pub cwd: Option<String>,
    /// Environment variables for this process. These are merged with sandbox-level envs.
    pub envs: serde_json::Map<String, Value>,
    /// Optional runtime tag used for listing and reconnecting processes.
    pub tag: Option<String>,
    /// Whether the process should keep stdin open.
    pub stdin: bool,
    /// Process timeout in milliseconds. Defaults to 60 seconds when omitted.
    pub timeout_ms: Option<u64>,
    /// Return non-zero exits as `Error::CommandExit` when used with `run_process`
    /// or `CommandHandle::wait_process`.
    pub check: bool,
}

impl Default for ProcessStartOptions {
    fn default() -> Self {
        Self {
            cmd: String::new(),
            args: Vec::new(),
            cwd: None,
            envs: Default::default(),
            tag: None,
            stdin: false,
            timeout_ms: None,
            check: false,
        }
    }
}

/// Completed typed process output with byte-preserving stdout and stderr.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ProcessResult {
    /// Captured stdout bytes.
    pub stdout: Vec<u8>,
    /// Captured stderr bytes.
    pub stderr: Vec<u8>,
    /// Process exit code.
    pub exit_code: i32,
    /// Runtime-provided error string, if any.
    pub error: Option<String>,
}

impl ProcessResult {
    /// Decode stdout as UTF-8, replacing invalid byte sequences.
    pub fn stdout_text_lossy(&self) -> String {
        String::from_utf8_lossy(&self.stdout).into_owned()
    }

    /// Decode stderr as UTF-8, replacing invalid byte sequences.
    pub fn stderr_text_lossy(&self) -> String {
        String::from_utf8_lossy(&self.stderr).into_owned()
    }

    fn command_result(&self) -> CommandResult {
        CommandResult {
            stdout: self.stdout_text_lossy(),
            stderr: self.stderr_text_lossy(),
            exit_code: self.exit_code,
            error: self.error.clone(),
        }
    }
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

    /// Run a typed process and wait for it to exit.
    pub async fn run_process(&self, opts: ProcessStartOptions) -> Result<ProcessResult> {
        let mut handle = self.start_process(opts).await?;
        handle.wait_process().await
    }

    /// Start a typed process and return a live handle immediately.
    pub async fn start_process(&self, opts: ProcessStartOptions) -> Result<CommandHandle> {
        self.start_process_with_payload(
            process_start_payload(&self.sandbox_envs, &opts)?,
            opts.check,
        )
        .await
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
        Ok(CommandHandle::new(actual_pid, socket, self.clone(), false))
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
        let payload = process_start_payload(
            &serde_json::Map::new(),
            &ProcessStartOptions {
                cmd: "/bin/bash".to_string(),
                args: vec!["-l".to_string(), "-c".to_string(), cmd.to_string()],
                envs: self.sandbox_envs.clone(),
                stdin: opts.stdin,
                timeout_ms: opts.timeout_ms,
                check: true,
                ..ProcessStartOptions::default()
            },
        )?;
        self.start_process_with_payload(payload, true).await
    }

    async fn start_process_with_payload(
        &self,
        payload: Value,
        check: bool,
    ) -> Result<CommandHandle> {
        let mut socket = ProcessSocket::connect(
            &self.data_plane.base_url,
            &self.data_plane.token,
            "/runtime/v1/process",
        )
        .await?;
        socket.send_json(&payload).await?;
        let first = next_started(&mut socket).await?;
        let pid = frame_pid(&first)
            .ok_or_else(|| Error::Sandbox("process started frame did not include pid".into()))?;
        Ok(CommandHandle::new(pid, socket, self.clone(), check))
    }
}

/// Live handle for one sandbox process stream.
pub struct CommandHandle {
    /// Process id.
    pub pid: String,
    socket: ProcessSocket,
    commands: Commands,
    stdout: Vec<u8>,
    stderr: Vec<u8>,
    check: bool,
}

impl CommandHandle {
    pub(crate) fn new(pid: String, socket: ProcessSocket, commands: Commands, check: bool) -> Self {
        Self {
            pid,
            socket,
            commands,
            stdout: Vec::new(),
            stderr: Vec::new(),
            check,
        }
    }

    /// Wait until the process exits and return captured output.
    pub async fn wait(&mut self) -> Result<CommandResult> {
        while let Some(frame) = self.socket.next_frame().await? {
            match frame.get("type").and_then(Value::as_str) {
                Some("started" | "ready" | "pong") => continue,
                Some("stdout") => self.stdout.extend(decode_runtime_data_bytes(
                    frame.get("data").and_then(Value::as_str).unwrap_or(""),
                )),
                Some("stderr") => self.stderr.extend(decode_runtime_data_bytes(
                    frame.get("data").and_then(Value::as_str).unwrap_or(""),
                )),
                Some("pty") => self.stdout.extend(decode_runtime_data_bytes(
                    frame.get("data").and_then(Value::as_str).unwrap_or(""),
                )),
                Some("exit") => {
                    let result = process_result(&self.stdout, &self.stderr, &frame);
                    let command_result = result.command_result();
                    if result.exit_code != 0 {
                        let _ = self.socket.close().await;
                        return Err(Error::CommandExit {
                            result: command_result,
                        });
                    }
                    let _ = self.socket.close().await;
                    return Ok(command_result);
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

    /// Wait until the process exits and return byte-preserving captured output.
    pub async fn wait_process(&mut self) -> Result<ProcessResult> {
        while let Some(frame) = self.socket.next_frame().await? {
            match frame.get("type").and_then(Value::as_str) {
                Some("started" | "ready" | "pong") => continue,
                Some("stdout") => self.stdout.extend(decode_runtime_data_bytes(
                    frame.get("data").and_then(Value::as_str).unwrap_or(""),
                )),
                Some("stderr") => self.stderr.extend(decode_runtime_data_bytes(
                    frame.get("data").and_then(Value::as_str).unwrap_or(""),
                )),
                Some("pty") => self.stdout.extend(decode_runtime_data_bytes(
                    frame.get("data").and_then(Value::as_str).unwrap_or(""),
                )),
                Some("exit") => {
                    let result = process_result(&self.stdout, &self.stderr, &frame);
                    if self.check && result.exit_code != 0 {
                        let _ = self.socket.close().await;
                        return Err(Error::CommandExit {
                            result: result.command_result(),
                        });
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
        Err(Error::Sandbox("process ended without an exit event".into()))
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

fn process_start_payload(
    sandbox_envs: &serde_json::Map<String, Value>,
    opts: &ProcessStartOptions,
) -> Result<Value> {
    if opts.cmd.trim().is_empty() {
        return Err(Error::InvalidArgument("process cmd is required".into()));
    }

    let mut envs = sandbox_envs.clone();
    envs.extend(opts.envs.clone());

    let mut payload = serde_json::Map::new();
    payload.insert("type".into(), Value::String("start".into()));
    payload.insert("cmd".into(), Value::String(opts.cmd.clone()));
    payload.insert(
        "args".into(),
        Value::Array(opts.args.iter().cloned().map(Value::String).collect()),
    );
    payload.insert("environment".into(), Value::Object(envs.clone()));
    payload.insert("envs".into(), Value::Object(envs));
    payload.insert("stdin".into(), Value::Bool(opts.stdin));
    payload.insert(
        "timeout_ms".into(),
        Value::from(opts.timeout_ms.unwrap_or(60_000)),
    );
    if let Some(cwd) = &opts.cwd {
        payload.insert("cwd".into(), Value::String(cwd.clone()));
    }
    if let Some(tag) = &opts.tag {
        payload.insert("tag".into(), Value::String(tag.clone()));
    }

    Ok(Value::Object(payload))
}

fn process_result(stdout: &[u8], stderr: &[u8], frame: &Value) -> ProcessResult {
    ProcessResult {
        stdout: stdout.to_vec(),
        stderr: stderr.to_vec(),
        exit_code: frame
            .get("exit_code")
            .or_else(|| frame.get("exitCode"))
            .and_then(Value::as_i64)
            .unwrap_or(0) as i32,
        error: frame
            .get("error")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned),
    }
}

fn process_info(value: Value) -> ProcessInfo {
    let item = value.get("process").unwrap_or(&value);
    ProcessInfo {
        pid: process_list_pid(item).unwrap_or_default(),
        tag: item
            .get("tag")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned),
        cmd: item
            .get("cmd")
            .or_else(|| item.get("command"))
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

fn process_list_pid(item: &Value) -> Option<String> {
    item.get("id").or_else(|| item.get("pid")).map(|value| {
        value
            .as_str()
            .map(ToOwned::to_owned)
            .unwrap_or_else(|| value.to_string())
    })
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{process_info, process_result, process_start_payload, ProcessStartOptions};

    #[test]
    fn process_info_prefers_stable_process_id_over_os_pid() {
        let info = process_info(json!({
            "id": "proc-123",
            "pid": 456,
            "command": "bash",
            "args": ["-lc", "sleep 60"],
            "cwd": "/workspace"
        }));

        assert_eq!(info.pid, "proc-123");
        assert_eq!(info.cmd.as_deref(), Some("bash"));
        assert_eq!(info.args, vec!["-lc".to_string(), "sleep 60".to_string()]);
        assert_eq!(info.cwd.as_deref(), Some("/workspace"));
    }

    #[test]
    fn process_start_payload_maps_typed_fields() {
        let mut sandbox_envs = serde_json::Map::new();
        sandbox_envs.insert("BASE".to_string(), json!("sandbox"));
        sandbox_envs.insert("OVERRIDE".to_string(), json!("sandbox"));
        let mut envs = serde_json::Map::new();
        envs.insert("OVERRIDE".to_string(), json!("process"));
        envs.insert("TRACE".to_string(), json!("1"));

        let payload = process_start_payload(
            &sandbox_envs,
            &ProcessStartOptions {
                cmd: "python3".to_string(),
                args: vec!["script.py".to_string()],
                cwd: Some("/workspace".to_string()),
                envs,
                tag: Some("tool-call".to_string()),
                stdin: true,
                timeout_ms: Some(12_000),
                check: false,
            },
        )
        .expect("start payload");

        assert_eq!(
            payload,
            json!({
                "type": "start",
                "cmd": "python3",
                "args": ["script.py"],
                "cwd": "/workspace",
                "environment": {
                    "BASE": "sandbox",
                    "OVERRIDE": "process",
                    "TRACE": "1"
                },
                "envs": {
                    "BASE": "sandbox",
                    "OVERRIDE": "process",
                    "TRACE": "1"
                },
                "tag": "tool-call",
                "stdin": true,
                "timeout_ms": 12000
            })
        );
    }

    #[test]
    fn process_start_payload_rejects_empty_cmd() {
        let error = process_start_payload(&serde_json::Map::new(), &ProcessStartOptions::default())
            .unwrap_err();

        assert!(matches!(error, crate::Error::InvalidArgument(_)));
    }

    #[test]
    fn process_result_preserves_bytes() {
        let result = process_result(
            &[0, 159, 146, 150],
            b"stderr",
            &json!({"type": "exit", "exit_code": 7, "error": "boom"}),
        );

        assert_eq!(result.stdout, vec![0, 159, 146, 150]);
        assert_eq!(result.stderr, b"stderr");
        assert_eq!(result.exit_code, 7);
        assert_eq!(result.error.as_deref(), Some("boom"));
        assert_ne!(result.command_result().stdout.as_bytes(), result.stdout);
    }
}
