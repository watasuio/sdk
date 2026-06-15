# Watasu Rust SDK

Rust SDK for Watasu.

## Install

```toml
[dependencies]
watasu = "0.1.3"
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

## TLS Features

The crate is async and uses Tokio. It defaults to `rustls-tls`; use
`default-features = false` with `features = ["native-tls"]` or
`features = ["native-tls-vendored"]` if your deployment needs native OpenSSL
TLS instead.
