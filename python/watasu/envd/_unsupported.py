"""Shared helpers for unsupported runtime-internal compatibility modules."""


def unsupported(name: str) -> None:
    """Raise the explicit error used by runtime-internal compatibility stubs."""

    raise NotImplementedError(
        f"watasu.envd.{name} is a compatibility namespace only; "
        "use the public Sandbox files, commands, pty, git, and code APIs instead"
    )


def module_getattr(module_name: str, attr_name: str) -> None:
    """Implement module-level ``__getattr__`` for unsupported internals."""

    unsupported(f"{module_name}.{attr_name}")

