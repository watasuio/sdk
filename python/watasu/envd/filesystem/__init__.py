"""Compatibility stub for runtime-internal filesystem modules."""

from .._unsupported import module_getattr


def __getattr__(name: str):
    module_getattr("filesystem", name)

