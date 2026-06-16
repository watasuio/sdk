# Watasu Rust SDK

Rust SDK for Watasu.

## Install

```toml
[dependencies]
watasu = "0.1.42"
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

```rust
# async fn run(mut sbx: watasu::Sandbox) -> watasu::Result<()> {
sbx.beta_pause().await?;
sbx.resume().await?;
# Ok(())
# }
```

Choose what happens when the sandbox timeout expires:

```rust
use watasu::{CreateOptions, Sandbox, SandboxLifecycle};

# async fn run() -> watasu::Result<()> {
let sbx = Sandbox::create(CreateOptions {
    lifecycle: Some(SandboxLifecycle::pause(true)),
    ..Default::default()
})
.await?;
# Ok(())
# }
```

`SandboxLifecycle::kill()` is the default behavior. `SandboxLifecycle::pause`
keeps the retained disk after timeout; passing `true` allows a later data-plane
request to resume that paused sandbox automatically.

Mount a named persistent volume when the sandbox starts:

```rust
use watasu::{CreateOptions, Sandbox, VolumeMount};

# async fn run() -> watasu::Result<()> {
let sbx = Sandbox::create(CreateOptions {
    volume_mounts: vec![
        VolumeMount::new("/workspace/cache", "cache"),
        VolumeMount::new("/data/models", "models"),
    ],
    ..Default::default()
})
.await?;
# Ok(())
# }
```

Create and edit a persistent volume while it is detached:

```rust
use watasu::{Volume, VolumeCreateOptions, VolumeWriteOptions};

# async fn run() -> watasu::Result<()> {
let volume = Volume::create("cache", VolumeCreateOptions::default()).await?;
volume.make_dir("/workspace", VolumeWriteOptions::default()).await?;
volume
    .write_file(
        "/workspace/status.txt",
        "ready\n",
        VolumeWriteOptions {
            mode: Some("0644".into()),
            ..Default::default()
        },
    )
    .await?;
let content = volume.read_file("/workspace/status.txt").await?;
println!("{}", String::from_utf8_lossy(&content));
volume.remove("/workspace/status.txt").await?;
volume.destroy().await?;
# Ok(())
# }
```

## Listing Sandboxes

```rust
use watasu::{ListOptions, Sandbox, SandboxListQuery};

# async fn run() -> watasu::Result<()> {
let mut metadata = serde_json::Map::new();
metadata.insert("purpose".into(), serde_json::Value::String("ci".into()));

let page = Sandbox::list(ListOptions {
    query: Some(SandboxListQuery {
        metadata,
        state: vec!["running".into()],
    }),
    limit: Some(20),
    ..Default::default()
})
.await?;

for sandbox in page.sandboxes {
    println!("{} {:?}", sandbox.sandbox_id, sandbox.state);
}
# Ok(())
# }
```

## Git, Watch, PTY, And Signed File URLs

```rust
use watasu::{
    CreateOptions, GitAddOptions, GitCloneOptions, GitCommitOptions,
    GitConfigureUserOptions, GitInitOptions, GitRemoteOperationOptions, GitResetOptions,
    GitRestoreOptions, PtyCreateOptions, Sandbox, WatchOptions, WriteEntry,
};

# async fn run() -> watasu::Result<()> {
let sbx = Sandbox::create(CreateOptions::default()).await?;

sbx.git
    .init(
        "/workspace/new-project",
        GitInitOptions {
            initial_branch: Some("main".into()),
            ..Default::default()
        },
    )
    .await?;
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
let remote_url = sbx
    .git
    .remote_get("/workspace/project", "origin", Default::default())
    .await?;
sbx.git
    .restore(
        "/workspace/project",
        GitRestoreOptions {
            paths: vec!["README.md".into()],
            ..Default::default()
        },
    )
    .await?;
sbx.git
    .reset(
        "/workspace/project",
        GitResetOptions {
            mode: Some("hard".into()),
            target: Some("HEAD".into()),
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

## Network Policy

```rust
use watasu::{NetworkUpdateOptions, Sandbox};

# async fn run(mut sbx: Sandbox) -> watasu::Result<()> {
sbx.update_network(NetworkUpdateOptions {
    allow_internet_access: Some(false),
    allow_package_registry_access: Some(true),
    allow_out: vec!["pypi.org:443".into(), "registry.npmjs.org:443".into()],
    deny_out: vec!["169.254.169.254".into()],
    ..Default::default()
})
.await?;
# Ok(())
# }
```

## Template Builds

```rust
use watasu::{Template, TemplateBuildOptions, TemplateBuilder};

# async fn run() -> watasu::Result<()> {
let template = TemplateBuilder::new()
    .from_python_image("3.12")
    .apt_install(["git"])
    .pip_install(["pytest"])
    .run_cmd("echo ready");

let build = Template::build_in_background(
    template,
    "python-ci:stable",
    TemplateBuildOptions {
        tags: vec!["stable".into()],
        cpu_count: Some(2),
        memory_mb: Some(2048),
        ..Default::default()
    },
)
.await?;

let status = Template::get_build_status(&build, Default::default()).await?;
Template::assign_tags(
    "python-ci:stable",
    vec!["prod".into()],
    Default::default(),
)
.await?;
# Ok(())
# }
```

Template names resolve server-side. `python-ci` starts the latest ready build;
`python-ci:stable` starts the tagged build.

## Metrics And Snapshots

```rust
use watasu::{
    CreateOptions, CreateSnapshotOptions, RestoreOptions, Sandbox, SnapshotListOptions,
};

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
let snapshot_page = Sandbox::list_snapshots_page(SnapshotListOptions {
    limit: Some(100),
    ..Default::default()
})
.await?;
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
