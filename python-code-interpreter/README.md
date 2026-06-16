# Watasu Code Interpreter Python SDK

Python package for Watasu code interpreter sandboxes.

## Install

```bash
pip install watasu-code-interpreter
```

Set `WATASU_API_KEY` before using the SDK.

## Usage

```python
from watasu_code_interpreter import Sandbox

with Sandbox.create() as sbx:
    context = sbx.create_code_context()
    execution = sbx.run_code(
        "print('hello')\n2 + 3",
        context=context,
        on_stdout=lambda message: print(message.line),
    )

    print(execution.text)
    sbx.remove_code_context(context)
```

`Sandbox.create()` starts the `code-interpreter` template by default. Code runs
in persistent contexts and returns structured `results`, `logs`, and `error`
fields for each execution.

The package installs `watasu` and exposes the `watasu_code_interpreter` import
path.
