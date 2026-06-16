"""Compatibility stub for runtime-internal version helpers."""

from ._unsupported import module_getattr


def __getattr__(name: str):
    module_getattr("versions", name)

