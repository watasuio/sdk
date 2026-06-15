# Watasu Rust SDK

Rust SDK for Watasu.

## Install

```toml
[dependencies]
watasu = "0.1.8"
```

Set `WATASU_API_KEY` before using the SDK.

## Usage

```rust
use watasu::{CreateOptions, Sandbox};

#[tokio::main]
async fn main() -> watasu::Result<()> {
    let sbx = Sandbox::create(CreateOptions::default()).await?;
    sbx.files.write("/home/user/a.py", "print(2 + 2)").await?;
    let result = sbx.commands.run("python /home/user/a.py").await?;
    println!("{}", result.stdout);
    sbx.kill().await?;
    Ok(())
}
```

`Sandbox::create` and `Sandbox::connect` return only after the Watasu API
supplies a usable data-plane session. The crate does not poll sandbox readiness.

## Git, Watch, PTY, And Signed File URLs

```rust
use watasu::{
    CreateOptions, GitAddOptions, GitCloneOptions, GitCommitOptions,
    GitConfigureUserOptions, GitRemoteOperationOptions, PtyCreateOptions,
    Sandbox, WatchOptions, WriteEntry,
};

# async fn run() -> watasu::Result<()> {
let sbx = Sandbox::create(CreateOptions::default()).await?;

sbx.git
    .clone(
        "https://github.com/acme/project.git",
        GitCloneOptions {
            path: Some("/workspace/project".into()),
            branch: Some("main".into()),
            depth: Some(1),
            ..Default::default()
        },
    )
    .await?;
let status = sbx.git.status("/workspace/project", Default::default()).await?;
sbx.git
    .configure_user(
        "Watasu Bot",
        "bot@watasu.local",
        GitConfigureUserOptions {
            scope: Some("local".into()),
            path: Some("/workspace/project".into()),
            ..Default::default()
        },
    )
    .await?;
sbx.git
    .create_branch("/workspace/project", "feature/docs", Default::default())
    .await?;
sbx.git
    .add(
        "/workspace/project",
        GitAddOptions {
            files: vec!["README.md".into()],
            ..Default::default()
        },
    )
    .await?;
sbx.git
    .commit(
        "/workspace/project",
        "Update docs",
        GitCommitOptions {
            author_name: Some("Watasu Bot".into()),
            author_email: Some("bot@watasu.local".into()),
            ..Default::default()
        },
    )
    .await?;
sbx.git
    .push(
        "/workspace/project",
        GitRemoteOperationOptions {
            remote: Some("origin".into()),
            branch: Some("feature/docs".into()),
            set_upstream: true,
            ..Default::default()
        },
    )
    .await?;

let mut watcher = sbx
    .files
    .watch_dir(
        "/workspace/project",
        WatchOptions {
            recursive: true,
            include_entry: true,
        },
    )
    .await?;
sbx.files
    .write_files(vec![
        WriteEntry::new("/workspace/project/a.txt", "alpha"),
        WriteEntry::new("/workspace/project/b.bin", [0, 1, 2]),
    ])
    .await?;

let mut terminal = sbx.pty.create(PtyCreateOptions::default()).await?;
terminal.send_stdin("echo hello\n").await?;

let upload_url = sbx.upload_url("/workspace/input.bin", Default::default()).await?;
let download_url = sbx.download_url("/workspace/output.bin", Default::default()).await?;

watcher.stop().await?;
terminal.kill().await?;
sbx.kill().await?;
# Ok(())
# }
```

## Metrics And Snapshots

```rust
use watasu::{CreateOptions, CreateSnapshotOptions, RestoreOptions, Sandbox};

# async fn run() -> watasu::Result<()> {
let sbx = Sandbox::create(CreateOptions::default()).await?;
let metrics = sbx.get_metrics().await?;
let snapshot = sbx
    .create_snapshot(CreateSnapshotOptions {
        name: Some("ready".into()),
        ..Default::default()
    })
    .await?;
let snapshots = sbx.list_snapshots().await?;
let snapshot_id = snapshot.snapshot_id.clone();
let restored = sbx
    .restore(RestoreOptions {
        checkpoint_id: snapshot_id.clone(),
        timeout_seconds: None,
    })
    .await?;
sbx.delete_snapshot(snapshot_id).await?;
sbx.kill().await?;
# Ok(())
# }
```

Watasu snapshots are backed by sandbox checkpoints. Use the returned
`snapshot_id` when restoring from a checkpoint.

## TLS Features

The crate is async and uses Tokio. It defaults to `rustls-tls`; use
`default-features = false` with `features = ["native-tls"]` or
`features = ["native-tls-vendored"]` if your deployment needs native OpenSSL
TLS instead.
