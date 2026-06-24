use std::collections::VecDeque;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::time::sleep;

use crate::error::{Error, Result};
use crate::process_socket::{decode_runtime_data_bytes, ProcessSocket};
use crate::transport::DataPlaneClient;

const STREAM_RECONNECT_ATTEMPTS: usize = 12;
const STREAM_RECONNECT_BASE_DELAY_MS: u64 = 250;
const STREAM_RECONNECT_MAX_DELAY_MS: u64 = 2_000;

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

/// Current status for one sandbox process.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ProcessStatus {
    /// Stable runtime process id.
    pub pid: String,
    /// Stable runtime process id, when returned separately by the data plane.
    pub id: Option<String>,
    /// Guest operating-system pid, when exposed by the runtime.
    pub os_pid: Option<u32>,
    /// Executable command.
    pub command: Option<String>,
    /// Command arguments.
    pub args: Vec<String>,
    /// Current working directory.
    pub cwd: Option<String>,
    /// Runtime user.
    pub user: Option<String>,
    /// Whether the process was started with a PTY.
    pub pty: Option<bool>,
    /// Runtime status such as `running`, `succeeded`, `failed`, `killed`, or `timed_out`.
    pub status: String,
    /// Runtime start timestamp.
    pub started_at: Option<String>,
    /// Runtime finish timestamp.
    pub finished_at: Option<String>,
    /// Process exit code, if finished.
    pub exit_code: Option<i32>,
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
    /// Optional caller-supplied stable process id.
    pub id: Option<String>,
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
            id: None,
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

/// Options for reading available process output without blocking.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ReadProcessOutputOptions {
    /// Output read mode. Defaults to forward cursor polling.
    pub mode: ProcessOutputReadMode,
    /// First cursor to return. When omitted, the runtime starts at cursor `0`.
    pub since: Option<u64>,
    /// Maximum output bytes to return across all events.
    pub limit_bytes: Option<usize>,
    /// Head byte budget for `ProcessOutputReadMode::HeadTail`.
    pub head_bytes: Option<usize>,
    /// Tail byte budget for `ProcessOutputReadMode::HeadTail`.
    pub tail_bytes: Option<usize>,
    /// Numerator for deriving head bytes from `limit_bytes` in head/tail mode.
    pub head_ratio_num: Option<usize>,
    /// Denominator for deriving head bytes from `limit_bytes` in head/tail mode.
    pub head_ratio_den: Option<usize>,
}

/// Nonblocking process-output read mode.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum ProcessOutputReadMode {
    /// Return events from `since` forward.
    #[default]
    Forward,
    /// Return the latest retained output up to `limit_bytes`.
    Tail,
    /// Return retained head and tail output, omitting the middle.
    HeadTail,
}

impl ProcessOutputReadMode {
    fn as_query_value(self) -> &'static str {
        match self {
            Self::Forward => "forward",
            Self::Tail => "tail",
            Self::HeadTail => "head_tail",
        }
    }
}

/// Completed-process output capture policy.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ProcessOutputCapturePolicy {
    /// Retain all output bytes.
    Full,
    /// Retain the first `max_bytes` bytes.
    Prefix {
        /// Maximum bytes to retain.
        max_bytes: usize,
    },
    /// Retain the last `max_bytes` bytes.
    Tail {
        /// Maximum bytes to retain.
        max_bytes: usize,
    },
    /// Retain `head_bytes` from the beginning and `tail_bytes` from the end.
    HeadTail {
        /// Bytes to retain from the beginning of output.
        head_bytes: usize,
        /// Bytes to retain from the end of output.
        tail_bytes: usize,
    },
}

impl Default for ProcessOutputCapturePolicy {
    fn default() -> Self {
        Self::Full
    }
}

impl ProcessOutputCapturePolicy {
    /// Retain the first `max_bytes` bytes.
    pub fn prefix(max_bytes: usize) -> Self {
        Self::Prefix { max_bytes }
    }

    /// Retain the last `max_bytes` bytes.
    pub fn tail(max_bytes: usize) -> Self {
        Self::Tail { max_bytes }
    }

    /// Retain `max_bytes` using the default 30% head and 70% tail split.
    pub fn head_tail(max_bytes: usize) -> Self {
        Self::head_tail_ratio(max_bytes, 3, 10)
    }

    /// Retain `max_bytes` using a caller-provided head ratio.
    pub fn head_tail_ratio(max_bytes: usize, head_ratio_num: usize, head_ratio_den: usize) -> Self {
        let denominator = head_ratio_den.max(1);
        let head_bytes = max_bytes.saturating_mul(head_ratio_num) / denominator;
        Self::HeadTail {
            head_bytes,
            tail_bytes: max_bytes.saturating_sub(head_bytes),
        }
    }

