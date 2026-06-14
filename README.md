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

# fn main() -> watasu::Result<()> {
let sbx = Sandbox::create(CreateOptions::default())?;
sbx.files.write("/home/user/a.py", "print(2 + 2)")?;
let result = sbx.commands.run("python /home/user/a.py")?;
println!("{}", result.stdout);
sbx.kill()?;
# Ok(())
# }
```

Set `WATASU_API_KEY` before use. The `Sandbox`, `commands`, and `files` surfaces
are implemented over Watasu's control-plane REST API and data-plane
REST/WebSocket runtime. Unsupported surfaces raise clear not-implemented errors.
