//! Watasu Rust SDK.
//!
//! The crate exposes a small synchronous API for creating sandboxes, running
//! commands, and reading/writing files. `Sandbox::create` and
//! `Sandbox::connect` return only after the Watasu API has supplied a usable
//! data-plane session, so callers do not need to poll sandbox readiness.
//!
//! ```no_run
//! use watasu::{CreateOptions, Sandbox};
//!
//! # fn main() -> watasu::Result<()> {
//! let sandbox = Sandbox::create(CreateOptions::default())?;
//! sandbox.files.write("/home/user/a.py", "print(2 + 2)")?;
//! let result = sandbox.commands.run("python /home/user/a.py")?;
//! assert_eq!(result.stdout.trim(), "4");
//! sandbox.kill()?;
//! # Ok(())
//! # }
//! ```

#![warn(missing_docs)]

mod commands;
mod config;
mod error;
mod filesystem;
mod process_socket;
mod sandbox;
mod transport;

pub use commands::{
    CommandExit, CommandHandle, CommandOptions, CommandResult, Commands, ProcessInfo,
};
pub use config::{ConnectionConfig, ConnectionOptions, KEEPALIVE_PING_INTERVAL_SECS};
pub use error::{Error, Result};
pub use filesystem::{EntryInfo, FileType, Filesystem, WriteInfo};
pub use process_socket::{decode_runtime_data, encode_runtime_data, ProcessSocket};
pub use sandbox::{CreateOptions, Sandbox, SandboxInfo};