    /// Retain explicit head and tail byte budgets.
    pub fn head_tail_bytes(head_bytes: usize, tail_bytes: usize) -> Self {
        Self::HeadTail {
            head_bytes,
            tail_bytes,
        }
    }
}

/// Options for stopping a sandbox process.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StopProcessOptions {
    /// Signal name or number. Defaults to `TERM`.
    pub signal: Option<String>,
    /// Whether to signal the full process group. Defaults to true.
    pub kill_group: bool,
    /// Grace period before the runtime escalates to `KILL`, in milliseconds.
    pub grace_ms: Option<u64>,
}

impl Default for StopProcessOptions {
    fn default() -> Self {
        Self {
            signal: None,
            kill_group: true,
            grace_ms: None,
        }
    }
}

/// One byte-preserving output event from a sandbox process.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ProcessOutputEvent {
    /// Monotonic cursor for this event.
    pub cursor: u64,
    /// Event stream type, usually `stdout`, `stderr`, or `pty`.
    pub r#type: String,
    /// Event bytes decoded from the runtime base64 payload.
    pub data: Vec<u8>,
}

/// Per-stream byte totals observed by the runtime.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ProcessOutputStreamTotals {
    /// Total stdout bytes observed.
    pub stdout: usize,
    /// Total stderr bytes observed.
    pub stderr: usize,
    /// Total PTY bytes observed.
    pub pty: usize,
}

/// Nonblocking snapshot of available process output.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ProcessOutput {
    /// Stable runtime process id.
    pub pid: String,
    /// Current process status.
    pub status: String,
    /// Process exit code, if finished.
    pub exit_code: Option<i32>,
    /// Runtime finish timestamp.
    pub finished_at: Option<String>,
    /// Cursor to use for the next output poll.
    pub next_cursor: u64,
    /// Whether older events were evicted before the requested cursor.
    pub truncated_before_cursor: bool,
    /// Whether any output was evicted from runtime retention before this read.
    pub truncated_by_retention: bool,
    /// Whether this response omitted any observed output.
    pub truncated: bool,
    /// Total process output bytes observed by the runtime.
    pub total_bytes_observed: usize,
    /// Alias for `total_bytes_observed`.
    pub total_bytes: usize,
    /// Bytes retained in this response.
    pub retained_bytes: usize,
    /// Alias for `retained_bytes`.
    pub returned_bytes: usize,
    /// Observed bytes omitted from this response.
    pub omitted_bytes: usize,
    /// First retained output-event cursor, when known.
    pub first_retained_cursor: Option<u64>,
    /// Last retained output-event cursor, when known.
    pub last_retained_cursor: Option<u64>,
    /// Runtime-observed per-stream byte totals.
    pub stream_totals: ProcessOutputStreamTotals,
    /// Output events available in this snapshot.
    pub events: Vec<ProcessOutputEvent>,
}

