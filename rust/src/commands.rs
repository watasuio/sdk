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

/// Options for running a typed sandbox process with bounded output capture.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ProcessRunOptions {
    /// Typed process start options.
    pub start: ProcessStartOptions,
    /// Maximum stdout bytes to store. When omitted, stdout is stored in full.
    pub max_stdout_bytes: Option<usize>,
    /// Maximum stderr bytes to store. When omitted, stderr is stored in full.
    pub max_stderr_bytes: Option<usize>,
    /// Maximum PTY bytes to store. When omitted, PTY output is stored in full.
    pub max_pty_bytes: Option<usize>,
}

/// Completed typed process output with byte-preserving captured output.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ProcessResult {
    /// Captured stdout bytes.
    pub stdout: Vec<u8>,
    /// Captured stderr bytes.
    pub stderr: Vec<u8>,
    /// Captured PTY output bytes.
    pub pty: Vec<u8>,
    /// Total stdout bytes observed before process exit.
    pub stdout_bytes: usize,
    /// Total stderr bytes observed before process exit.
    pub stderr_bytes: usize,
    /// Total PTY bytes observed before process exit.
    pub pty_bytes: usize,
    /// Whether stdout capture was truncated by `max_stdout_bytes`.
    pub stdout_truncated: bool,
    /// Whether stderr capture was truncated by `max_stderr_bytes`.
    pub stderr_truncated: bool,
    /// Whether PTY capture was truncated by `max_pty_bytes`.
    pub pty_truncated: bool,
    /// Process exit code.
    pub exit_code: i32,
    /// Runtime-provided error string, if any.
    pub error: Option<String>,
    /// Process id returned by the runtime.
    pub pid: Option<String>,
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

    /// Decode PTY output as UTF-8, replacing invalid byte sequences.
    pub fn pty_text_lossy(&self) -> String {
        String::from_utf8_lossy(&self.pty).into_owned()
    }

    fn command_result(&self) -> CommandResult {
        let mut stdout = self.stdout.clone();
        stdout.extend_from_slice(&self.pty);
        CommandResult {
            stdout: String::from_utf8_lossy(&stdout).into_owned(),
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
        self.run_process_with_options(ProcessRunOptions {
            start: opts,
            ..ProcessRunOptions::default()
        })
        .await
    }

    /// Run a typed process with bounded output capture and wait for it to exit.
    pub async fn run_process_with_options(&self, opts: ProcessRunOptions) -> Result<ProcessResult> {
        let max_stdout_bytes = opts.max_stdout_bytes;
        let max_stderr_bytes = opts.max_stderr_bytes;
        let max_pty_bytes = opts.max_pty_bytes;
        let mut handle = self.start_process(opts.start).await?;
        handle
            .wait_process_with_limits(OutputLimits {
                max_stdout_bytes,
                max_stderr_bytes,
                max_pty_bytes,
            })
            .await
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
    check: bool,
}

impl CommandHandle {
    pub(crate) fn new(pid: String, socket: ProcessSocket, commands: Commands, check: bool) -> Self {
        Self {
            pid,
            socket,
            commands,
            check,
        }
    }

    /// Wait until the process exits and return captured output.
    pub async fn wait(&mut self) -> Result<CommandResult> {
        let result = self
            .wait_process_with_check(OutputLimits::default(), true)
            .await?;
        Ok(result.command_result())
    }

    /// Wait until the process exits and return byte-preserving captured output.
    pub async fn wait_process(&mut self) -> Result<ProcessResult> {
        self.wait_process_with_limits(OutputLimits::default()).await
    }

    async fn wait_process_with_limits(&mut self, limits: OutputLimits) -> Result<ProcessResult> {
        self.wait_process_with_check(limits, self.check).await
    }

    async fn wait_process_with_check(
        &mut self,
        limits: OutputLimits,
        check: bool,
    ) -> Result<ProcessResult> {
        let mut capture = OutputCapture::new(limits);

        while let Some(frame) = self.socket.next_frame().await? {
            match frame.get("type").and_then(Value::as_str) {
                Some("started" | "ready" | "pong") => continue,
                Some("stdout") => capture.push_stdout(&frame),
                Some("stderr") => capture.push_stderr(&frame),
                Some("pty") => capture.push_pty(&frame),
                Some("exit") => {
                    let result = process_result(capture, &self.pid, &frame);
                    if check && result.exit_code != 0 {
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

#[derive(Clone, Copy, Debug, Default)]
struct OutputLimits {
    max_stdout_bytes: Option<usize>,
    max_stderr_bytes: Option<usize>,
    max_pty_bytes: Option<usize>,
}

#[derive(Clone, Debug)]
struct OutputCapture {
    stdout: Vec<u8>,
    stderr: Vec<u8>,
    pty: Vec<u8>,
    stdout_bytes: usize,
    stderr_bytes: usize,
    pty_bytes: usize,
    stdout_truncated: bool,
    stderr_truncated: bool,
    pty_truncated: bool,
    limits: OutputLimits,
}

impl OutputCapture {
    fn new(limits: OutputLimits) -> Self {
        Self {
            stdout: Vec::new(),
            stderr: Vec::new(),
            pty: Vec::new(),
            stdout_bytes: 0,
            stderr_bytes: 0,
            pty_bytes: 0,
            stdout_truncated: false,
            stderr_truncated: false,
            pty_truncated: false,
            limits,
        }
    }

    fn push_stdout(&mut self, frame: &Value) {
        append_capped(
            &mut self.stdout,
            &mut self.stdout_bytes,
            &mut self.stdout_truncated,
            self.limits.max_stdout_bytes,
            frame_data(frame),
        );
    }

    fn push_stderr(&mut self, frame: &Value) {
        append_capped(
            &mut self.stderr,
            &mut self.stderr_bytes,
            &mut self.stderr_truncated,
            self.limits.max_stderr_bytes,
            frame_data(frame),
        );
    }

    fn push_pty(&mut self, frame: &Value) {
        append_capped(
            &mut self.pty,
            &mut self.pty_bytes,
            &mut self.pty_truncated,
            self.limits.max_pty_bytes,
            frame_data(frame),
        );
    }
}

fn append_capped(
    target: &mut Vec<u8>,
    total_bytes: &mut usize,
    truncated: &mut bool,
    max_bytes: Option<usize>,
    bytes: Vec<u8>,
) {
    *total_bytes = total_bytes.saturating_add(bytes.len());

    match max_bytes {
        Some(max_bytes) => {
            if target.len() < max_bytes {
                let remaining = max_bytes - target.len();
                let to_copy = remaining.min(bytes.len());
                target.extend_from_slice(&bytes[..to_copy]);
            }
            if *total_bytes > max_bytes {
                *truncated = true;
            }
        }
        None => target.extend_from_slice(&bytes),
    }
}

fn frame_data(frame: &Value) -> Vec<u8> {
    decode_runtime_data_bytes(frame.get("data").and_then(Value::as_str).unwrap_or(""))
}

fn process_result(capture: OutputCapture, fallback_pid: &str, frame: &Value) -> ProcessResult {
    ProcessResult {
        stdout: capture.stdout,
        stderr: capture.stderr,
        pty: capture.pty,
        stdout_bytes: capture.stdout_bytes,
        stderr_bytes: capture.stderr_bytes,
        pty_bytes: capture.pty_bytes,
        stdout_truncated: capture.stdout_truncated,
        stderr_truncated: capture.stderr_truncated,
        pty_truncated: capture.pty_truncated,
        exit_code: frame
            .get("exit_code")
            .or_else(|| frame.get("exitCode"))
            .and_then(Value::as_i64)
            .unwrap_or(0) as i32,
        error: frame
            .get("error")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned),
        pid: frame_pid(frame).or_else(|| Some(fallback_pid.to_string())),
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
    use base64::engine::general_purpose::STANDARD as BASE64;
    use base64::Engine as _;
    use serde_json::json;

    use super::{
        append_capped, process_info, process_result, process_start_payload, OutputCapture,
        OutputLimits, ProcessStartOptions,
    };

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
        let mut capture = OutputCapture::new(OutputLimits::default());
        capture.stdout = vec![0, 159, 146, 150];
        capture.stdout_bytes = 4;
        capture.stderr = b"stderr".to_vec();
        capture.stderr_bytes = 6;

        let result = process_result(
            capture,
            "proc-fallback",
            &json!({"type": "exit", "pid": "proc-1", "exit_code": 7, "error": "boom"}),
        );

        assert_eq!(result.stdout, vec![0, 159, 146, 150]);
        assert_eq!(result.stderr, b"stderr");
        assert_eq!(result.stdout_bytes, 4);
        assert_eq!(result.stderr_bytes, 6);
        assert_eq!(result.exit_code, 7);
        assert_eq!(result.error.as_deref(), Some("boom"));
        assert_eq!(result.pid.as_deref(), Some("proc-1"));
        assert_ne!(result.command_result().stdout.as_bytes(), result.stdout);
    }

    #[test]
    fn capped_capture_stores_prefix_and_counts_total_bytes() {
        let mut capture = OutputCapture::new(OutputLimits {
            max_stdout_bytes: Some(4),
            max_stderr_bytes: Some(3),
            max_pty_bytes: Some(0),
        });

        capture.push_stdout(&json!({"type": "stdout", "data": BASE64.encode("abcdef")}));
        capture.push_stdout(&json!({"type": "stdout", "data": BASE64.encode("gh")}));
        capture.push_stderr(&json!({"type": "stderr", "data": BASE64.encode("wxyz")}));
        capture.push_pty(&json!({"type": "pty", "data": BASE64.encode("pty")}));

        let result = process_result(
            capture,
            "proc-fallback",
            &json!({"type": "exit", "exit_code": 0}),
        );

        assert_eq!(result.stdout, b"abcd");
        assert_eq!(result.stderr, b"wxy");
        assert_eq!(result.pty, b"");
        assert_eq!(result.stdout_bytes, 8);
        assert_eq!(result.stderr_bytes, 4);
        assert_eq!(result.pty_bytes, 3);
        assert!(result.stdout_truncated);
        assert!(result.stderr_truncated);
        assert!(result.pty_truncated);
        assert_eq!(result.pid.as_deref(), Some("proc-fallback"));
    }

    #[test]
    fn append_capped_marks_exact_limit_as_not_truncated() {
        let mut bytes = Vec::new();
        let mut total = 0;
        let mut truncated = false;

        append_capped(
            &mut bytes,
            &mut total,
            &mut truncated,
            Some(3),
            b"abc".to_vec(),
        );

        assert_eq!(bytes, b"abc");
        assert_eq!(total, 3);
        assert!(!truncated);
    }
}
