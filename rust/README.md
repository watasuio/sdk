# Watasu Rust SDK

Rust SDK for Watasu.

## Install

```toml
[dependencies]
watasu = "0.1.4"
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
let restored = sbx
    .restore(RestoreOptions {
        checkpoint_id: snapshot.snapshot_id,
        timeout_seconds: None,
    })
    .await?;
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