/// Options for running a typed sandbox process with bounded output capture.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ProcessRunOptions {
    /// Typed process start options.
    pub start: ProcessStartOptions,
    /// Capture policy for combined chronological output.
    pub capture_policy: ProcessOutputCapturePolicy,
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
    /// Captured combined stdout/stderr/PTY bytes in chronological order.
    pub output: Vec<u8>,
    /// Captured combined output events in chronological order.
    pub output_events: Vec<ProcessOutputEvent>,
    /// Total combined output bytes observed before process exit.
    pub total_bytes: usize,
    /// Combined output bytes retained by `capture_policy`.
    pub retained_bytes: usize,
    /// Combined output bytes omitted by `capture_policy`.
    pub omitted_bytes: usize,
    /// Whether combined output was truncated by `capture_policy`.
    pub truncated: bool,
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
    /// Decode combined chronological output as UTF-8, replacing invalid byte sequences.
    pub fn output_text_lossy(&self) -> String {
        String::from_utf8_lossy(&self.output).into_owned()
    }

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
        self.stop_process(
            pid,
            StopProcessOptions {
                signal: Some("SIGKILL".to_string()),
                ..StopProcessOptions::default()
            },
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
        let capture_policy = opts.capture_policy;
        let mut handle = self.start_process(opts.start).await?;
        handle
            .wait_process_with_limits(OutputLimits {
                max_stdout_bytes,
                max_stderr_bytes,
                max_pty_bytes,
                capture_policy,
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
        self.connect_since(pid, 0).await
    }

    /// Reconnect to a live process stream by pid starting at a cursor.
    pub async fn connect_since(&self, pid: impl ToString, cursor: u64) -> Result<CommandHandle> {
        let pid = pid.to_string();
        let encoded_pid = path_component(&pid);
        let mut socket = ProcessSocket::connect(
            &self.data_plane.base_url,
            &self.data_plane.token,
            &format!("/runtime/v1/process/{encoded_pid}/connect?since={cursor}"),
        )
        .await?;
        let first = next_started(&mut socket).await?;
        let actual_pid = frame_pid(&first).unwrap_or(pid);
        Ok(CommandHandle::new(actual_pid, socket, self.clone(), false))
    }

    /// Look up current process status without attaching to the stream.
    pub async fn process(&self, pid: impl ToString) -> Result<ProcessStatus> {
        let pid = pid.to_string();
        let payload = self
            .data_plane
            .get_json(&format!("/runtime/v1/process/{}", path_component(&pid)))
            .await?;
        Ok(process_status(payload))
    }

    /// Read currently available output events without blocking.
    pub async fn read_process_output(
        &self,
        pid: impl ToString,
        opts: ReadProcessOutputOptions,
    ) -> Result<ProcessOutput> {
        let pid = pid.to_string();
        let mut path = format!("/runtime/v1/process/{}/output", path_component(&pid));
        let query = process_output_query(&opts);
        if !query.is_empty() {
            path.push('?');
            path.push_str(&query);
        }
        let payload = self.data_plane.get_json(&path).await?;
        Ok(process_output(payload))
    }

    /// Stop a process, optionally signalling the whole process group.
    pub async fn stop_process(
        &self,
        pid: impl ToString,
        opts: StopProcessOptions,
    ) -> Result<ProcessStatus> {
        let pid = pid.to_string();
        let mut path = format!("/runtime/v1/process/{}", path_component(&pid));
        let query = stop_process_query(&opts);
        if !query.is_empty() {
            path.push('?');
            path.push_str(&query);
        }
        let payload = self.data_plane.delete_json(&path).await?;
        Ok(process_status(payload))
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
        let mut next_cursor = 0;
        let mut reconnect_attempts = 0;

        loop {
            let frame = match self.socket.next_frame().await {
                Ok(Some(frame)) => {
                    reconnect_attempts = 0;
                    frame
                }
                Ok(None) => {
                    self.reconnect_stream(next_cursor, &mut reconnect_attempts)
                        .await?;
                    continue;
                }
                Err(error) if is_reconnectable_stream_error(&error) => {
                    self.reconnect_stream(next_cursor, &mut reconnect_attempts)
                        .await?;
                    continue;
                }
                Err(error) => return Err(error),
            };
            advance_cursor(&mut next_cursor, &frame);
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
    }

    async fn reconnect_stream(&mut self, cursor: u64, attempts: &mut usize) -> Result<()> {
        let mut last_error = None;
        while *attempts < STREAM_RECONNECT_ATTEMPTS {
            let _ = self.socket.close().await;
            if *attempts > 0 {
                sleep(reconnect_delay(*attempts)).await;
            }
            *attempts += 1;
            match self.commands.connect_since(self.pid.clone(), cursor).await {
                Ok(handle) => {
                    let CommandHandle { pid, socket, .. } = handle;
                    self.pid = pid;
                    self.socket = socket;
                    return Ok(());
                }
                Err(error) => last_error = Some(error),
            }
        }
        Err(last_error.unwrap_or_else(|| {
            Error::Sandbox("process websocket closed before exit and could not reconnect".into())
        }))
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
    if let Some(id) = &opts.id {
        payload.insert("id".into(), Value::String(id.clone()));
    }
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

fn process_output_query(opts: &ReadProcessOutputOptions) -> String {
    let mut serializer = url::form_urlencoded::Serializer::new(String::new());
    if opts.mode != ProcessOutputReadMode::Forward {
        serializer.append_pair("mode", opts.mode.as_query_value());
    }
    if let Some(since) = opts.since {
        serializer.append_pair("since", &since.to_string());
    }
    if let Some(limit_bytes) = opts.limit_bytes {
        serializer.append_pair("limit_bytes", &limit_bytes.to_string());
    }
    if let Some(head_bytes) = opts.head_bytes {
        serializer.append_pair("head_bytes", &head_bytes.to_string());
    }
    if let Some(tail_bytes) = opts.tail_bytes {
        serializer.append_pair("tail_bytes", &tail_bytes.to_string());
    }
    if let Some(head_ratio_num) = opts.head_ratio_num {
        serializer.append_pair("head_ratio_num", &head_ratio_num.to_string());
    }
    if let Some(head_ratio_den) = opts.head_ratio_den {
        serializer.append_pair("head_ratio_den", &head_ratio_den.to_string());
    }
    serializer.finish()
}

fn stop_process_query(opts: &StopProcessOptions) -> String {
    let mut serializer = url::form_urlencoded::Serializer::new(String::new());
    if let Some(signal) = &opts.signal {
        serializer.append_pair("signal", signal);
    }
    serializer.append_pair("kill_group", if opts.kill_group { "true" } else { "false" });
    if let Some(grace_ms) = opts.grace_ms {
        serializer.append_pair("grace_ms", &grace_ms.to_string());
    }
    serializer.finish()
}

fn path_component(value: &str) -> String {
    url::form_urlencoded::byte_serialize(value.as_bytes()).collect()
}

fn advance_cursor(next_cursor: &mut u64, frame: &Value) {
    if let Some(cursor) = frame.get("cursor").and_then(Value::as_u64) {
        *next_cursor = (*next_cursor).max(cursor.saturating_add(1));
    }
}

fn reconnect_delay(attempt: usize) -> Duration {
    let exponent = attempt.saturating_sub(1).min(8) as u32;
    Duration::from_millis(
        STREAM_RECONNECT_MAX_DELAY_MS
            .min(STREAM_RECONNECT_BASE_DELAY_MS.saturating_mul(2_u64.pow(exponent))),
    )
}

fn is_reconnectable_stream_error(error: &Error) -> bool {
    matches!(error, Error::WebSocket(_) | Error::Io(_))
}

#[derive(Clone, Debug, Default)]
struct OutputLimits {
    max_stdout_bytes: Option<usize>,
    max_stderr_bytes: Option<usize>,
    max_pty_bytes: Option<usize>,
    capture_policy: ProcessOutputCapturePolicy,
}

#[derive(Clone, Debug)]
struct OutputCapture {
    combined: PolicyCapture,
    stdout: PolicyCapture,
    stderr: PolicyCapture,
    pty: PolicyCapture,
}

impl OutputCapture {
    fn new(limits: OutputLimits) -> Self {
        let stdout_policy = stream_capture_policy(limits.max_stdout_bytes, &limits.capture_policy);
        let stderr_policy = stream_capture_policy(limits.max_stderr_bytes, &limits.capture_policy);
        let pty_policy = stream_capture_policy(limits.max_pty_bytes, &limits.capture_policy);
        Self {
            combined: PolicyCapture::new(limits.capture_policy),
            stdout: PolicyCapture::new(stdout_policy),
            stderr: PolicyCapture::new(stderr_policy),
            pty: PolicyCapture::new(pty_policy),
        }
    }

    fn push_stdout(&mut self, frame: &Value) {
        let bytes = frame_data(frame);
        let cursor = frame_cursor(frame);
        self.combined.push(cursor, "stdout", &bytes);
        self.stdout.push(cursor, "stdout", &bytes);
    }

    fn push_stderr(&mut self, frame: &Value) {
        let bytes = frame_data(frame);
        let cursor = frame_cursor(frame);
        self.combined.push(cursor, "stderr", &bytes);
        self.stderr.push(cursor, "stderr", &bytes);
    }

    fn push_pty(&mut self, frame: &Value) {
        let bytes = frame_data(frame);
        let cursor = frame_cursor(frame);
        self.combined.push(cursor, "pty", &bytes);
        self.pty.push(cursor, "pty", &bytes);
    }
}

fn stream_capture_policy(
    limit: Option<usize>,
    fallback: &ProcessOutputCapturePolicy,
) -> ProcessOutputCapturePolicy {
    limit
        .map(ProcessOutputCapturePolicy::prefix)
        .unwrap_or_else(|| fallback.clone())
}

#[derive(Clone, Debug)]
struct PolicyCapture {
    policy: ProcessOutputCapturePolicy,
    total_bytes: usize,
    segments: VecDeque<CapturedSegment>,
    head_segments: Vec<CapturedSegment>,
    tail_segments: VecDeque<CapturedSegment>,
}

#[derive(Clone, Debug)]
struct CapturedSegment {
    cursor: u64,
    stream: String,
    data: Vec<u8>,
    start: usize,
}

#[derive(Clone, Debug, Default)]
struct CapturedOutput {
    bytes: Vec<u8>,
    events: Vec<ProcessOutputEvent>,
    total_bytes: usize,
    retained_bytes: usize,
    omitted_bytes: usize,
    truncated: bool,
}

impl PolicyCapture {
    fn new(policy: ProcessOutputCapturePolicy) -> Self {
        Self {
            policy,
            total_bytes: 0,
            segments: VecDeque::new(),
            head_segments: Vec::new(),
            tail_segments: VecDeque::new(),
        }
    }

    fn push(&mut self, cursor: u64, stream: &str, bytes: &[u8]) {
        let start = self.total_bytes;
        self.total_bytes = self.total_bytes.saturating_add(bytes.len());

        match self.policy {
            ProcessOutputCapturePolicy::Full => {
                push_segment(&mut self.segments, cursor, stream, bytes, start);
            }
            ProcessOutputCapturePolicy::Prefix { max_bytes } => {
                push_prefix_segment(&mut self.segments, cursor, stream, bytes, start, max_bytes);
            }
            ProcessOutputCapturePolicy::Tail { max_bytes } => {
                push_segment(&mut self.segments, cursor, stream, bytes, start);
                trim_captured_front(&mut self.segments, max_bytes);
            }
            ProcessOutputCapturePolicy::HeadTail {
                head_bytes,
                tail_bytes,
            } => {
                push_prefix_segment(
                    &mut self.head_segments,
                    cursor,
                    stream,
                    bytes,
                    start,
                    head_bytes,
                );
                push_segment(&mut self.tail_segments, cursor, stream, bytes, start);
                trim_captured_front(&mut self.tail_segments, tail_bytes);
            }
        }
    }

    fn finish(self) -> CapturedOutput {
        let segments = match self.policy {
            ProcessOutputCapturePolicy::HeadTail { .. } => {
                let mut segments = self.head_segments;
                segments.extend(self.tail_segments);
                merge_captured_segments(segments)
            }
            _ => self.segments.into_iter().collect(),
        };

        let mut bytes = Vec::new();
        let mut events = Vec::new();
        for segment in segments {
            bytes.extend_from_slice(&segment.data);
            events.push(ProcessOutputEvent {
                cursor: segment.cursor,
                r#type: segment.stream,
                data: segment.data,
            });
        }

        let retained_bytes = bytes.len();
        let omitted_bytes = self.total_bytes.saturating_sub(retained_bytes);
        CapturedOutput {
            bytes,
            events,
            total_bytes: self.total_bytes,
            retained_bytes,
            omitted_bytes,
            truncated: omitted_bytes > 0,
        }
    }
}

fn push_segment<T>(segments: &mut T, cursor: u64, stream: &str, bytes: &[u8], start: usize)
where
    T: Extend<CapturedSegment>,
{
    if bytes.is_empty() {
        return;
    }
    segments.extend([CapturedSegment {
        cursor,
        stream: stream.to_string(),
        data: bytes.to_vec(),
        start,
    }]);
}

fn push_prefix_segment<T>(
    segments: &mut T,
    cursor: u64,
    stream: &str,
    bytes: &[u8],
    start: usize,
    max_bytes: usize,
) where
    T: Extend<CapturedSegment>,
{
    if bytes.is_empty() || start >= max_bytes {
        return;
    }
    let take = max_bytes.saturating_sub(start).min(bytes.len());
    if take == 0 {
        return;
    }
    segments.extend([CapturedSegment {
        cursor,
        stream: stream.to_string(),
        data: bytes[..take].to_vec(),
        start,
    }]);
}

fn trim_captured_front(segments: &mut VecDeque<CapturedSegment>, max_bytes: usize) {
    let mut retained = segments
        .iter()
        .map(|segment| segment.data.len())
        .sum::<usize>();

    while retained > max_bytes {
        let remove = retained.saturating_sub(max_bytes);
        let Some(front) = segments.front_mut() else {
            break;
        };

        if remove >= front.data.len() {
            let removed = front.data.len();
            segments.pop_front();
            retained = retained.saturating_sub(removed);
        } else {
            front.data.drain(..remove);
            front.start = front.start.saturating_add(remove);
            retained = retained.saturating_sub(remove);
        }
    }
}

fn merge_captured_segments(mut segments: Vec<CapturedSegment>) -> Vec<CapturedSegment> {
    segments.sort_by(|left, right| {
        left.start
            .cmp(&right.start)
            .then_with(|| left.cursor.cmp(&right.cursor))
    });

    let mut merged: Vec<CapturedSegment> = Vec::new();
    for mut segment in segments {
        if let Some(previous) = merged.last() {
            let previous_end = previous.start.saturating_add(previous.data.len());
            if segment.start < previous_end {
                let overlap = previous_end.saturating_sub(segment.start);
                if overlap >= segment.data.len() {
                    continue;
                }
                segment.data.drain(..overlap);
                segment.start = segment.start.saturating_add(overlap);
            }
        }

        if !segment.data.is_empty() {
            merged.push(segment);
        }
    }

    merged
}

#[cfg(test)]
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

fn frame_cursor(frame: &Value) -> u64 {
    frame
        .get("cursor")
        .and_then(Value::as_u64)
        .unwrap_or_default()
}

fn process_result(capture: OutputCapture, fallback_pid: &str, frame: &Value) -> ProcessResult {
    let combined = capture.combined.finish();
    let stdout = capture.stdout.finish();
    let stderr = capture.stderr.finish();
    let pty = capture.pty.finish();

    ProcessResult {
        output: combined.bytes,
        output_events: combined.events,
        total_bytes: combined.total_bytes,
        retained_bytes: combined.retained_bytes,
        omitted_bytes: combined.omitted_bytes,
        truncated: combined.truncated,
        stdout: stdout.bytes,
        stderr: stderr.bytes,
        pty: pty.bytes,
        stdout_bytes: stdout.total_bytes,
        stderr_bytes: stderr.total_bytes,
        pty_bytes: pty.total_bytes,
        stdout_truncated: stdout.truncated,
        stderr_truncated: stderr.truncated,
        pty_truncated: pty.truncated,
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

fn process_status(value: Value) -> ProcessStatus {
    let item = value.get("process").unwrap_or(&value);
    ProcessStatus {
        pid: process_list_pid(item).unwrap_or_default(),
        id: item
            .get("id")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned),
        os_pid: item
            .get("os_pid")
            .or_else(|| item.get("osPid"))
            .and_then(Value::as_u64)
            .and_then(|value| u32::try_from(value).ok()),
        command: item
            .get("command")
            .or_else(|| item.get("cmd"))
            .and_then(Value::as_str)
            .map(ToOwned::to_owned),
        args: string_array(item.get("args").or_else(|| item.get("arguments"))),
        cwd: item
            .get("cwd")
            .or_else(|| item.get("working_directory"))
            .and_then(Value::as_str)
            .map(ToOwned::to_owned),
        user: item
            .get("user")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned),
        pty: item.get("pty").and_then(Value::as_bool),
        status: item
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        started_at: item
            .get("started_at")
            .or_else(|| item.get("startedAt"))
            .and_then(Value::as_str)
            .map(ToOwned::to_owned),
        finished_at: item
            .get("finished_at")
            .or_else(|| item.get("finishedAt"))
            .and_then(Value::as_str)
            .map(ToOwned::to_owned),
        exit_code: item
            .get("exit_code")
            .or_else(|| item.get("exitCode"))
            .and_then(Value::as_i64)
            .and_then(|value| i32::try_from(value).ok()),
    }
}

fn process_output(value: Value) -> ProcessOutput {
    ProcessOutput {
        pid: value
            .get("pid")
            .or_else(|| value.get("id"))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        status: value
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        exit_code: value
            .get("exit_code")
            .or_else(|| value.get("exitCode"))
            .and_then(Value::as_i64)
            .and_then(|value| i32::try_from(value).ok()),
        finished_at: value
            .get("finished_at")
            .or_else(|| value.get("finishedAt"))
            .and_then(Value::as_str)
            .map(ToOwned::to_owned),
        next_cursor: value
            .get("next_cursor")
            .or_else(|| value.get("nextCursor"))
            .and_then(Value::as_u64)
            .unwrap_or_default(),
        truncated_before_cursor: value
            .get("truncated_before_cursor")
            .or_else(|| value.get("truncatedBeforeCursor"))
            .and_then(Value::as_bool)
            .unwrap_or(false),
        truncated_by_retention: value
            .get("truncated_by_retention")
            .or_else(|| value.get("truncatedByRetention"))
            .and_then(Value::as_bool)
            .unwrap_or(false),
        truncated: value
            .get("truncated")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        total_bytes_observed: usize_field(
            &value,
            &[
                "total_bytes_observed",
                "totalBytesObserved",
                "total_bytes",
                "totalBytes",
            ],
        ),
        total_bytes: usize_field(
            &value,
            &[
                "total_bytes",
                "totalBytes",
                "total_bytes_observed",
                "totalBytesObserved",
            ],
        ),
        retained_bytes: usize_field(
            &value,
            &[
                "retained_bytes",
                "retainedBytes",
                "returned_bytes",
                "returnedBytes",
            ],
        ),
        returned_bytes: usize_field(
            &value,
            &[
                "returned_bytes",
                "returnedBytes",
                "retained_bytes",
                "retainedBytes",
            ],
        ),
        omitted_bytes: usize_field(&value, &["omitted_bytes", "omittedBytes"]),
        first_retained_cursor: u64_field(&value, &["first_retained_cursor", "firstRetainedCursor"]),
        last_retained_cursor: u64_field(&value, &["last_retained_cursor", "lastRetainedCursor"]),
        stream_totals: process_output_stream_totals(
            value
                .get("stream_totals")
                .or_else(|| value.get("streamTotals")),
        ),
        events: value
            .get("events")
            .and_then(Value::as_array)
            .map(|items| items.iter().map(process_output_event).collect())
            .unwrap_or_default(),
    }
}

fn process_output_stream_totals(value: Option<&Value>) -> ProcessOutputStreamTotals {
    let Some(value) = value else {
        return ProcessOutputStreamTotals::default();
    };
    ProcessOutputStreamTotals {
        stdout: usize_field(value, &["stdout"]),
        stderr: usize_field(value, &["stderr"]),
        pty: usize_field(value, &["pty"]),
    }
}

fn process_output_event(value: &Value) -> ProcessOutputEvent {
    ProcessOutputEvent {
        cursor: value
            .get("cursor")
            .and_then(Value::as_u64)
            .unwrap_or_default(),
        r#type: value
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        data: decode_runtime_data_bytes(value.get("data").and_then(Value::as_str).unwrap_or("")),
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
        args: string_array(item.get("args")),
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

fn string_array(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .map(|item| item.as_str().unwrap_or_default().to_string())
                .collect()
        })
        .unwrap_or_default()
}

fn usize_field(value: &Value, keys: &[&str]) -> usize {
    keys.iter()
        .find_map(|key| value.get(*key))
        .and_then(Value::as_u64)
        .and_then(|value| usize::try_from(value).ok())
        .unwrap_or_default()
}

fn u64_field(value: &Value, keys: &[&str]) -> Option<u64> {
    keys.iter()
        .find_map(|key| value.get(*key))
        .and_then(Value::as_u64)
}

#[cfg(test)]
mod tests {
    use base64::engine::general_purpose::STANDARD as BASE64;
    use base64::Engine as _;
    use serde_json::json;

    use super::{
        advance_cursor, append_capped, process_info, process_output, process_output_query,
        process_result, process_start_payload, OutputCapture, OutputLimits,
        ProcessOutputCapturePolicy, ProcessOutputReadMode, ProcessStartOptions,
        ReadProcessOutputOptions,
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
                id: Some("proc-typed".to_string()),
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
                "id": "proc-typed",
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
    fn process_output_decodes_base64_events() {
        let output = process_output(json!({
            "pid": "proc-1",
            "status": "running",
            "exit_code": null,
            "finished_at": null,
            "next_cursor": 43,
            "truncated_before_cursor": false,
            "truncated_by_retention": true,
            "truncated": true,
            "total_bytes_observed": 12,
            "retained_bytes": 7,
            "omitted_bytes": 5,
            "first_retained_cursor": 41,
            "last_retained_cursor": 42,
            "stream_totals": {"stdout": 4, "stderr": 3, "pty": 0},
            "events": [
                {"cursor": 41, "type": "stdout", "data": BASE64.encode([0, 159, 146, 150])},
                {"cursor": 42, "type": "stderr", "data": BASE64.encode("err")}
            ]
        }));

        assert_eq!(output.pid, "proc-1");
        assert_eq!(output.status, "running");
        assert_eq!(output.next_cursor, 43);
        assert!(output.truncated_by_retention);
        assert!(output.truncated);
        assert_eq!(output.total_bytes_observed, 12);
        assert_eq!(output.total_bytes, 12);
        assert_eq!(output.retained_bytes, 7);
        assert_eq!(output.returned_bytes, 7);
        assert_eq!(output.omitted_bytes, 5);
        assert_eq!(output.first_retained_cursor, Some(41));
        assert_eq!(output.last_retained_cursor, Some(42));
        assert_eq!(output.stream_totals.stdout, 4);
        assert_eq!(output.events[0].r#type, "stdout");
        assert_eq!(output.events[0].data, vec![0, 159, 146, 150]);
        assert_eq!(output.events[1].r#type, "stderr");
        assert_eq!(output.events[1].data, b"err");
    }

    #[test]
    fn process_output_query_serializes_read_modes() {
        let query = process_output_query(&ReadProcessOutputOptions {
            mode: ProcessOutputReadMode::HeadTail,
            since: Some(7),
            limit_bytes: Some(96),
            head_bytes: None,
            tail_bytes: None,
            head_ratio_num: Some(3),
            head_ratio_den: Some(10),
        });

        assert_eq!(
            query,
            "mode=head_tail&since=7&limit_bytes=96&head_ratio_num=3&head_ratio_den=10"
        );

        let query = process_output_query(&ReadProcessOutputOptions {
            mode: ProcessOutputReadMode::Tail,
            since: None,
            limit_bytes: Some(64),
            head_bytes: None,
            tail_bytes: None,
            head_ratio_num: None,
            head_ratio_den: None,
        });

        assert_eq!(query, "mode=tail&limit_bytes=64");
    }

    #[test]
    fn advance_cursor_uses_next_undelivered_event() {
        let mut next_cursor = 0;

        advance_cursor(&mut next_cursor, &json!({"cursor": 3, "type": "stdout"}));
        advance_cursor(&mut next_cursor, &json!({"cursor": 1, "type": "stdout"}));
        advance_cursor(&mut next_cursor, &json!({"type": "started"}));

        assert_eq!(next_cursor, 4);
    }

    #[test]
    fn process_result_preserves_bytes() {
        let mut capture = OutputCapture::new(OutputLimits::default());
        capture.push_stdout(
            &json!({"cursor": 1, "type": "stdout", "data": BASE64.encode([0, 159, 146, 150])}),
        );
        capture
            .push_stderr(&json!({"cursor": 2, "type": "stderr", "data": BASE64.encode("stderr")}));

        let result = process_result(
            capture,
            "proc-fallback",
            &json!({"type": "exit", "pid": "proc-1", "exit_code": 7, "error": "boom"}),
        );

        assert_eq!(
            result.output,
            [vec![0, 159, 146, 150], b"stderr".to_vec()].concat()
        );
        assert_eq!(result.output_events.len(), 2);
        assert_eq!(result.output_events[0].cursor, 1);
        assert_eq!(result.output_events[1].cursor, 2);
        assert_eq!(result.total_bytes, 10);
        assert_eq!(result.retained_bytes, 10);
        assert_eq!(result.omitted_bytes, 0);
        assert!(!result.truncated);
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
            capture_policy: ProcessOutputCapturePolicy::Full,
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
    fn head_tail_capture_retains_start_tail_and_omitted_bytes() {
        let mut capture = OutputCapture::new(OutputLimits {
            capture_policy: ProcessOutputCapturePolicy::head_tail(96),
            ..OutputLimits::default()
        });

        let mut bytes = b"START\n".to_vec();
        bytes.extend(vec![b'x'; 1024 * 1024 + 17]);
        bytes.extend_from_slice(b"\nDONE_TAIL\n");
        capture.push_stdout(&json!({"cursor": 1, "type": "stdout", "data": BASE64.encode(bytes)}));

        let result = process_result(capture, "proc-1", &json!({"type": "exit", "exit_code": 0}));
        let text = result.output_text_lossy();
        assert!(text.contains("START"), "{text:?}");
        assert!(text.contains("DONE_TAIL"), "{text:?}");
        assert_eq!(result.retained_bytes, 96);
        assert_eq!(result.output.len(), 96);
        assert!(result.truncated);
        assert!(result.omitted_bytes > 0);
        assert_eq!(result.output_events.len(), 2);
    }

    #[test]
    fn tail_capture_retains_latest_bytes() {
        let mut capture = OutputCapture::new(OutputLimits {
            capture_policy: ProcessOutputCapturePolicy::tail(16),
            ..OutputLimits::default()
        });

        capture
            .push_stdout(&json!({"cursor": 1, "type": "stdout", "data": BASE64.encode("START")}));
        capture.push_stdout(
            &json!({"cursor": 2, "type": "stdout", "data": BASE64.encode("0123456789")}),
        );
        capture.push_stdout(
            &json!({"cursor": 3, "type": "stdout", "data": BASE64.encode("DONE_TAIL")}),
        );

        let result = process_result(capture, "proc-1", &json!({"type": "exit", "exit_code": 0}));
        assert_eq!(result.output_text_lossy(), "3456789DONE_TAIL");
        assert_eq!(result.retained_bytes, 16);
        assert!(result.truncated);
    }

    #[test]
    fn mixed_stream_capture_preserves_chronological_order() {
        let mut capture = OutputCapture::new(OutputLimits::default());
        capture.push_stdout(&json!({"cursor": 1, "type": "stdout", "data": BASE64.encode("out1")}));
        capture.push_stderr(&json!({"cursor": 2, "type": "stderr", "data": BASE64.encode("err2")}));
        capture.push_stdout(&json!({"cursor": 3, "type": "stdout", "data": BASE64.encode("out3")}));

        let result = process_result(capture, "proc-1", &json!({"type": "exit", "exit_code": 0}));
        assert_eq!(result.output, b"out1err2out3".to_vec());
        assert_eq!(
            result
                .output_events
                .iter()
                .map(|event| event.r#type.as_str())
                .collect::<Vec<_>>(),
            vec!["stdout", "stderr", "stdout"]
        );
    }

    #[test]
    fn head_tail_text_decoding_is_lossy_for_split_utf8() {
        let mut capture = OutputCapture::new(OutputLimits {
            capture_policy: ProcessOutputCapturePolicy::head_tail_bytes(3, 3),
            ..OutputLimits::default()
        });

        capture.push_stdout(
            &json!({"cursor": 1, "type": "stdout", "data": BASE64.encode("😀middle😀")}),
        );

        let result = process_result(capture, "proc-1", &json!({"type": "exit", "exit_code": 0}));
        assert!(String::from_utf8(result.output.clone()).is_err());
        let text = result.output_text_lossy();
        assert!(!text.is_empty());
        assert!(text.is_char_boundary(text.len()));
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
