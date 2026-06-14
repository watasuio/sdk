# Watasu SDKs

Python, TypeScript, and Rust SDKs for Watasu sandboxes.

`Sandbox.create` and `Sandbox.connect` are single provider operations: the
control-plane API waits for the runtime lifecycle internally and returns success
only with a usable data-plane session. SDKs do not poll sandbox readiness.

## Python

```python
from watasu import Sandbox

sbx = Sandbox.create()
sbx.files.write("/home/user/a.py", "print(2+2)")
result = sbx.commands.run("python /home/user/a.py")
print(result.stdout)
sbx.kill()
```

## TypeScript

```ts
import { Sandbox } from '@watasu/sdk'

const sbx = await Sandbox.create()
await sbx.files.write('/home/user/a.js', 'console.log(2 + 2)')
const result = await sbx.commands.run('node /home/user/a.js')
console.log(result.stdout)
await sbx.kill()
```

## Rust

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

The Rust crate is async and uses Tokio. It defaults to `rustls-tls`; use
`default-features = false` with `features = ["native-tls"]` or
`features = ["native-tls-vendored"]` if your deployment needs native OpenSSL
TLS instead.

Set `WATASU_API_KEY` before use. The `Sandbox`, `commands`, and `files` surfaces
are implemented over Watasu's control-plane REST API and data-plane
REST/WebSocket runtime. Unsupported surfaces raise clear not-implemented errors.
