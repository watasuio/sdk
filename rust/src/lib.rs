//! Rust SDK for Watasu.
//!
//! The crate exposes a small async API for creating sandboxes, running
//! commands, and reading/writing files. `Sandbox::create` and
//! `Sandbox::connect` return only after the Watasu API has supplied a usable
//! data-plane session, so callers do not need to poll sandbox readiness.
//!
//! ```no_run
//! use watasu::{CreateOptions, Sandbox};
//!
//! # async fn run() -> watasu::Result<()> {
//! let sandbox = Sandbox::create(CreateOptions::default()).await?;
//! sandbox.files.write("/home/user/a.py", "print(2 + 2)").await?;
//! let result = sandbox.commands.run("python /home/user/a.py").await?;
//! assert_eq!(result.stdout.trim(), "4");
//! sandbox.kill().await?;
//! # Ok(())
//! # }
//! ```

#![warn(missing_docs)]

#[cfg(all(
    any(feature = "rustls-tls", feature = "rustls-tls-native-roots"),
    any(feature = "native-tls", feature = "native-tls-vendored")
))]
compile_error!("enable either a rustls TLS feature or a native-tls feature, not both");

mod commands;
mod config;
mod error;
mod filesystem;
mod git;
mod process_socket;
mod pty;
mod sandbox;
mod template;
mod transport;
mod volume;

pub use commands::{
    CommandExit, CommandHandle, CommandOptions, CommandResult, Commands, ProcessInfo,
};
pub use config::{ConnectionConfig, ConnectionOptions, KEEPALIVE_PING_INTERVAL_SECS};
pub use error::{Error, Result};
pub use filesystem::{EntryInfo, FileType, Filesystem, WriteEntry, WriteInfo};
pub use filesystem::{FilesystemEvent, WatchHandle, WatchOptions};
pub use git::{
    Git, GitAddOptions, GitBranches, GitCloneOptions, GitCommandResult, GitCommitOptions,
    GitConfigOptions, GitConfigureUserOptions, GitCredentialOptions, GitDeleteBranchOptions,
    GitFileStatus, GitInitOptions, GitRemoteAddOptions, GitRemoteOperationOptions,
    GitRequestOptions, GitResetOptions, GitRestoreOptions, GitStatus,
};
pub use process_socket::{decode_runtime_data, encode_runtime_data, ProcessSocket};
pub use pty::{Pty, PtyCreateOptions, PtySize};
pub use sandbox::{
    CreateOptions, CreateSnapshotOptions, FileUrlInfo, FileUrlOptions, ListOptions,
    NetworkUpdateOptions, RestoreOptions, Sandbox, SandboxInfo, SandboxInfoLifecycle,
    SandboxLifecycle, SandboxListPage, SandboxListQuery, SandboxMetrics, SnapshotInfo,
    SnapshotListOptions, SnapshotListPage,
};
pub use template::{
    BuildInfo, BuildStatusReason, LogEntry, Template, TemplateBuildOptions, TemplateBuildStatus,
    TemplateBuildStatusOptions, TemplateBuildStatusResponse, TemplateBuilder, TemplateTag,
    TemplateTagInfo,
};
pub use volume::{
    Volume, VolumeCreateOptions, VolumeEntryStat, VolumeInfo, VolumeListOptions, VolumeWriteOptions,
};
