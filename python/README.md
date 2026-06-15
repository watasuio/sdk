# Watasu Python SDK

Python SDK for Watasu.

## Install

```bash
pip install watasu
```

Set `WATASU_API_KEY` before using the SDK.

## Usage

```python
from watasu import Sandbox

sbx = Sandbox()
sbx.files.write("/home/user/a.py", "print(2 + 2)")
result = sbx.commands.run("python /home/user/a.py")
print(result.stdout)
sbx.kill()
```

`Sandbox()`, `Sandbox.create`, and `Sandbox.connect` return only after the Watasu
API supplies a usable data-plane session. The SDK does not poll sandbox
readiness.

```python
from watasu import Sandbox

with Sandbox.create() as sbx:
    result = sbx.commands.run("echo hello")
    print(result.stdout)
```

Leaving the context manager calls `kill()`.

## Async API

```python
from watasu import AsyncSandbox


async def main() -> None:
    sbx = await AsyncSandbox.create()
    await sbx.kill()
```

Unsupported compatibility surfaces raise explicit not-implemented errors instead
of silently falling back to client-side polling.
