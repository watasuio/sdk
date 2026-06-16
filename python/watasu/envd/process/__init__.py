"""Compatibility stub for runtime-internal process modules."""

from .._unsupported import module_getattr


def __getattr__(name: str):
    module_getattr("process", name)

